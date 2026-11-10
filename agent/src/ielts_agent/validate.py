"""Проверка учебных материалов: обязательные поля, лицензия, корректность ключей (запускается в CI)."""

from __future__ import annotations

import re

from .content import REQUIRED_FIELDS, load_answer_key, load_tasks, passage_text
from .reading import count_words
from .repo import Repo
from .writing import _norm

_QUESTION = re.compile(r"^\s*(\d{1,2})\.\s+\S", re.M)
COMPLETION = {
    "summary_completion",
    "note_completion",
    "table_completion",
    "flowchart_completion",
    "sentence_completion",
    "diagram_labelling",
    "short_answer",
}
WRITING_TASKS = {"task2", "task1-academic", "task1-general"}


def validate_content(repo: Repo) -> list[str]:
    problems: list[str] = []
    index = load_tasks(repo)
    if not len(index):
        return ["В content/ нет ни одного задания"]
    for task in index:
        where = task.rel_path
        for name in REQUIRED_FIELDS:
            if name not in task.meta or task.meta[name] in (None, ""):
                problems.append(f"{where}: нет обязательного поля «{name}»")
        if task.module == "reading":
            problems += _validate_reading(repo, task, where)
        elif task.module == "writing":
            if task.writing_task not in WRITING_TASKS:
                problems.append(f"{where}: поле task должно быть одним из {sorted(WRITING_TASKS)}")
        else:
            problems.append(f"{where}: неизвестный module «{task.module}»")
    return problems


def _validate_reading(repo: Repo, task, where: str) -> list[str]:
    problems = []
    try:
        key = load_answer_key(repo, task.id)
    except FileNotFoundError:
        return [f"{where}: нет ключа content/answer-keys/{task.id}.yml"]
    questions_part = task.body[len(passage_text(task)):]
    numbers = sorted({int(n) for n in _QUESTION.findall(questions_part)})
    expected = int(task.meta.get("questions") or 0)
    if expected and len(numbers) != expected:
        problems.append(f"{where}: в тексте {len(numbers)} вопросов, в шапке {expected}")
    if sorted(key["answers"]) != numbers:
        problems.append(f"{where}: номера в ключе {sorted(key['answers'])} не совпадают с вопросами {numbers}")
    passage = _norm(passage_text(task))
    for number, spec in key["answers"].items():
        accepted = spec.get("answer")
        if not isinstance(accepted, list) or not accepted:
            problems.append(f"{task.id} Q{number}: answer должен быть непустым списком")
            continue
        if spec.get("type") in COMPLETION:
            limit = spec.get("max_words")
            for variant in accepted:
                if _norm(str(variant)) not in passage:
                    problems.append(f"{task.id} Q{number}: ответа «{variant}» нет в тексте дословно")
                if limit and count_words(str(variant)) > int(limit):
                    problems.append(f"{task.id} Q{number}: «{variant}» длиннее {limit} слов")
        quote = key["evidence"].get(number)
        is_not_given = any(str(a).upper() == "NOT GIVEN" for a in accepted)
        if quote and _norm(quote) not in passage:
            problems.append(f"{task.id} Q{number}: цитаты-доказательства нет в тексте дословно")
        if not quote and not is_not_given:
            problems.append(f"{task.id} Q{number}: нет цитаты-доказательства (evidence)")
    return problems
