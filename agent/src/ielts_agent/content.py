"""Учебные материалы: задания с YAML-шапкой и ключи ответов."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .repo import Repo

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)

REQUIRED_FIELDS = ("id", "module", "title", "time_minutes", "source", "license", "ai_generated", "reviewed")


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(text.lstrip("﻿"))
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group(1)) or {}
    return meta, match.group(2).strip() + "\n"


@dataclass
class Task:
    id: str
    module: str
    path: Path
    meta: dict[str, Any]
    body: str
    rel_path: str = ""

    @property
    def title(self) -> str:
        return str(self.meta.get("title", self.id))

    @property
    def variant(self) -> str:
        return str(self.meta.get("variant", ""))

    @property
    def writing_task(self) -> str:
        return str(self.meta.get("task", ""))

    @property
    def min_words(self) -> int:
        return int(self.meta.get("min_words") or (250 if self.writing_task == "task2" else 150))

    @property
    def time_minutes(self) -> int:
        return int(self.meta.get("time_minutes") or 20)

    @property
    def diagnostic(self) -> bool:
        return bool(self.meta.get("diagnostic"))

    @property
    def question_types(self) -> list[str]:
        return list(self.meta.get("question_types") or [])

    @property
    def kind(self) -> str:
        """Тип для плана: reading-academic, reading-general, task2, task1-academic, task1-general."""
        if self.module == "reading":
            return f"reading-{self.variant or 'academic'}"
        return self.writing_task


@dataclass
class TaskIndex:
    tasks: dict[str, Task] = field(default_factory=dict)

    def get(self, task_id: str | None) -> Task | None:
        if not task_id:
            return None
        return self.tasks.get(task_id.strip().upper())

    def by_kind(self, kind: str) -> list[Task]:
        return sorted((t for t in self.tasks.values() if t.kind == kind), key=lambda t: t.id)

    def __iter__(self):
        return iter(sorted(self.tasks.values(), key=lambda t: t.id))

    def __len__(self) -> int:
        return len(self.tasks)


def load_tasks(repo: Repo) -> TaskIndex:
    index = TaskIndex()
    for path in sorted(repo.content.rglob("*.md")):
        if path.name.upper() in {"FORMAT.MD", "README.MD"} or "answer-keys" in path.parts:
            continue
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if not meta.get("id"):
            continue
        task_id = str(meta["id"]).upper()
        index.tasks[task_id] = Task(
            id=task_id,
            module=str(meta.get("module", "")),
            path=path,
            meta=meta,
            body=body,
            rel_path=path.relative_to(repo.root).as_posix(),
        )
    return index


def load_answer_key(repo: Repo, task_id: str) -> dict[str, Any]:
    path = repo.answer_keys / f"{task_id.upper()}.yml"
    if not path.exists():
        raise FileNotFoundError(f"Нет ключа ответов для {task_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    answers = {int(k): v for k, v in (data.get("answers") or {}).items()}
    evidence = {int(k): v for k, v in (data.get("evidence") or {}).items()}
    return {"id": data.get("id", task_id), "answers": answers, "evidence": evidence}


def passage_text(task: Task) -> str:
    """Текст до первого блока вопросов — для проверки цитат."""
    return re.split(r"^##\s+Questions", task.body, maxsplit=1, flags=re.M)[0]
