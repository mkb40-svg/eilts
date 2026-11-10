"""Вызовы ИИ: несколько провайдеров, роль → (провайдер, модель), учёт расходов и лимит бюджета.

Поддерживаются:
- anthropic          — Claude напрямую (секрет ANTHROPIC_API_KEY);
- anthropic_foundry  — Claude через Microsoft Foundry в Azure (секрет ANTHROPIC_FOUNDRY_API_KEY);
- openai_compatible  — любой API в формате OpenAI Chat Completions: Azure OpenAI, xAI (Grok), DeepSeek.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema

from .repo import Repo
from .store import Store, now_iso

# Серверный fallback при отказе модели — только на первом уровне Claude API (не в Foundry).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    """ИИ для роли не подключён (нет ключа) или исчерпан месячный бюджет."""


class LLMError(Exception):
    """Ответ модели нельзя использовать: отказ, обрезка, невалидный JSON."""


@dataclass
class Usage:
    provider: str
    model: str
    role: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    price_known: bool = True


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


class Backend(Protocol):
    name: str

    def has_credentials(self) -> bool: ...

    def create(self, *, model: str, effort: str, system: str, user: str, schema: dict | None,
               max_tokens: int) -> Reply: ...


# --- Claude ---------------------------------------------------------------------------
class AnthropicBackend:
    """Claude через официальный Anthropic SDK: напрямую или через Microsoft Foundry (Azure)."""

    def __init__(self, name: str, spec: dict, client: Any | None = None):
        self.name = name
        self.foundry = spec.get("type") == "anthropic_foundry"
        self.key_env = spec.get("api_key_env") or (
            "ANTHROPIC_FOUNDRY_API_KEY" if self.foundry else "ANTHROPIC_API_KEY"
        )
        self.resource = spec.get("resource") or os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE")
        self._client = client

    def has_credentials(self) -> bool:
        if self._client is not None:
            return True
        if self.foundry:
            return bool(os.environ.get(self.key_env) and self.resource)
        return bool(os.environ.get(self.key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def client(self):
        if self._client is None:
            import anthropic  # импорт здесь: без ключа SDK не нужен

            if self.foundry:
                self._client = anthropic.AnthropicFoundry(
                    api_key=os.environ[self.key_env], resource=self.resource, max_retries=3
                )
            else:
                self._client = anthropic.Anthropic(api_key=os.environ.get(self.key_env), max_retries=3)
        return self._client

    def create(self, *, model, effort, system, user, schema, max_tokens) -> Reply:
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            # Системный промпт одинаков между вызовами — кэшируется (prompt caching).
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        client = self.client()
        if model in FALLBACK_MODELS and not self.foundry:
            response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)

        usage = response.usage
        reply = Reply(
            text="".join(b.text for b in response.content if getattr(b, "type", "") == "text"),
            model=getattr(response, "model", None) or model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )
        if response.stop_reason == "refusal":
            raise _Unusable("refusal", reply)
        if response.stop_reason == "max_tokens":
            raise _Unusable("max_tokens", reply)
        return reply


# --- OpenAI-совместимые API: Azure (DeepSeek, Grok, GPT), xAI, DeepSeek ---------------
class ProviderHTTPError(LLMError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail[:300]}")
        self.status = status
        self.detail = detail


_SWAP_TOKENS_PARAM = {"max_tokens": "max_completion_tokens", "max_completion_tokens": "max_tokens"}


def _origin(url: str) -> str:
    """https://res.openai.azure.com/openai/deployments/... → https://res.openai.azure.com"""
    parts = urllib.parse.urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url.strip().rstrip("/")


