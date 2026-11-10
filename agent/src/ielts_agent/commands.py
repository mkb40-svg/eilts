"""Сценарии агента: событие GitHub → проверка → комментарий, файлы прогресса, следующее задание."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import feedback, issueforms, planner, progress, reading, writing
from .content import Task, load_answer_key, load_tasks
from .explain import answer_question, explain_reading
from .github import GitHub, form_url
from .llm import LLM, LLMError, LLMUnavailable
from .repo import Repo
from .store import Store, now_iso

STALE_TASK_DAYS = 3
_LEVELS = {"около 5.0": 5.0, "около 5.5": 5.5, "около 6.0": 6.0, "около 6.5": 6.5, "около 7.0": 7.0, "7.5 и выше": 7.5}


class Context:
    def __init__(self, repo: Repo, gh: GitHub, llm: LLM | None = None, today: date | None = None):
        self.repo = repo
        self.gh = gh
        self.store = Store(repo)
        self.config = repo.config()
        profile = self.store.profile() or {}
        if profile.get("feedback_language"):
            self.config["agent"]["feedback_language"] = profile["feedback_language"]
        self.llm = llm or LLM(repo, self.config)
        self.index = load_tasks(repo)
        self.templates = issueforms.load_templates(repo)
        self.today = today or date.today()

    # --- ссылки ---------------------------------------------------------------
    def site_base(self) -> str | None:
        if "github.com" not in self.gh.server_url:
            return None
        owner, name = self.gh.repository.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"

    def site_task_url(self, task: Task) -> str | None:
        base = self.site_base()
        return f"{base}tasks/{task.id}.html" if base else None

    def form_for(self, task: Task) -> str:
        template = "reading" if task.module == "reading" else "writing"
        title = f"{'Reading' if task.module == 'reading' else 'Writing'}: {task.id}"
        return form_url(self.gh.repo_url, template, task_id=task.id, title=title)

    def next_line(self, exclude: set[str] | None = None) -> str:
        choice = planner.choose_next(
            self.store.profile(), self.store.results(), self.index, self.today, self.config, exclude
        )
        return feedback.next_action(choice, self.form_for(choice.task) if choice else None)


# --- маршрутизация событий ------------------------------------------------------------------
def handle_event(ctx: Context, event_name: str, event: dict[str, Any]) -> str:
    owner = (event.get("repository") or {}).get("owner", {}).get("login")
    sender = (event.get("sender") or {}).get("login")
    if not owner or sender != owner:
        return "skip: событие не от владельца репозитория"

    if event_name == "issues" and event.get("action") == "opened":
        issue = event["issue"]
        labels = [l["name"] for l in issue.get("labels", [])]
        template = issueforms.detect(issue.get("body") or "", labels, ctx.templates)
        if template is None:
            return "skip: Issue не по форме"
        ctx.gh.ensure_labels()
        if template.labels and not set(template.labels) & set(labels):
            ctx.gh.add_labels(issue["number"], template.labels)
        values = issueforms.parse(issue.get("body") or "", template)
        number = issue["number"]
        if template.name == "start":
            return onboard(ctx, number, values)
        if template.name == "writing":
            return submit_writing(ctx, number, values, source=f"i{number}")
        if template.name == "reading":
            return submit_reading(ctx, number, values, source=f"i{number}")
        if template.name == "question":
            return ask(ctx, number, str(values.get("question") or ""), context="")
        return f"skip: форма {template.name} не обрабатывается"

    if event_name == "issue_comment" and event.get("action") == "created":
        issue, comment = event["issue"], event["comment"]
        if (comment.get("user") or {}).get("type") == "Bot":
            return "skip: комментарий бота"
        body = comment.get("body") or ""
        number = issue["number"]
        labels = [l["name"] for l in issue.get("labels", [])]
        if "@claude" in body.lower() or "question" in labels:
            question = re.sub(r"@claude", "", body, flags=re.I).strip()
            return ask(ctx, number, question, context=issue.get("body") or "")
        task_id = issueforms.task_marker(issue.get("body") or "")
        task = ctx.index.get(task_id)
        if task is None:
            return "skip: комментарий не к заданию"
        source = f"i{number}c{comment.get('id', '')}"
        if task.module == "reading":
            return submit_reading(ctx, number, {"task_id": task.id, "answers": body}, source=source)
        return submit_writing(ctx, number, {"task_id": task.id, "essay": body}, source=source)
    return f"skip: событие {event_name}/{event.get('action')}"


# --- анкета «Старт» ----------------------------------------------------------------------
def onboard(ctx: Context, number: int, values: dict[str, Any]) -> str:
    try:
        exam_date = date.fromisoformat(str(values.get("exam_date") or "").strip())
    except ValueError:
        ctx.gh.comment(
            number,
            "Не понял дату экзамена. Укажите её в формате ГГГГ-ММ-ДД (например, 2027-03-15) "
            "и отправьте анкету «Старт» ещё раз.",
        )
        return "start: неверная дата"
    try:
        hours = min(30.0, max(2.0, float(str(values.get("hours_per_week") or "6").replace(",", "."))))
    except ValueError:
        hours = 6.0

    existing = ctx.store.profile()
    profile = {
        "module": "general" if "general" in str(values.get("module", "")).lower() else "academic",
        "target_band": float(values.get("target_band") or 7.0),
        "exam_date": exam_date.isoformat(),
        "hours_per_week": hours,
        "current_level": _LEVELS.get(str(values.get("current_level") or "")),
        "feedback_language": "en" if str(values.get("feedback_language")) == "English" else "ru",
        "weak_areas": values.get("weak_areas") or "",
        "created_at": (existing or {}).get("created_at", now_iso()),
        "updated_at": now_iso(),
        "start_issue": number,
    }
    ctx.store.save_profile(profile)
    ctx.config["agent"]["feedback_language"] = profile["feedback_language"]
    ctx.repo.write_text(ctx.repo.plan / "PLAN.md", planner.build_plan(profile, ctx.today))

    lines = [
        "## Профиль создан" if not existing else "## Профиль обновлён",
        "",
        f"- Формат: **{'General Training' if profile['module'] == 'general' else 'Academic'}**",
        f"- Цель: **{profile['target_band']:.1f}**, экзамен **{profile['exam_date']}** "
        f"(через {(exam_date - ctx.today).days} дн.)",
        f"- Нагрузка: **{hours:g} ч** в неделю",
        "",
        f"План до экзамена — в [plan/PLAN.md]({ctx.gh.blob_url('plan/PLAN.md')}), "
        f"прогресс — в [PROGRESS.md]({ctx.gh.blob_url('PROGRESS.md')}).",
        "",
    ]
    if not existing:
        variant = planner.variant_of(profile)
        created = []
        for kind in (f"reading-{variant}", "task2"):
            pool = ctx.index.by_kind(kind)
            task = next((t for t in pool if t.diagnostic), pool[0] if pool else None)
            if task is None:
                continue
            reason = "Стартовая диагностика: по ней агент поймёт ваш уровень и подстроит план."
            body = feedback.task_issue_body(
                task, reason, ctx.gh.blob_url(task.rel_path), ctx.site_task_url(task), ctx.form_for(task)
            )
            num = ctx.gh.create_issue(f"Диагностика: {task.title}", body, ["task", "diagnostic"])
            created.append(f"- #{num} — {'Reading' if task.module == 'reading' else 'Writing Task 2'}: {task.title}")
        if created:
            lines += ["### Диагностика", "Выполните эти задания в удобном порядке:", *created, ""]
        lines += [
            "Дальше каждый день будет приходить Issue «Задание на день». Отвечайте прямо в ней комментарием.",
            "",
        ]
    ai = [r for r in ("examiner", "explainer", "coach") if ctx.llm.available(r)]
    if not ai:
        lines += [
            "> ИИ пока не подключён: Reading проверяется по ключу, Writing сохраняется с подсчётом слов. "
            "Чтобы включить оценку эссе и разборы, добавьте ключ в Settings → Secrets → Actions (см. README).",
            "",
        ]
    ctx.gh.comment(number, "\n".join(lines))
    ctx.gh.close_issue(number)
    return "start: ok"


# --- Writing -------------------------------------------------------------------------------
def submit_writing(ctx: Context, number: int, values: dict[str, Any], source: str) -> str:
    task = ctx.index.get(str(values.get("task_id") or ""))
    if task is not None and task.module != "writing":
        task = None
    task_type = task.writing_task if task else writing.task_type_from_form(values.get("task_type"))
    prompt_text = task.body if task else str(values.get("custom_prompt") or "")
    essay = str(values.get("essay") or "").strip()
    task_label = task.id if task else "custom"
    result_id = f"{ctx.today.isoformat()}-{task_label}-{source}"
    if ctx.store.has_result(result_id):
        return "writing: уже проверено"
    if not essay:
        ctx.gh.comment(number, "Не нашёл текста работы — вставьте его целиком и отправьте ещё раз.")
        return "writing: пусто"

    words = writing.count_words(essay)
    min_words = writing.MIN_WORDS[task_type]
    minutes = str(values.get("minutes") or "").strip() or None
    ctx.repo.write_text(
        ctx.repo.submissions / f"{result_id}.md",
        f"# {task_label} ({task_type})\n\nIssue #{number} · {ctx.today.isoformat()} · {words} слов\n\n"
        f"## Задание\n\n{prompt_text.strip() or '—'}\n\n## Текст\n\n{essay}\n",
    )

    results = ctx.store.results()
    assessment, no_ai = None, ctx.llm.unavailable_reason("examiner")
    if no_ai is None:
        try:
            recurring = [e["category"] for e in ctx.store.top_errors("writing", limit=5)]
            assessment = writing.assess(
                ctx.llm,
                ctx.repo,
                ctx.config,
                task_type=task_type,
                prompt_text=prompt_text,
                essay=essay,
                minutes=minutes,
                recurring=recurring,
                previous_median=planner.current_bands(results)["writing"],
            )
        except LLMUnavailable as exc:
            no_ai = str(exc)
        except LLMError:
            no_ai = "error"

    repeats: dict[str, int] = {}
    if assessment:
        repeats = ctx.store.update_error_bank("writing", assessment["errors"], result_id, ctx.today)
    ctx.store.add_result(
        {
            "id": result_id,
            "issue": number,
            "module": "writing",
            "task": task_label,
            "kind": task_type,
            "date": ctx.today.isoformat(),
            "checked_at": now_iso(),
            "words": words,
            "minutes": minutes,
            "band": assessment["band"] if assessment else None,
            "criteria": assessment["criteria"] if assessment else None,
            "errors": [e["category"] for e in assessment["errors"]] if assessment else [],
            "confidence": assessment["confidence"] if assessment else None,
            "runs": assessment["runs"] if assessment else 0,
            "model": assessment["model"] if assessment else None,
        }
    )

    categories = ctx.repo.rubric_yaml("error_categories.yml")["writing"]
    title = f"Writing {('· ' + task.id) if task else '· своя тема'}"
    comment = feedback.writing_comment(
        title, words, min_words, assessment, repeats, categories,
        int(ctx.config["agent"].get("max_errors_in_comment", 10)), ctx.next_line(), no_ai,
    )
    report = comment
    if assessment and len(assessment["errors"]) > int(ctx.config["agent"].get("max_errors_in_comment", 10)):
        report = feedback.writing_comment(title, words, min_words, assessment, repeats, categories, 100, "", no_ai)
    ctx.repo.write_text(ctx.repo.reports / f"{result_id}.md", report + "\n")
    ctx.gh.comment(number, comment)
    ctx.gh.add_labels(number, ["needs-recheck"] if no_ai == "error" else ["checked"])
    ctx.gh.close_issue(number)
    return f"writing: {'band ' + str(assessment['band']) if assessment else 'без ИИ (' + str(no_ai) + ')'}"


# --- Reading -------------------------------------------------------------------------------
def submit_reading(ctx: Context, number: int, values: dict[str, Any], source: str) -> str:
    task = ctx.index.get(str(values.get("task_id") or ""))
    if task is None or task.module != "reading":
        available = ", ".join(t.id for t in ctx.index if t.module == "reading")
        ctx.gh.comment(number, f"Не нашёл задание Reading «{values.get('task_id')}». Доступны: {available}.")
        return "reading: нет задания"
    answers = reading.parse_answers(str(values.get("answers") or ""))
    if not answers:
        ctx.gh.comment(number, "Не нашёл ответов. Пишите по одному на строку: `1. TRUE`, `2. B`, `3. water vapour`.")
        return "reading: нет ответов"
    result_id = f"{ctx.today.isoformat()}-{task.id}-{source}"
    if ctx.store.has_result(result_id):
        return "reading: уже проверено"

    key = load_answer_key(ctx.repo, task.id)
    result = reading.check(key, answers)
    raw40, band = reading.band_estimate(result, task.variant, ctx.repo.rubric_yaml("conversion.yml"))
    ctx.repo.write_text(
        ctx.repo.submissions / f"{result_id}.md",
        f"# {task.id}\n\nIssue #{number} · {ctx.today.isoformat()} · {result.correct}/{result.total}\n\n"
        + "\n".join(f"{n}. {a}" for n, a in sorted(answers.items()))
        + "\n",
    )

    explanations, no_ai = None, None
    if result.wrong:
        no_ai = ctx.llm.unavailable_reason("explainer")
        if no_ai is None:
            try:
                explanations = explain_reading(ctx.llm, ctx.repo, ctx.config, task, key, result)
            except LLMUnavailable as exc:
                no_ai = str(exc)
            except LLMError:
                no_ai = "error"

    traps = {e["number"]: e["trap"] for e in (explanations or {}).get("explanations", [])}
    bank_errors = []
    for item in result.wrong:
        category = traps.get(item.number) or (item.reason if item.reason in ("word_limit", "spelling") else None)
        if category:
            bank_errors.append({"category": category, "quote": item.given, "correction": " / ".join(item.accepted)})
    ctx.store.update_error_bank("reading", bank_errors, result_id, ctx.today)
    ctx.store.add_result(
        {
            "id": result_id,
            "issue": number,
            "module": "reading",
            "task": task.id,
            "kind": task.kind,
            "date": ctx.today.isoformat(),
            "checked_at": now_iso(),
            "correct": result.correct,
            "total": result.total,
            "raw40": raw40,
            "band": band,
            "by_type": result.accuracy_by_type(),
            "minutes": str(values.get("minutes") or "").strip() or None,
            "errors": [e["category"] for e in bank_errors],
            "model": (explanations or {}).get("model"),
        }
    )
    comment = feedback.reading_comment(
        task.title, task.id, result, raw40, band, explanations, ctx.next_line(), no_ai
    )
    ctx.repo.write_text(ctx.repo.reports / f"{result_id}.md", comment + "\n")
    ctx.gh.comment(number, comment)
    ctx.gh.add_labels(number, ["checked"])
    ctx.gh.close_issue(number)
    return f"reading: {result.correct}/{result.total}"


# --- вопросы наставнику ----------------------------------------------------------------------
def ask(ctx: Context, number: int, question: str, context: str) -> str:
    if not question.strip():
        return "question: пусто"
    reason = ctx.llm.unavailable_reason("coach")
    if reason:
        ctx.gh.comment(number, feedback.NO_AI.get(reason, feedback.NO_AI["error"]))
        return f"question: без ИИ ({reason})"
    try:
        answer = answer_question(ctx.llm, ctx.repo, ctx.config, question, context)
    except (LLMUnavailable, LLMError):
        ctx.gh.comment(number, feedback.NO_AI["error"])
        return "question: ошибка ИИ"
    ctx.gh.comment(number, f"{answer}\n\n<sub>Ответ ИИ-наставника · {ctx.llm.describe('coach')}</sub>")
    return "question: ok"


# --- по расписанию ---------------------------------------------------------------------------
def daily(ctx: Context) -> str:
    profile = ctx.store.profile()
    if not profile:
        return "daily: нет профиля — сначала анкета «Старт»"
    exclude: set[str] = set()
    for issue in ctx.gh.open_issues("task"):
        created = str(issue.get("created_at") or "")[:10]
        task_id = issueforms.task_marker(issue.get("body") or "")
        if created == ctx.today.isoformat():
            return "daily: задание на сегодня уже есть"
        age = (ctx.today - date.fromisoformat(created)).days if created else 0
        if age >= STALE_TASK_DAYS and "Диагностика" not in str(issue.get("title", "")):
            ctx.gh.comment(issue["number"], "Задание устарело и закрыто автоматически — вместо него пришло новое.")
            ctx.gh.close_issue(issue["number"])
            continue
        if task_id:
            exclude.add(task_id)

    choice = planner.choose_next(profile, ctx.store.results(), ctx.index, ctx.today, ctx.config, exclude)
    if choice is None:
        return "daily: нет подходящих заданий"
    body = feedback.task_issue_body(
        choice.task, choice.reason, ctx.gh.blob_url(choice.task.rel_path), ctx.site_task_url(choice.task),
        ctx.form_for(choice.task), light=choice.light,
    )
    title = f"Задание на {ctx.today.strftime('%d.%m')}: {choice.task.title}"
    number = ctx.gh.create_issue(title, body, ["task"])
    return f"daily: #{number} {choice.task.id}"


def weekly(ctx: Context) -> str:
    profile = ctx.store.profile()
    if not profile:
        return "weekly: нет профиля"
    results = ctx.store.results()
    stats = planner.week_stats(results, ctx.today)
    categories = ctx.repo.rubric_yaml("error_categories.yml")

    def name(cat: str) -> str:
        return categories["writing"].get(cat) or categories["reading"].get(cat) or cat

    lines = [
        f"## Итоги недели {stats['start'].strftime('%d.%m')}–{stats['end'].strftime('%d.%m')}",
        "",
        f"Работ: **{stats['count']}** (Writing {stats['by_module']['writing']}, Reading {stats['by_module']['reading']}),"
        f" дней с занятиями: **{stats['active_days']}** из 7.",
        "",
        "| Модуль | Эта неделя | Прошлая неделя |",
        "| --- | --- | --- |",
    ]
    for module in ("writing", "reading"):
        now, before = stats["bands"][module]
        lines.append(f"| {module.capitalize()} | {'—' if now is None else f'{now:.1f}'} | {'—' if before is None else f'{before:.1f}'} |")
    lines.append("")
    if stats["top_errors"]:
        lines += ["**Частые ошибки недели:** " + ", ".join(f"{name(c)} ({n})" for c, n in stats["top_errors"]), ""]
    if stats["count"] == 0:
        lines += ["На этой неделе работ не было. Ничего страшного — начните с одного короткого задания завтра.", ""]
    lines += ["### На следующую неделю", ctx.next_line(), ""]

    if stats["count"] and ctx.llm.available("coach"):
        try:
            summary = "\n".join(lines)
            advice = answer_question(
                ctx.llm, ctx.repo, ctx.config,
                "Based on this weekly summary, give 3 short, concrete recommendations for next week.",
                context=summary,
            )
            lines += ["### Совет наставника", advice, ""]
        except (LLMUnavailable, LLMError):
            pass
    body = "\n".join(lines)
    iso = ctx.today.isocalendar()
    ctx.repo.write_text(ctx.repo.plan / "weeks" / f"{iso.year}-W{iso.week:02d}.md", body + "\n")
    for issue in ctx.gh.open_issues("weekly"):
        ctx.gh.close_issue(issue["number"])
    number = ctx.gh.create_issue(f"Итоги недели {stats['end'].strftime('%d.%m')}", body, ["weekly"])
    return f"weekly: #{number}"


def dashboard(ctx: Context) -> str:
    progress.write_dashboard(ctx.repo, ctx.config, ctx.today)
    return "dashboard: ok"
