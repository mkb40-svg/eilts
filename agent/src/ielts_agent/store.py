"""Данные ученика в файлах progress/: профиль, результаты, банк ошибок, расходы на ИИ."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .repo import Repo

CLOSED_AFTER_CLEAN = 3  # три чистые работы подряд — категория ошибки закрыта


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, repo: Repo):
        self.repo = repo

    # --- профиль --------------------------------------------------------------
    @property
    def profile_path(self):
        return self.repo.progress / "profile.json"

    def profile(self) -> dict[str, Any] | None:
        if not self.profile_path.exists():
            return None
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def save_profile(self, profile: dict[str, Any]) -> None:
        self.repo.write_text(self.profile_path, _dump(profile))

    # --- результаты -----------------------------------------------------------
    @property
    def results_path(self):
        return self.repo.progress / "results.jsonl"

    def results(self) -> list[dict[str, Any]]:
        if not self.results_path.exists():
            return []
        rows = []
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def has_result(self, result_id: str) -> bool:
        return any(r.get("id") == result_id for r in self.results())

    def add_result(self, row: dict[str, Any]) -> bool:
        """Идемпотентно: повторная запись с тем же id не добавляется."""
        if self.has_result(row["id"]):
            return False
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        self.repo.check_writable(self.results_path)
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return True

    # --- банк ошибок ----------------------------------------------------------
    @property
    def error_bank_path(self):
        return self.repo.progress / "error_bank.json"

    def error_bank(self) -> dict[str, dict[str, Any]]:
        if not self.error_bank_path.exists():
            return {}
        return json.loads(self.error_bank_path.read_text(encoding="utf-8"))

    def update_error_bank(
        self,
        module: str,
        errors: Iterable[dict[str, Any]],
        source_id: str,
        today: date | None = None,
    ) -> dict[str, int]:
        """Возвращает {категория: сколько раз встречалась всего} для категорий этой работы."""
        today_s = (today or date.today()).isoformat()
        bank = self.error_bank()
        seen: dict[str, int] = {}
        for err in errors:
            cat = err.get("category") or "other"
            key = f"{module}:{cat}"
            entry = bank.setdefault(
                key,
                {"module": module, "category": cat, "count": 0, "first_seen": today_s, "examples": []},
            )
            entry["count"] += 1
            entry["last_seen"] = today_s
            entry["clean_streak"] = 0
            entry["closed"] = False
            if err.get("quote") and len(entry["examples"]) < 5:
                entry["examples"].append(
                    {"wrong": err.get("quote"), "right": err.get("correction", ""), "src": source_id}
                )
            seen[cat] = entry["count"]
        for key, entry in bank.items():
            if entry.get("module") == module and entry["category"] not in seen:
                entry["clean_streak"] = entry.get("clean_streak", 0) + 1
                entry["closed"] = entry["clean_streak"] >= CLOSED_AFTER_CLEAN
        self.repo.write_text(self.error_bank_path, _dump(bank))
        return seen

    def top_errors(self, module: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        entries = [
            e
            for e in self.error_bank().values()
            if not e.get("closed") and (module is None or e.get("module") == module)
        ]
        return sorted(entries, key=lambda e: (-e["count"], e["category"]))[:limit]

    # --- расходы на ИИ --------------------------------------------------------
    @property
    def usage_path(self):
        return self.repo.progress / "usage.jsonl"

    def add_usage(self, row: dict[str, Any]) -> None:
        self.repo.check_writable(self.usage_path)
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def month_spend(self, month: str | None = None) -> float:
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        if not self.usage_path.exists():
            return 0.0
        total = 0.0
        for line in self.usage_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if str(row.get("at", "")).startswith(month):
                    total += float(row.get("cost_usd", 0))
        return round(total, 4)