class OpenAICompatibleBackend:
    """Chat Completions в формате OpenAI.

    api_style:
    - v1 (по умолчанию): POST {base_url}/chat/completions, модель в теле запроса (xAI, DeepSeek, Azure /openai/v1);
    - azure_deployments: POST {endpoint}/openai/deployments/{развёртывание}/chat/completions?api-version=...
      — классический формат Azure; адрес ресурса берётся из переменной endpoint_env, чтобы не хранить его в репозитории.
    """

    def __init__(self, name: str, spec: dict, transport: Any | None = None):
        self.name = name
        self.api_style = spec.get("api_style", "v1")
        self.base_url = str(spec.get("base_url") or "").rstrip("/")
        self.endpoint_env = spec.get("endpoint_env", "")
        self.api_version = str(spec.get("api_version") or "2025-01-01-preview")
        self.key_env = spec.get("api_key_env", "")
        self.auth_header = spec.get("auth_header", "Authorization")
        self.max_tokens_param = spec.get("max_tokens_param", "max_tokens")
        self.min_output_tokens = int(spec.get("min_output_tokens") or 0)
        self.send_reasoning_effort = bool(spec.get("reasoning_effort", False))
        self.json_mode = bool(spec.get("json_mode", True))
        self.timeout = int(spec.get("timeout_seconds") or 600)
        self.extra_body = dict(spec.get("extra_body") or {})
        self._transport = transport or _post_json

    def endpoint(self) -> str:
        url = os.environ.get(self.endpoint_env, "") if self.endpoint_env else ""
        url = url or self.base_url
        return "" if not url or "<" in url else url

    def has_credentials(self) -> bool:
        return bool(self.endpoint() and os.environ.get(self.key_env))

    def url_for(self, model: str) -> str:
        if self.api_style == "azure_deployments":
            deployment = urllib.parse.quote(model, safe="")
            return (f"{_origin(self.endpoint())}/openai/deployments/{deployment}/chat/completions"
                    f"?api-version={self.api_version}")
        return f"{self.endpoint().rstrip('/')}/chat/completions"

    def create(self, *, model, effort, system, user, schema, max_tokens) -> Reply:
        if schema is not None:
            system = (
                system
                + "\n\nReturn ONLY one JSON object that validates against this JSON Schema "
                "(no markdown, no commentary, no text before or after the JSON):\n"
                + json.dumps(schema, ensure_ascii=False)
            )
        body: dict[str, Any] = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self.max_tokens_param: max(max_tokens, self.min_output_tokens),
            **self.extra_body,
        }
        if self.api_style != "azure_deployments":
            body["model"] = model  # в формате deployments модель задаётся адресом
        if schema is not None and self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.send_reasoning_effort and effort:
            body["reasoning_effort"] = effort
        key = os.environ.get(self.key_env, "")
        headers = {"api-key": key} if self.auth_header.lower() == "api-key" else {"Authorization": f"Bearer {key}"}
        data = self._send(self.url_for(model), body, headers)

        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):  # некоторые API отдают содержимое частями
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        reply = Reply(
            text=strip_reasoning(str(content)),
            model=str(data.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0) - cached,
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_read=cached,
        )
        finish = choice.get("finish_reason")
        if finish == "length":
            raise _Unusable("max_tokens", reply)
        if finish == "content_filter":
            raise _Unusable("refusal", reply)
        return reply

    def _send(self, url: str, body: dict, headers: dict) -> dict:
        """Если модель не принимает необязательный параметр, убираем или меняем его и повторяем.

        Поправка запоминается, чтобы следующие вызовы сразу шли в рабочем виде.
        """
        for _ in range(4):
            try:
                return self._transport(url, body, headers, self.timeout)
            except ProviderHTTPError as exc:
                if exc.status != 400:
                    raise
                msg = exc.detail.lower()
                if "response_format" in body and "response_format" in msg:
                    body.pop("response_format")
                    self.json_mode = False
                elif "reasoning_effort" in body and "reasoning_effort" in msg:
                    body.pop("reasoning_effort")
                    self.send_reasoning_effort = False
                elif self.max_tokens_param in msg and self.max_tokens_param in body:
                    other = _SWAP_TOKENS_PARAM[self.max_tokens_param]
                    body[other] = body.pop(self.max_tokens_param)
                    self.max_tokens_param = other
                else:
                    raise
        raise LLMError("не удалось подобрать параметры запроса")


