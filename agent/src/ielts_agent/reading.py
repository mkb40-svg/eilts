"""Проверка Reading по ключу — делает код, не ИИ (ФТ-13)."""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import bands

_ANSWER_LINE = re.compile(r"^\s*(?:q\s*)?(\d{1,2})\s*[\.\):\-–—]?\s*(.*?)\s*$", re.I)

_JUDGEMENT_ALIASES = {
    "T": "TRUE",
    "F": "FALSE",
    "NG": "NOT GIVEN",
    "N/G": "NOT GIVEN",
    "NOTGIVEN": "NOT GIVEN",
    "NOT-GIVEN": "NOT GIVEN",
    "Y": "YES",
    "N": "NO",
}
_JUDGEMENT_TYPES = {"tfng", "ynng"}
_LETTER_TYPES = {
    "matching_headings",
    "matching_information",
    "matching_features",
    "matching_sentence_endings",
    "multiple_choice",
}


def parse_answers(text: str) -> dict[int, str]:
    """«1. TRUE», «2) b», «3 - water vapour» → {1: "TRUE", 2: "b", 3: "water vapour"}."""
    answers: dict[int, str] = {}
    for line in (text or "").splitlines():
        match = _ANSWER_LINE.match(line)
        if match and match.group(1):
            answers[int(match.group(1))] = match.group(2).strip()
    return answers


def normalize(value: str) -> str:
    value = (value or "").strip().strip("\"'«»“”").replace("’", "'")
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .;,")
    return value.lower()


def _normalize_typed(value: str, qtype: str) -> str:
    value = normalize(value)
    if qtype in _JUDGEMENT_TYPES:
        upper = value.upper()
        return _JUDGEMENT_ALIASES.get(upper.replace(" ", ""), _JUDGEMENT_ALIASES.get(upper, upper)).lower()
    if qtype in _LETTER_TYPES:
        return value.strip("()[] ")
    return value


def count_words(value: str) -> int:
    return len([w for w in re.split(r"\s+", (value or "").strip()) if w])


@dataclass
class ItemResult:
    number: int
    qtype: str
    given: str
    accepted: list[str]
    correct: bool
    reason: str  # ok | blank | wrong | word_limit | spelling


@dataclass
class ReadingResult:
    items: list[ItemResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def correct(self) -> int:
        return sum(1 for i in self.items if i.correct)

    @property
    def wrong(self) -> list[ItemResult]:
        return [i for i in self.items if not i.correct]

    def accuracy_by_type(self) -> dict[str, list[int]]:
        stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for item in self.items:
            stats[item.qtype][1] += 1
            if item.correct:
                stats[item.qtype][0] += 1
        return dict(stats)


def check(key: dict, answers: dict[int, str]) -> ReadingResult:
    result = ReadingResult()
    for number in sorted(key["answers"]):
        spec = key["answers"][number]
        qtype = str(spec.get("type", "short_answer"))
        accepted = spec.get("answer")
        accepted = accepted if isinstance(accepted, list) else [accepted]
        accepted = [str(a) for a in accepted]
        given = answers.get(number, "").strip()
        max_words = spec.get("max_words")

        if not given:
            result.items.append(ItemResult(number, qtype, given, accepted, False, "blank"))
            continue
        if max_words and count_words(given) > int(max_words):
            result.items.append(ItemResult(number, qtype, given, accepted, False, "word_limit"))
            continue

        norm_given = _normalize_typed(given, qtype)
        norm_accepted = [_normalize_typed(a, qtype) for a in accepted]
        if norm_given in norm_accepted:
            result.items.append(ItemResult(number, qtype, given, accepted, True, "ok"))
            continue

        close = qtype not in _JUDGEMENT_TYPES | _LETTER_TYPES and any(
            difflib.SequenceMatcher(None, norm_given, a).ratio() >= 0.8 for a in norm_accepted
        )
        result.items.append(ItemResult(number, qtype, given, accepted, False, "spelling" if close else "wrong"))
    return result


def band_estimate(result: ReadingResult, variant: str, conversion: dict) -> tuple[int, float]:
    """Сырой балл, приведённый к 40 вопросам, и ориентировочный band."""
    raw40 = bands.scale_to_40(result.correct, result.total)
    table = conversion["reading_general" if variant == "general" else "reading_academic"]
    return raw40, bands.raw_to_band(raw40, table)


REASON_RU = {
    "blank": "нет ответа",
    "wrong": "неверно",
    "word_limit": "превышен лимит слов",
    "spelling": "ошибка в написании",
}

TYPE_RU = {
    "tfng": "True / False / Not Given",
    "ynng": "Yes / No / Not Given",
    "matching_headings": "Matching headings",
    "matching_information": "Matching information",
    "matching_features": "Matching features",
    "matching_sentence_endings": "Matching sentence endings",
    "multiple_choice": "Multiple choice",
    "summary_completion": "Summary completion",
    "note_completion": "Note completion",
    "table_completion": "Table completion",
    "flowchart_completion": "Flow-chart completion",
    "sentence_completion": "Sentence completion",
    "diagram_labelling": "Diagram labelling",
    "short_answer": "Short answer",
}
