"""Слой ИИ: оценка Writing, повторные оценки, цитаты, провайдеры, бюджет."""

import json

import pytest

from ielts_agent import writing
from ielts_agent.llm import LLM, LLMError, LLMUnavailable, OpenAICompatibleBackend
from ielts_agent.store import Store, now_iso
from conftest import ESSAY, assessment


def run_assess(repo, llm, previous=None):
    return writing.assess(
        llm, repo, repo.config(), task_type="task2", prompt_text="Topic", essay=ESSAY,
        minutes="40", recurring=["article"], previous_median=previous,
    )


def test_count_words():
    # It's · a · well-known · fact · 3 · cars · 2 · bikes
    assert writing.count_words("It's a well-known fact: 3 cars, 2 bikes.") == 8


def test_quotes_must_exist_in_essay():
    errors = [{"quote": "the  Society", "category": "article"}, {"quote": "invented words", "category": "other"}]
    kept, dropped = writing.validate_quotes(errors, ESSAY)
    assert len(kept) == 1 and dropped == 1


def test_single_run_when_confident(repo, fake_ai):
    fake_ai.default = assessment(confidence=0.9)
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    result = run_assess(repo, llm)
    assert result["runs"] == 1
    assert result["band"] == 6.0  # (6 + 6.5 + 6 + 5.5) / 4 = 6.0
    assert result["errors"][0]["quote"] == "the society"
    # Текст ученика уходит модели внутри тегов как данные
    assert "<response>" in fake_ai.calls[0]["user"] and "Word count (computed by code)" in fake_ai.calls[0]["user"]


def test_recheck_on_low_confidence_and_median(repo, fake_ai):
    fake_ai.queue = [
        assessment(task=7, coherence=7, lexical=7, grammar=7, confidence=0.3),
        assessment(task=5, coherence=5, lexical=5, grammar=5, confidence=0.7),
        assessment(task=6, coherence=6, lexical=6, grammar=6, confidence=0.7),
    ]
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    result = run_assess(repo, llm)
    assert result["runs"] == 3  # 7 и 5 расходятся > 0.5 → третья оценка
    assert result["band"] == 6.0  # медиана по каждому критерию


def test_recheck_on_jump_from_previous_median(repo, fake_ai):
    fake_ai.default = assessment(task=8, coherence=8, lexical=8, grammar=8, confidence=0.9)
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    assert run_assess(repo, llm, previous=6.0)["runs"] == 2


def test_no_key_means_unavailable(repo):
    llm = LLM(repo, repo.config())  # провайдеры из config.yml, ключей в окружении нет
    assert llm.unavailable_reason("examiner") == "no_key"
    with pytest.raises(LLMUnavailable):
        llm.text_call("coach", "s", "u")


def test_budget_limit_blocks_calls(repo, fake_ai):
    Store(repo).add_usage({"at": now_iso(), "cost_usd": 0.02})
    Store(repo).add_usage({"at": "2001-01-01T00:00:00Z", "cost_usd": 99})  # прошлые месяцы не считаются
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    llm.config["budget_usd_month"] = 0.01
    assert llm.unavailable_reason("examiner") == "budget"
    llm.config["budget_usd_month"] = 1
    assert llm.available("examiner")


def test_usage_and_cost_logged(repo, fake_ai):
    fake_ai.default = "ok"
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    assert llm.text_call("coach", "system", "user") == "ok"
    row = json.loads((repo.progress / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    # claude-opus-5-5: 1000 × $4 + 500 × $20 за миллион = $0.014
    assert row["cost_usd"] == pytest.approx(0.014)
    assert row["price_known"] is True and row["role"] == "coach"


def test_invalid_json_retried_once_then_error(repo, fake_ai):
    fake_ai.queue = ["not json", "still not json"]
    llm = LLM(repo, repo.config(), backends={"anthropic": fake_ai})
    with pytest.raises(LLMError):
        llm.json_call("examiner", "s", "u", {"type": "object"})
    assert len(fake_ai.calls) == 2


# --- OpenAI-совместимые провайдеры (Azure OpenAI, Grok, DeepSeek) ----------------------------
def test_openai_compatible_backend_json_mode(repo, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    sent = {}

    def transport(url, body, headers):
        sent.update(url=url, body=body, headers=headers)
        content = json.dumps({"answer": 42})
        return {"model": body["model"], "choices": [{"message": {"content": "```json\n" + content + "\n```"},
                                                      "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30}}

    backend = OpenAICompatibleBackend(
        "deepseek", {"base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"}, transport
    )
    config = repo.config()
    config["agent"]["roles"]["explainer"] = {"provider": "deepseek", "model": "some-model"}
    llm = LLM(repo, config, backends={"deepseek": backend})
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
    assert llm.json_call("explainer", "You explain.", "Q", schema) == {"answer": 42}
    assert sent["url"] == "https://api.deepseek.com/chat/completions"
    assert sent["headers"] == {"Authorization": "Bearer secret"}
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in sent["body"]["messages"][0]["content"]
    row = json.loads((repo.progress / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["provider"] == "deepseek" and row["price_known"] is False  # цены нет в pricing.yml


def test_azure_openai_uses_api_key_header_and_param(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    seen = {}

    def transport(url, body, headers):
        seen.update(body=body, headers=headers)
        return {"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}], "usage": {}}

    backend = OpenAICompatibleBackend("azure_openai", {
        "base_url": "https://res.openai.azure.com/openai/v1", "api_key_env": "AZURE_OPENAI_API_KEY",
        "auth_header": "api-key", "max_tokens_param": "max_completion_tokens"}, transport)
    with pytest.raises(Exception):
        backend.create(model="dep", effort="medium", system="s", user="u", schema=None, max_tokens=100)
    assert seen["headers"] == {"api-key": "k"}
    assert seen["body"]["max_completion_tokens"] == 100


def test_placeholder_base_url_is_not_configured(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    backend = OpenAICompatibleBackend("azure_openai", {
        "base_url": "https://<ресурс>.openai.azure.com/openai/v1", "api_key_env": "AZURE_OPENAI_API_KEY"})
    assert not backend.has_credentials()