def _post_json(url: str, body: dict, headers: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ProviderHTTPError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"network: {exc.reason}") from exc


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Рассуждающие модели (DeepSeek) иногда кладут ход мыслей в <think>…</think> перед ответом."""
    text = _THINK.sub("", text)
    if "</think>" in text.lower():  # открывающий тег мог не прийти
        text = re.split(r"</think>", text, flags=re.I)[-1]
    return text.strip()


class _Unusable(Exception):
    def __init__(self, reason: str, reply: Reply):
        super().__init__(reason)
        self.reason = reason
        self.reply = reply


def make_backend(name: str, spec: dict) -> Backend:
    kind = spec.get("type", "anthropic")
    if kind in ("anthropic", "anthropic_foundry"):
        return AnthropicBackend(name, spec)
    if kind == "openai_compatible":
        return OpenAICompatibleBackend(name, spec)
    raise ValueError(f"Неизвестный тип провайдера ИИ: {kind}")


def find_price(pricing: dict, *names: str) -> dict | None:
    """Цена по имени модели из ответа или имени развёртывания, без учёта регистра."""
    lowered = {str(k).lower(): v for k, v in pricing.items()}
    for name in names:
        if name and str(name).lower() in lowered:
            return lowered[str(name).lower()]
    return None


def cost_of(pricing: dict, model: str, reply: Reply, fallback_model: str = "") -> tuple[float, bool]:
    price = find_price(pricing, model, fallback_model)
    if not price:
        return 0.0, False
    total = (
        reply.input_tokens * price["input"]
        + reply.output_tokens * price["output"]
        + reply.cache_read * price.get("cache_read", price["input"])
        + reply.cache_write * price.get("cache_write", price["input"])
    )
    return round(total / 1_000_000, 6), True


# --- фасад ----------------------------------------------------------------------------
class LLM:
    def __init__(self, repo: Repo, config: dict, backends: dict[str, Backend] | None = None):
        self.repo = repo
        self.config = config["agent"]
        self.store = Store(repo)
        self.pricing = repo.rubric_yaml("pricing.yml") or {}
        self.backends: dict[str, Backend] = backends if backends is not None else {
            name: make_backend(name, spec or {}) for name, spec in (self.config.get("providers") or {}).items()
        }

    def role(self, role: str) -> dict:
        spec = dict((self.config.get("roles") or {}).get(role) or {})
        spec.setdefault("provider", "azure")
        spec.setdefault("model", "DeepSeek-V4-Pro")
        spec.setdefault("effort", "medium")
        return spec

    def has_role(self, role: str) -> bool:
        return bool((self.config.get("roles") or {}).get(role))

    def backend_for(self, role: str) -> Backend | None:
        return self.backends.get(self.role(role)["provider"])

    def budget_left(self) -> float:
        return float(self.config.get("budget_usd_month", 0)) - self.store.month_spend()

    def unavailable_reason(self, role: str) -> str | None:
        backend = self.backend_for(role)
        if backend is None or not backend.has_credentials():
            return "no_key"
        if self.budget_left() <= 0:
            return "budget"
        return None

    def available(self, role: str) -> bool:
        return self.unavailable_reason(role) is None

    def describe(self, role: str) -> str:
        spec = self.role(role)
        return f"{spec['provider']} / {spec['model']}"

    def _call(self, role: str, system: str, user: str, schema: dict | None, max_tokens: int) -> Reply:
        reason = self.unavailable_reason(role)
        if reason:
            raise LLMUnavailable(reason)
        spec = self.role(role)
        backend = self.backend_for(role)
        try:
            reply = backend.create(model=spec["model"], effort=spec["effort"], system=system, user=user,
                                   schema=schema, max_tokens=max_tokens)
            failure = None
        except _Unusable as exc:
            reply, failure = exc.reply, exc.reason
        cost, known = cost_of(self.pricing, reply.model, reply, fallback_model=spec["model"])
        usage = Usage(backend.name, reply.model, role, reply.input_tokens, reply.output_tokens,
                      reply.cache_read, reply.cache_write, cost, known)
        self.store.add_usage({"at": now_iso(), **usage.__dict__})
        if failure:
            raise LLMError(failure)
        return reply

    def json_call(self, role: str, system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict:
        reply = self._call(role, system, user, schema, max_tokens)
        try:
            data = json.loads(_strip_fence(reply.text))
            jsonschema.validate(data, schema)
            return data
        except (json.JSONDecodeError, jsonschema.ValidationError) as first_error:
            # Одна повторная попытка с описанием ошибки (нужно не-Claude моделям без строгой схемы).
            retry_user = f"{user}\n\nYour previous answer was not valid: {str(first_error)[:300]}. Return valid JSON only."
            reply = self._call(role, system, retry_user, schema, max_tokens)
            try:
                data = json.loads(_strip_fence(reply.text))
                jsonschema.validate(data, schema)
                return data
            except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
                raise LLMError(f"invalid_json: {str(exc)[:200]}") from exc

    def text_call(self, role: str, system: str, user: str, max_tokens: int = 4000) -> str:
        return self._call(role, system, user, None, max_tokens).text.strip()


def _strip_fence(text: str) -> str:
    text = strip_reasoning(text)
    if not text.startswith(("{", "```")) and "{" in text and text.rstrip().endswith("}"):
        text = text[text.index("{"):]  # пояснение перед JSON, несмотря на просьбу
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def load_prompt(repo: Repo, name: str) -> str:
    return (repo.prompts / name).read_text(encoding="utf-8")


def data_block(tag: str, text: str) -> str:
    """Текст ученика передаётся как данные в тегах; закрывающий тег внутри экранируется."""
    safe = (text or "").replace(f"</{tag}>", f"</ {tag}>")
    return f"<{tag}>\n{safe}\n</{tag}>"
