"""Наставник без ИИ: текущие баллы, выбор следующего задания, план до экзамена, итоги недели.

Логика детерминирована и проверяется тестами; ИИ лишь дополняет недельный отчёт советом.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from . import bands
from .content import Task, TaskIndex

WEIGHTS = {"writing": 1.2, "reading": 1.0}
UNKNOWN_GAP = 1.0


def module_of(kind: str) -> str:
    return "reading" if kind.startswith("reading") else "writing"


def variant_of(profile: dict | None) -> str:
    return "general" if profile and profile.get("module") == "general" else "academic"


def result_date(row: dict) -> date:
    return date.fromisoformat(str(row.get("date") or row.get("checked_at", "1970-01-01"))[:10])


def current_bands(results: list[dict]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for module in ("writing", "reading"):
        scored = [r["band"] for r in results if r.get("module") == module and r.get("band") is not None]
        out[module] = bands.median_band(scored[-3:])
    return out


def reading_accuracy(results: list[dict]) -> dict[str, list[int]]:
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in results:
        for qtype, (correct, total) in (row.get("by_type") or {}).items():
            stats[qtype][0] += correct
            stats[qtype][1] += total
    return dict(stats)


def weakest_type(results: list[dict], min_total: int = 3) -> tuple[str, float] | None:
    candidates = [(c / t, q) for q, (c, t) in reading_accuracy(results).items() if t >= min_total]
    if not candidates:
        return None
    ratio, qtype = min(candidates)
    return qtype, ratio


def days_since_last(results: list[dict], today: date) -> int | None:
    if not results:
        return None
    return (today - max(result_date(r) for r in results)).days


def streak(results: list[dict], today: date) -> int:
    days = {result_date(r) for r in results}
    if not days:
        return 0
    cursor = today if today in days else today - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


@dataclass
class Choice:
    task: Task
    reason: str
    light: bool = False
    repeat: bool = False


def _fmt(band: float | None) -> str:
    return "нет данных" if band is None else f"{band:.1f}"


def choose_next(
    profile: dict | None,
    results: list[dict],
    index: TaskIndex,
    today: date,
    config: dict,
    exclude: set[str] | None = None,
) -> Choice | None:
    exclude = {e.upper() for e in (exclude or set())}
    variant = variant_of(profile)
    target = float((profile or {}).get("target_band", 7.0))
    current = current_bands(results)
    done = {str(r.get("task", "")).upper() for r in results}
    idle = days_since_last(results, today)
    light = idle is not None and idle >= int(config["schedule"].get("reminder_after_days", 2))

    recent = [r.get("module") for r in results[-2:]]
    scores: dict[str, float] = {}
    for module, weight in WEIGHTS.items():
        gap = UNKNOWN_GAP if current[module] is None else max(target - current[module], 0.0)
        penalty = 0.5 if len(recent) == 2 and all(m == module for m in recent) else 1.0
        scores[module] = (gap + 0.1) * weight * penalty
    if light:
        scores["reading"] += 10  # после перерыва — короткое задание на 20 минут
    order = sorted(scores, key=lambda m: -scores[m])

    for module in order:
        if module == "reading":
            kinds = [f"reading-{variant}"]
        else:
            t2 = sum(1 for r in results if r.get("kind") == "task2")
            t1 = sum(1 for r in results if str(r.get("kind", "")).startswith("task1"))
            kinds = [f"task1-{variant}", "task2"] if (light or t1 * 2 < t2) else ["task2", f"task1-{variant}"]
        for kind in kinds:
            pool = [t for t in index.by_kind(kind) if t.id not in exclude]
            if not pool:
                continue
            fresh = [t for t in pool if t.id not in done]
            if fresh:
                task = _pick_reading(fresh, results) if module == "reading" else fresh[0]
                return Choice(task, _reason(module, kind, current, target, results, light, task), light)
            # Всё пройдено — повтор самого давнего: полезно переписать эссе или перерешать текст.
            last_done = {str(r.get("task", "")).upper(): result_date(r) for r in results}
            task = min(pool, key=lambda t: (last_done.get(t.id, date.min), t.id))
            reason = _reason(module, kind, current, target, results, light, task)
            return Choice(task, reason + " Новые задания этого типа закончились — повторите это.", light, True)
    return None


def _pick_reading(fresh: list[Task], results: list[dict]) -> Task:
    weak = weakest_type(results)
    if weak:
        for task in fresh:
            if weak[0] in task.question_types:
                return task
    return fresh[0]


def _reason(module, kind, current, target, results, light, task) -> str:
    if light:
        return "Вы давно не занимались — возвращаемся коротким заданием на 20 минут, без перегрузки."
    if module == "writing":
        base = f"Writing сейчас {_fmt(current['writing'])} при цели {target:.1f} — здесь самый большой разрыв."
        if kind == "task2":
            return base + " Task 2 весит в итоге Writing вдвое больше Task 1."
        return base + " Task 1 давно не было — держим оба типа в форме."
    base = f"Reading сейчас {_fmt(current['reading'])} при цели {target:.1f}."
    weak = weakest_type(results)
    if weak and weak[0] in task.question_types:
        from .reading import TYPE_RU

        return base + f" Слабее всего тип «{TYPE_RU.get(weak[0], weak[0])}» ({weak[1]:.0%}) — он есть в этом тексте."
    return base


# --- план до экзамена ---------------------------------------------------------------------
SHARES = [
    ("Writing (эссе и письма)", 0.35),
    ("Reading (тексты на время)", 0.25),
    ("Лексика и грамматика по банку ошибок", 0.15),
    ("Listening и Speaking — по своим материалам (в системе с этапа v1)", 0.25),
]


def build_plan(profile: dict, today: date) -> str:
    exam = date.fromisoformat(profile["exam_date"])
    days_left = (exam - today).days
    weeks = max(1, math.ceil(days_left / 7)) if days_left > 0 else 0
    hours = float(profile.get("hours_per_week", 6))
    essays = max(1, round(hours * 0.35))  # ~1 час на эссе вместе с разбором
    passages = max(1, round(hours * 0.25 / 0.5))

    lines = [
        "# План подготовки",
        "",
        f"_Создан агентом {today.isoformat()} по анкете «Старт». Обновляется при новой анкете._",
        "",
        f"Цель — **{float(profile['target_band']):.1f}** ({'Academic' if profile.get('module') != 'general' else 'General Training'}),"
        f" экзамен **{exam.isoformat()}**, осталось **{max(days_left, 0)} дн.** (≈ {weeks} нед.),"
        f" **{hours:g} ч** в неделю.",
        "",
    ]
    if days_left <= 0:
        lines += ["> Дата экзамена уже прошла — обновите её, отправив анкету «Старт» ещё раз.", ""]
    elif weeks < 4:
        lines += [
            "> До экзамена меньше 4 недель: план сжат до практики в формате экзамена и разбора ошибок. "
            "Если цель выше текущего уровня больше чем на 1 band, подумайте о переносе даты или увеличении часов.",
            "",
        ]

    lines += ["## Неделя: на что уходят часы", "", "| Направление | Часов в неделю |", "| --- | --- |"]
    for name, share in SHARES:
        lines.append(f"| {name} | {hours * share:.1f} |")
    lines += [
        "",
        f"Ориентир на неделю: **{essays}** работ Writing (Task 2 вдвое чаще Task 1), **{passages}** текстов Reading.",
        "Задание на каждый день агент присылает отдельной Issue и подбирает под слабые места.",
        "",
        "## Этапы",
        "",
        "| Недели | Этап | Что делаем |",
        "| --- | --- | --- |",
    ]
    if weeks >= 8:
        base_end = 2
        final_start = weeks - 1
        lines += [
            f"| 1–{base_end} | Диагностика и база | Диагностика, разбор формата, банк ошибок |",
            f"| {base_end + 1}–{final_start - 1} | Практика | Задания под слабые места, повтор ошибок |",
            f"| {final_start}–{weeks} | Финишная прямая | Пробные экзамены, повтор банка ошибок, тайминг |",
        ]
    elif weeks >= 1:
        lines += [
            f"| 1–{max(1, weeks - 1)} | Практика | Диагностика, задания под слабые места |",
            f"| {weeks} | Финишная прямая | Пробный экзамен, повтор банка ошибок |",
        ]
    lines += ["", "За 7 дней до экзамена агент переключится в режим повторения ошибок и тайминга.", ""]
    return "\n".join(lines)


# --- итоги недели --------------------------------------------------------------------------
def week_stats(results: list[dict], today: date) -> dict[str, Any]:
    start = today - timedelta(days=6)
    prev_start = start - timedelta(days=7)
    this = [r for r in results if start <= result_date(r) <= today]
    prev = [r for r in results if prev_start <= result_date(r) < start]

    def med(rows, module):
        return bands.median_band(r.get("band") for r in rows if r.get("module") == module)

    errors: dict[str, int] = defaultdict(int)
    for row in this:
        for cat in row.get("errors") or []:
            errors[cat] += 1
    return {
        "start": start,
        "end": today,
        "count": len(this),
        "by_module": {m: sum(1 for r in this if r.get("module") == m) for m in ("writing", "reading")},
        "bands": {m: (med(this, m), med(prev, m)) for m in ("writing", "reading")},
        "top_errors": sorted(errors.items(), key=lambda kv: -kv[1])[:3],
        "active_days": len({result_date(r) for r in this}),
    }
