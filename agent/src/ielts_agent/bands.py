"""Баллы IELTS: округление, перевод сырого балла, медианы."""

from __future__ import annotations

import math
from statistics import median as _median
from typing import Iterable, Sequence


def snap_half(value: float) -> float:
    """Ближайшее значение шкалы 0–9 с шагом 0.5."""
    value = max(0.0, min(9.0, float(value)))
    return round(value * 2) / 2


def floor_half(value: float) -> float:
    """Вниз до ближайших 0.5 (осторожное округление одной работы)."""
    return math.floor(round(value * 8) / 8 * 2) / 2


def round_overall(value: float) -> float:
    """Правило IELTS для общего балла: .25 вверх до .5, .75 вверх до целого."""
    value = round(value * 8) / 8  # убираем погрешность float: средние четырёх band кратны 0.125
    whole = math.floor(value)
    frac = value - whole
    if frac < 0.25:
        return float(whole)
    if frac < 0.75:
        return whole + 0.5
    return float(whole + 1)


def task_band(criteria: Sequence[float], mode: str = "floor_half") -> float:
    mean = sum(criteria) / len(criteria)
    if mode == "ielts":
        return round_overall(mean)
    return floor_half(mean)


def overall_band(modules: Sequence[float]) -> float:
    return round_overall(sum(modules) / len(modules))


def raw_to_band(raw: int, table: Iterable[Sequence[float]]) -> float:
    for min_raw, band in table:
        if raw >= min_raw:
            return float(band)
    return 0.0


def scale_to_40(correct: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(correct / total * 40))


def median_band(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return snap_half(_median(clean))
