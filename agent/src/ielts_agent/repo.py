"""Пути репозитория и загрузка конфигурации."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "schedule": {
        "timezone": "UTC",
        "reminder_after_days": 2,
        "max_load_multiplier": 1.5,
        "mock_every_weeks": 2,
    },
    "agent": {
        "providers": {},
        "roles": {},
        "feedback_language": "ru",
        "budget_usd_month": 10,
        "max_errors_in_comment": 10,
        "recheck": {"low_confidence": 0.6, "jump_from_median": 1.0, "disagreement": 0.5},
    },
    "scoring": {"writing_task_rounding": "floor_half"},
    "hosting": {"mode": "single_public"},
}

# Куда агенту разрешено писать (раздел 5 ТЗ).
WRITABLE_DIRS = ("progress", "plan", "submissions", "content/generated")
WRITABLE_FILES = ("PROGRESS.md",)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("IELTS_REPO_ROOT")
    if env:
        return Path(env).resolve()
    start = (start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "config.yml").exists() and (candidate / "content").is_dir():
            return candidate
    return Path(__file__).resolve().parents[3]


@dataclass
class Repo:
    root: Path

    @classmethod
    def discover(cls) -> "Repo":
        return cls(find_root())

    # --- каталоги -------------------------------------------------------------
    @property
    def content(self) -> Path:
        return self.root / "content"

    @property
    def answer_keys(self) -> Path:
        return self.content / "answer-keys"

    @property
    def progress(self) -> Path:
        return self.root / "progress"

    @property
    def reports(self) -> Path:
        return self.progress / "reports"

    @property
    def charts(self) -> Path:
        return self.progress / "charts"

    @property
    def plan(self) -> Path:
        return self.root / "plan"

    @property
    def submissions(self) -> Path:
        return self.root / "submissions"

    @property
    def templates(self) -> Path:
        return self.root / ".github" / "ISSUE_TEMPLATE"

    @property
    def agent_dir(self) -> Path:
        return self.root / "agent"

    @property
    def rubrics(self) -> Path:
        return self.agent_dir / "rubrics"

    @property
    def prompts(self) -> Path:
        return self.agent_dir / "prompts"

    @property
    def calibration(self) -> Path:
        return self.agent_dir / "calibration"

    # --- конфигурация ---------------------------------------------------------
    def config(self) -> dict[str, Any]:
        path = self.root / "config.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        return _deep_merge(DEFAULT_CONFIG, data or {})

    def rubric_yaml(self, name: str) -> Any:
        return yaml.safe_load((self.rubrics / name).read_text(encoding="utf-8"))

    def check_writable(self, path: Path) -> Path:
        """Агент пишет только в разрешённые места: защита от ошибок и подмены путей."""
        rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        if rel in WRITABLE_FILES or any(rel == d or rel.startswith(d + "/") for d in WRITABLE_DIRS):
            return path
        raise PermissionError(f"Агенту запрещено писать в {rel}")

    def write_text(self, path: Path, text: str) -> None:
        self.check_writable(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
