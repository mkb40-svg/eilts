"""Баллы, проверка Reading, разбор форм, материалы."""

import pytest

from ielts_agent import bands, issueforms, reading
from ielts_agent.content import load_answer_key, load_tasks
from ielts_agent.validate import validate_content
from conftest import START_BODY, form_body


@pytest.mark.parametrize(
    "mean, expected",
    [(6.25, 6.5), (6.75, 7.0), (6.125, 6.0), (6.0, 6.0), (6.5, 6.5), (6.875, 7.0), (6.625, 6.5), (6.375, 6.5)],
)
def test_overall_rounding_ielts_rule(mean, expected):
    assert bands.round_overall(mean) == expected


def test_overall_band_from_four_modules():
    assert bands.overall_band([6.5, 6.5, 5.5, 6.5]) == 6.5  # 6.25 → 6.5
    assert bands.overall_band([7.0, 6.5, 6.5, 7.0]) == 7.0  # 6.75 → 7.0


def test_task_band_rounding_modes():
    assert bands.task_band([6.0, 6.5, 6.0, 5.5], "floor_half") == 6.0
    assert bands.task_band([6.5, 6.5, 6.0, 6.0], "floor_half") == 6.0
    assert bands.task_band([6.5, 6.5, 6.0, 6.0], "ielts") == 6.5


def test_raw_to_band_and_scaling(repo):
    conv = repo.rubric_yaml("conversion.yml")
    assert bands.raw_to_band(30, conv["reading_academic"]) == 7.0
    assert bands.raw_to_band(34, conv["reading_general"]) == 7.0
    assert bands.raw_to_band(0, conv["listening"]) == 0.0
    assert bands.scale_to_40(10, 13) == 31


def test_parse_answers_accepts_common_formats():
    text = "1. TRUE\n2) ng\n3 - b\nq4: water vapour\n\nnot an answer"
    assert reading.parse_answers(text) == {1: "TRUE", 2: "ng", 3: "b", 4: "water vapour"}


def test_check_rules():
    key = {
        "answers": {
            1: {"answer": ["NOT GIVEN"], "type": "tfng"},
            2: {"answer": ["B"], "type": "multiple_choice"},
            3: {"answer": ["water vapour", "water vapor"], "type": "sentence_completion", "max_words": 2},
            4: {"answer": ["roots"], "type": "sentence_completion", "max_words": 2},
            5: {"answer": ["iv"], "type": "matching_headings"},
            6: {"answer": ["moisture"], "type": "sentence_completion", "max_words": 1},
            7: {"answer": ["FALSE"], "type": "tfng"},
        },
        "evidence": {},
    }
    answers = {1: "ng", 2: "(b)", 3: "Water Vapor.", 4: "the deep roots", 5: "IV", 6: "moistur"}
    result = reading.check(key, answers)
    by = {i.number: i for i in result.items}
    assert by[1].correct and by[2].correct and by[3].correct and by[5].correct
    assert by[4].reason == "word_limit"
    assert by[6].reason == "spelling" and not by[6].correct  # орфография = неверно, как на экзамене
    assert by[7].reason == "blank"
    assert result.correct == 4 and result.total == 7


def test_real_answer_key_full_marks(repo):
    key = load_answer_key(repo, "R-0001")
    answers = {n: spec["answer"][0] for n, spec in key["answers"].items()}
    result = reading.check(key, answers)
    assert result.correct == result.total == 13


def test_all_content_is_valid(repo):
    """Лицензии, обязательные поля, совпадение номеров, ответы и цитаты дословно из текста."""
    assert validate_content(repo) == []


def test_content_index(repo):
    index = load_tasks(repo)
    assert index.get("r-0001").diagnostic
    assert {t.kind for t in index} >= {"reading-academic", "reading-general", "task2", "task1-academic", "task1-general"}


def test_form_parsing_and_detection_without_labels(repo):
    templates = issueforms.load_templates(repo)
    start = issueforms.detect(START_BODY, [], templates)
    assert start.name == "start"
    values = issueforms.parse(START_BODY, start)
    assert values["target_band"] == "7.0"
    assert values["weak_areas"] is None
    assert values["privacy"] and values["privacy"][0].startswith("Я понимаю")

    body = form_body(**{"ID задания": "R-0002", "Ответы": "1. TRUE\n2. FALSE", "Сколько минут заняло": "_No response_"})
    tpl = issueforms.detect(body, [], templates)
    assert tpl.name == "reading"
    assert issueforms.parse(body, tpl)["answers"] == "1. TRUE\n2. FALSE"


def test_task_marker():
    assert issueforms.task_marker("text <!-- ielts-task: w2-0003 -->") == "W2-0003"
    assert issueforms.task_marker("no marker") is None
