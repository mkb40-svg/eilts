"""Writing: подсчёт слов (код) и оценка по четырём критериям (ИИ) с повторной оценкой и проверкой цитат."""

from __future__ import annotations

import re
from statistics import median
from typing import Any

from . import bands
from .llm import LLM, data_block, load_prompt
from .repo import Repo

CRITERIA = ("task", "coherence", "lexical", "grammar")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")

TASK_TYPES = {
    "task2": "Task 2 (essay)",
    "task1-academic": "Task 1 Academic (report on visual information)",
    "task1-general": "Task 1 General Training (letter)",
}
MIN_WORDS = {"task2": 250, "task1-academic": 150, "task1-general": 150}


def count_words(text: str) -> int:
    return len(_WORD.findall(text or ""))


def criterion_name(key: str, task_type: str) -> str:
    if key == "task":
        return "Task Response" if task_type == "task2" else "Task Achievement"
    return {
        "coherence": "Coherence and Cohesion",
        "lexical": "Lexical Resource",
        "grammar": "Grammatical Range and Accuracy",
    }[key]


def task_type_from_form(value: str | None) -> str:
    value = (value or "").lower()
    if "academic" in value:
        return "task1-academic"
    if "general" in value or "письмо" in value:
        return "task1-general"
    return "task2"


def assessment_schema(categories: list[str]) -> dict[str, Any]:
    def obj(props: dict, required: list[str] | None = None) -> dict:
        return {
            "type": "object",
            "properties": props,
            "required": required or list(props),
            "additionalProperties": False,
        }

    criterion = obj({"band": {"type": "number"}, "comment": {"type": "string"}})
    return obj(
        {
            "criteria": obj({key: criterion for key in CRITERIA}),
            "strengths": {"type": "array", "items": {"type": "string"}},
            "errors": {
                "type": "array",
                "items": obj(
                    {
                        "quote": {"type": "string"},
                        "correction": {"type": "string"},
                        "category": {"type": "string", "enum": categories},
                        "explanation": {"type": "string"},
                    }
                ),
            },
            "priorities": {"type": "array", "items": {"type": "string"}},
            "sample": obj({"original_quote": {"type": "string"}, "improved": {"type": "string"}}),
            "summary": {"type": "string"},
            "confidence": {"type": "number"},
            "injection_detected": {"type": "boolean"},
        }
    )


def _norm(text: str) -> str:
    text = (text or "").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def validate_quotes(errors: list[dict], essay: str) -> tuple[list[dict], int]:
    """Оставляет только ошибки, чья цитата действительно есть в тексте ученика."""
    haystack = _norm(essay)
    kept = [e for e in errors if e.get("quote") and _norm(e["quote"]) in haystack]
    return kept, len(errors) - len(kept)


def build_user_message(
    *,
    task_type: str,
    prompt_text: str,
    essay: str,
    words: int,
    minutes: str | None,
    language: str,
    recurring: list[str],
) -> str:
    facts = [
        f"Task type: {TASK_TYPES[task_type]}",
        f"Word count (computed by code): {words}; minimum required: {MIN_WORDS[task_type]}",
        f"Time spent (reported by candidate): {minutes or 'not reported'} minutes",
        f"Feedback language: {'Russian' if language == 'ru' else 'English'}",
        "Candidate's recurring error categories: " + (", ".join(recurring) if recurring else "none yet"),
    ]
    return "\n\n".join(
        [
            data_block("facts", "\n".join(facts)),
            data_block("task", prompt_text.strip() or "(no task text provided; assess as a general response)"),
            data_block("response", essay.strip()),
        ]
    )


def build_system(repo: Repo, categories: dict[str, str]) -> str:
    rubric = (repo.rubrics / "writing_criteria.md").read_text(encoding="utf-8")
    cats = "\n".join(f"- {key}: {label}" for key, label in categories.items())
    return (
        load_prompt(repo, "examiner_writing.md")
        + "\n\n# Assessment criteria\n\n"
        + rubric
        + "\n\n# Allowed error categories (use the key)\n\n"
        + cats
    )


def _run_band(run: dict, rounding: str) -> float:
    return bands.task_band([bands.snap_half(run["criteria"][k]["band"]) for k in CRITERIA], rounding)


def assess(
    llm: LLM,
    repo: Repo,
    config: dict,
    *,
    task_type: str,
    prompt_text: str,
    essay: str,
    minutes: str | None,
    recurring: list[str],
    previous_median: float | None,
) -> dict[str, Any]:
    categories = repo.rubric_yaml("error_categories.yml")["writing"]
    schema = assessment_schema(list(categories))
    system = build_system(repo, categories)
    words = count_words(essay)
    user = build_user_message(
        task_type=task_type,
        prompt_text=prompt_text,
        essay=essay,
        words=words,
        minutes=minutes,
        language=config["agent"].get("feedback_language", "ru"),
        recurring=recurring,
    )
    rounding = config["scoring"].get("writing_task_rounding", "floor_half")
    recheck = config["agent"]["recheck"]

    # Вторая оценка — другой моделью (роль examiner_recheck), если она настроена и доступна:
    # независимое мнение надёжнее, чем повтор той же модели.
    second_role = "examiner_recheck" if llm.has_role("examiner_recheck") and llm.available("examiner_recheck") else "examiner"
    roles_used = ["examiner"]
    runs = [llm.json_call("examiner", system, user, schema)]
    first = _run_band(runs[0], rounding)
    needs_second = float(runs[0].get("confidence", 1)) < recheck["low_confidence"] or (
        previous_median is not None and abs(first - previous_median) > recheck["jump_from_median"]
    )
    if needs_second:
        runs.append(llm.json_call(second_role, system, user, schema))
        roles_used.append(second_role)
        if abs(_run_band(runs[1], rounding) - first) > recheck["disagreement"]:
            runs.append(llm.json_call("examiner", system, user, schema))
            roles_used.append("examiner")

    criteria = {
        k: bands.snap_half(median(bands.snap_half(r["criteria"][k]["band"]) for r in runs)) for k in CRITERIA
    }
    band = bands.task_band(list(criteria.values()), rounding)
    # Тексты разбора берём из прогона, чей балл ближе всего к итоговому.
    best = min(runs, key=lambda r: abs(_run_band(r, rounding) - band))
    errors, dropped = validate_quotes(best.get("errors", []), essay)
    return {
        "task_type": task_type,
        "words": words,
        "min_words": MIN_WORDS[task_type],
        "criteria": criteria,
        "comments": {k: best["criteria"][k]["comment"] for k in CRITERIA},
        "band": band,
        "strengths": best.get("strengths", [])[:3],
        "errors": errors,
        "dropped_errors": dropped,
        "priorities": best.get("priorities", [])[:3],
        "sample": best.get("sample") or {"original_quote": "", "improved": ""},
        "summary": best.get("summary", ""),
        "confidence": round(float(median(float(r.get("confidence", 0)) for r in runs)), 2),
        "runs": len(runs),
        "run_bands": [_run_band(r, rounding) for r in runs],
        "injection_detected": any(r.get("injection_detected") for r in runs),
        "model": " + ".join(dict.fromkeys(llm.describe(r) for r in roles_used)),
    }
