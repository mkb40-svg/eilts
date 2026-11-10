"""Разбор ошибок Reading (ИИ) и ответы наставника на вопросы."""

from __future__ import annotations

from typing import Any

from .content import Task, passage_text
from .llm import LLM, data_block, load_prompt
from .reading import ReadingResult
from .repo import Repo
from .writing import _norm


def explanation_schema(traps: list[str]) -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "number": {"type": "integer"},
            "why": {"type": "string"},
            "trap": {"type": "string", "enum": traps},
            "evidence": {"type": "string"},
        },
        "required": ["number", "why", "trap", "evidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"explanations": {"type": "array", "items": item}, "tip": {"type": "string"}},
        "required": ["explanations", "tip"],
        "additionalProperties": False,
    }


def explain_reading(
    llm: LLM, repo: Repo, config: dict, task: Task, key: dict, result: ReadingResult
) -> dict[str, Any]:
    traps = repo.rubric_yaml("error_categories.yml")["reading"]
    system = (
        load_prompt(repo, "explainer_reading.md")
        + "\n\n# Allowed trap values\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in traps.items())
    )
    wrong_lines = [
        f"Q{i.number} ({i.qtype}): candidate answered {i.given or '(blank)'!r}; "
        f"correct: {' / '.join(i.accepted)}; marking: {i.reason}"
        for i in result.wrong
    ]
    key_lines = [
        f"Q{n}: {' / '.join(map(str, spec['answer']))}" for n, spec in sorted(key["answers"].items())
    ]
    language = "Russian" if config["agent"].get("feedback_language", "ru") == "ru" else "English"
    user = "\n\n".join(
        [
            data_block("facts", f"Feedback language: {language}"),
            data_block("passage_and_questions", task.body),
            data_block("answer_key", "\n".join(key_lines)),
            data_block("wrong", "\n".join(wrong_lines)),
        ]
    )
    data = llm.json_call("explainer", system, user, explanation_schema(list(traps)))
    passage = _norm(passage_text(task))
    wrong_numbers = {i.number for i in result.wrong}
    explanations = []
    for item in data.get("explanations", []):
        if item.get("number") not in wrong_numbers:
            continue
        if item.get("evidence") and _norm(item["evidence"]) not in passage:
            item["evidence"] = ""  # цитаты нет в тексте — не показываем выдуманную
        explanations.append(item)
    return {"explanations": explanations, "tip": data.get("tip", ""), "model": llm.describe("explainer")}


def answer_question(llm: LLM, repo: Repo, config: dict, question: str, context: str = "") -> str:
    language = "Russian" if config["agent"].get("feedback_language", "ru") == "ru" else "English"
    parts = [data_block("facts", f"Feedback language: {language}")]
    if context.strip():
        parts.append(data_block("context", context[:6000]))
    parts.append(data_block("question", question[:4000]))
    return llm.text_call("coach", load_prompt(repo, "coach.md"), "\n\n".join(parts), max_tokens=3000)
