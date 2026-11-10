"""План, выбор задания, дашборд, сайт."""

from datetime import date, timedelta

from ielts_agent import planner, progress, site
from ielts_agent.content import load_tasks
from ielts_agent.store import Store
from conftest import TODAY

PROFILE = {"module": "academic", "target_band": 7.0, "exam_date": "2027-03-15", "hours_per_week": 6}


def row(module, kind, band, days_ago, task="X", **extra):
    return {"id": f"{task}-{days_ago}-{module}", "module": module, "kind": kind, "band": band, "task": task,
            "date": (TODAY - timedelta(days=days_ago)).isoformat(), **extra}


def test_plan_has_weeks_and_short_timeline_warning():
    plan = planner.build_plan(PROFILE, TODAY)
    assert "Финишная прямая" in plan and "| Writing (эссе и письма) | 2.1 |" in plan
    soon = planner.build_plan({**PROFILE, "exam_date": (TODAY + timedelta(days=20)).isoformat()}, TODAY)
    assert "меньше 4 недель" in soon


def test_choose_next_targets_biggest_gap(repo):
    index = load_tasks(repo)
    config = repo.config()
    results = [row("reading", "reading-academic", 7.0, 1, "R-0003")]
    choice = planner.choose_next(PROFILE, results, index, TODAY, config)
    assert choice.task.module == "writing" and choice.task.kind == "task2"  # Writing неизвестен → разрыв больше
    results = [row("writing", "task2", 7.0, 2, "W2-0001"), row("writing", "task2", 7.0, 1, "W2-0002")]
    choice = planner.choose_next(PROFILE, results, index, TODAY, config)
    assert choice.task.module == "reading"  # не три Writing подряд и Writing уже на уровне цели


def test_weak_question_type_drives_reading_choice(repo):
    index = load_tasks(repo)
    results = [row("writing", "task2", 7.5, 3, "W2-0001"),
               row("reading", "reading-academic", 6.0, 1, "R-0003",
                   by_type={"ynng": [4, 5], "matching_headings": [0, 4], "tfng": [5, 5]})]
    # R-0003 пройден; R-0001 содержит matching_headings — самый слабый тип
    choice = planner.choose_next(PROFILE, results, index, TODAY, repo.config())
    assert choice.task.id == "R-0001" and "Matching headings" in choice.reason


def test_streak_and_idle():
    results = [row("reading", "reading-academic", 6, d) for d in (0, 1, 2, 5)]
    assert planner.streak(results, TODAY) == 3
    assert planner.days_since_last(results, TODAY) == 0


def test_dashboard_files(repo):
    store = Store(repo)
    store.save_profile(PROFILE)
    store.add_result(row("writing", "task2", 6.0, 2, "W2-0001", words=260, issue=5))
    store.add_result(row("reading", "reading-academic", 6.5, 1, "R-0001", correct=10, total=13,
                         by_type={"tfng": [4, 5]}, issue=6))
    progress.write_dashboard(repo, repo.config(), TODAY)
    md = (repo.root / "PROGRESS.md").read_text(encoding="utf-8")
    assert "| Writing | 6.0 | 7.0 | -1.0 | 1 |" in md and "10/13" in md and "#6" in md
    svg = (repo.charts / "bands.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "цель 7.0" in svg


def test_site_has_tasks_but_no_answer_keys(repo, tmp_path):
    out = tmp_path / "site"
    count = site.build(repo, out, repository="student/ielts-self-prep")
    assert count == len(load_tasks(repo))
    page = (out / "tasks" / "R-0001.html").read_text(encoding="utf-8")
    assert "issues/new?template=reading.yml&amp;task_id=R-0001" in page
    assert "data-minutes='20'" in page
    all_html = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.html"))
    assert "answer-keys" not in all_html and "max_words" not in all_html  # ключи на сайт не попадают
    assert not list(out.rglob("*.yml"))
    assert (out / ".nojekyll").exists()


def test_site_base_for_user_pages(repo, gh):
    from ielts_agent.commands import Context

    ctx = Context(repo, gh, today=date(2026, 11, 10))
    assert ctx.site_base() == "https://student.github.io/ielts-self-prep/"
