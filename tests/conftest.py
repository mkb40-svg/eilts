"""Общие фикстуры: копия репозитория во временной папке, подставные GitHub и модель ИИ."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from ielts_agent.commands import Context
from ielts_agent.github import FakeGitHub
from ielts_agent.llm import LLM, Reply
from ielts_agent.repo import Repo

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 10)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    for rel in ("config.yml", "content", "agent/rubrics", "agent/prompts", ".github/ISSUE_TEMPLATE"):
        src = ROOT / rel
        dst = tmp_path / rel
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
    return Repo(tmp_path)


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FOUNDRY_API_KEY",
                 "AZURE_OPENAI_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def assessment(task=6.0, coherence=6.5, lexical=6.0, grammar=5.5, errors=None, confidence=0.85, injection=False):
    def crit(band):
        return {"band": band, "comment": f"Комментарий для {band}"}

    return {
        "criteria": {"task": crit(task), "coherence": crit(coherence), "lexical": crit(lexical), "grammar": crit(grammar)},
        "strengths": ["Чёткая позиция"],
        "errors": errors if errors is not None else [
            {"quote": "the society", "correction": "society", "category": "article", "explanation": "Абстрактное существительное."}
        ],
        "priorities": ["Артикли", "Развитие идей", "Связки"],
        "sample": {"original_quote": "Firstly", "improved": "To begin with, ..."},
        "summary": "Уровень около 6.",
        "confidence": confidence,
        "injection_detected": injection,
    }


class FakeBackend:
    """Подставная модель: отдаёт заранее заданные ответы и запоминает запросы."""

    name = "anthropic"

    def __init__(self):
        self.queue: list = []
        self.calls: list[dict] = []
        self.default = None

    def has_credentials(self) -> bool:
        return True

    def create(self, *, model, effort, system, user, schema, max_tokens):
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        item = self.queue.pop(0) if self.queue else self.default
        if callable(item):
            item = item(user)
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return Reply(text=text, model=model, input_tokens=1000, output_tokens=500)


@pytest.fixture
def fake_ai() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub("student/ielts-self-prep")


@pytest.fixture
def ctx(repo, gh, fake_ai):
    config = repo.config()
    llm = LLM(repo, config, backends={"anthropic": fake_ai})
    return Context(repo, gh, llm=llm, today=TODAY)


@pytest.fixture
def ctx_no_ai(repo, gh):
    return Context(repo, gh, llm=LLM(repo, repo.config(), backends={}), today=TODAY)


def issue_event(body: str, number: int = 7, labels=(), sender: str = "student") -> dict:
    return {
        "action": "opened",
        "repository": {"owner": {"login": "student"}},
        "sender": {"login": sender, "type": "User"},
        "issue": {"number": number, "body": body, "labels": [{"name": l} for l in labels]},
    }


def comment_event(issue_body: str, comment: str, number: int = 9, labels=(), sender: str = "student") -> dict:
    return {
        "action": "created",
        "repository": {"owner": {"login": "student"}},
        "sender": {"login": sender, "type": "User"},
        "issue": {"number": number, "body": issue_body, "labels": [{"name": l} for l in labels]},
        "comment": {"id": 555, "body": comment, "user": {"login": sender, "type": "User"}},
    }


def form_body(**sections: str) -> str:
    """Тело Issue в том виде, как его создаёт GitHub по форме: «### Заголовок» + значение."""
    return "\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items())


START_BODY = form_body(
    **{
        "Формат экзамена": "Academic",
        "Целевой балл (band)": "7.0",
        "Дата экзамена": "2027-03-15",
        "Часов в неделю на подготовку": "6",
        "Текущий уровень (если знаете)": "около 6.0",
        "Язык пояснений": "Русский",
        "Что даётся труднее всего (по ощущениям)": "_No response_",
        "Приватность": "- [X] Я понимаю, что в публичном репозитории мои работы и прогресс видны всем (под ником GitHub).",
    }
)

ESSAY = (
    "In recent years the society has debated whether private cars should be banned from city centres. "
    "Firstly, cars produce pollution and noise. " * 20
)
