"""Калибровка экзаменатора (раздел 7.7 ТЗ): сравнение оценок ИИ с оценками преподавателя."""

from __future__ import annotations

from .content import parse_front_matter
from .llm import LLM
from .repo import Repo
from .writing import assess

MAX_MAE = 0.5
MIN_WITHIN_HALF = 0.8


def run(repo: Repo, config: dict, llm: LLM) -> tuple[bool, str]:
    samples = sorted((repo.calibration / "writing").glob("*.md"))
    if not samples:
        return True, "Калибровочных работ нет (agent/calibration/writing/*.md) — пропускаю."
    reason = llm.unavailable_reason("examiner")
    if reason:
        return True, f"Экзаменатор недоступен ({reason}) — калибровка пропущена."

    rows, diffs = [], []
    for path in samples:
        meta, essay = parse_front_matter(path.read_text(encoding="utf-8"))
        expected = float(meta["band"])
        result = assess(
            llm, repo, config,
            task_type=str(meta.get("task", "task2")),
            prompt_text=str(meta.get("prompt", "")),
            essay=essay,
            minutes=None,
            recurring=[],
            previous_median=None,
        )
        diff = abs(result["band"] - expected)
        diffs.append(diff)
        rows.append(f"| {path.name} | {expected:.1f} | {result['band']:.1f} | {diff:.1f} |")

    mae = sum(diffs) / len(diffs)
    within = sum(1 for d in diffs if d <= 0.5) / len(diffs)
    ok = mae <= MAX_MAE and within >= MIN_WITHIN_HALF
    report = "\n".join(
        [
            f"## Калибровка экзаменатора: {'пройдена' if ok else 'НЕ пройдена'}",
            "",
            f"Модель: {llm.describe('examiner')} · работ: {len(diffs)} · средняя ошибка {mae:.2f} band "
            f"(порог {MAX_MAE}) · в пределах ±0.5: {within:.0%} (порог {MIN_WITHIN_HALF:.0%})",
            "",
            "| Работа | Преподаватель | ИИ | Разница |",
            "| --- | --- | --- | --- |",
            *rows,
        ]
    )
    return ok, report
