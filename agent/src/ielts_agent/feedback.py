"""Тексты, которые агент публикует в Issues и в progress/reports/."""

from __future__ import annotations

from typing import Any

from .planner import Choice
from .reading import REASON_RU, TYPE_RU, ReadingResult
from .writing import CRITERIA, criterion_name

DISCLAIMER = "_Оценка ориентировочная и не является официальным результатом IELTS._"

NO_AI = {
    "no_key": (
        "ИИ для этой роли не подключён: в Settings → Secrets and variables → Actions нужны секрет "
        "`AZURE_OPENAI_API_KEY` и переменная `AZURE_OPENAI_ENDPOINT` (README, раздел «Подключение ИИ»)."
    ),
    "budget": "Месячный лимит расходов на ИИ (`budget_usd_month` в `config.yml`) исчерпан — ИИ включится в новом месяце.",
    "error": "ИИ не смог выполнить разбор (отказ модели или техническая ошибка). Работа сохранена; повторите отправку позже.",
}


def next_action(choice: Choice | None, form_link: str | None = None) -> str:
    if choice is None:
        return "Новых заданий пока нет — повторите любое из пройденных."
    line = f"**{choice.task.id}** — {choice.task.title} ({choice.task.time_minutes} мин). {choice.reason}"
    return line + (f" [Открыть форму ответа]({form_link})" if form_link else "")


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


# --- Writing ------------------------------------------------------------------------------
def writing_comment(
    title: str,
    words: int,
    min_words: int,
    assessment: dict[str, Any] | None,
    repeats: dict[str, int],
    categories: dict[str, str],
    max_errors: int,
    next_line: str,
    no_ai: str | None = None,
) -> str:
    lines = []
    head = f"## {title}"
    if assessment:
        head += f" · ориентировочно {assessment['band']:.1f}"
    lines += [head, ""]
    short = words < min_words
    lines.append(f"Слов: **{words}** (минимум {min_words})" + (" — **недобор**, это снижает балл за выполнение задания." if short else "."))
    lines.append("")

    if not assessment:
        lines += [NO_AI.get(no_ai or "no_key", NO_AI["error"]), "", "### Следующее действие", next_line, ""]
        return "\n".join(lines)

    task_type = assessment["task_type"]
    lines += ["| Критерий | Band | Главное |", "| --- | --- | --- |"]
    for key in CRITERIA:
        comment = assessment["comments"][key].replace("\n", " ").replace("|", "/")
        lines.append(f"| {criterion_name(key, task_type)} | {assessment['criteria'][key]:.1f} | {comment} |")
    lines.append("")
    if assessment.get("injection_detected"):
        lines += ["> В тексте есть фраза, похожая на попытку повлиять на оценку. Она проигнорирована.", ""]
    if assessment.get("strengths"):
        lines += ["### Что получилось", *[f"- {s}" for s in assessment["strengths"]], ""]
    errors = assessment.get("errors", [])
    if errors:
        lines.append(f"### Ошибки ({min(len(errors), max_errors)} из {len(errors)}, по важности)")
        for i, err in enumerate(errors[:max_errors], 1):
            cat = err.get("category", "other")
            seen = repeats.get(cat, 0)
            repeat = f" · повторяется {seen}-й раз" if seen >= 2 else ""
            lines.append(
                f"{i}. «{err['quote']}» → «{err['correction']}» — {categories.get(cat, cat)}{repeat}. {err['explanation']}"
            )
        lines.append("")
    if assessment.get("priorities"):
        lines += ["### Три приоритета на следующую работу", *[f"{i}. {p}" for i, p in enumerate(assessment["priorities"], 1)], ""]
    sample = assessment.get("sample") or {}
    if sample.get("improved"):
        lines += [
            "<details><summary>Как можно улучшить один абзац</summary>",
            "",
            f"Ваш абзац начинается с: «{sample.get('original_quote', '')}»",
            "",
            f"> {sample['improved']}",
            "",
            "</details>",
            "",
        ]
    lines += ["### Следующее действие", next_line, ""]
    meta = f"Модель: {assessment['model']} · прогонов оценки: {assessment['runs']} · уверенность {assessment['confidence']:.2f}"
    lines += [DISCLAIMER, f"<sub>{meta}</sub>"]
    return "\n".join(lines)


# --- Reading ------------------------------------------------------------------------------
def reading_comment(
    task_title: str,
    task_id: str,
    result: ReadingResult,
    raw40: int,
    band: float,
    explanations: dict[str, Any] | None,
    next_line: str,
    no_ai: str | None = None,
) -> str:
    lines = [
        f"## Reading · {task_id} · {result.correct} из {result.total}",
        "",
        f"Ориентировочно **{band:.1f}** (≈ {raw40}/40 в пересчёте на полный тест; по одному тексту оценка грубая).",
        "",
    ]
    if result.wrong:
        by_number = {e["number"]: e for e in (explanations or {}).get("explanations", [])}
        lines += ["| № | Ваш ответ | Верно | Что случилось |", "| --- | --- | --- | --- |"]
        for item in result.wrong:
            lines.append(
                f"| {item.number} | {item.given or '—'} | {' / '.join(item.accepted)} | {REASON_RU.get(item.reason, item.reason)} |"
            )
        lines.append("")
        if by_number:
            lines.append("### Почему так")
            for item in result.wrong:
                exp = by_number.get(item.number)
                if not exp:
                    continue
                lines.append(f"**{item.number}.** {exp['why']}")
                if exp.get("evidence"):
                    lines.append(f"> {exp['evidence']}")
                lines.append("")
            if explanations.get("tip"):
                lines += ["### Стратегия", explanations["tip"], ""]
        elif no_ai:
            lines += [NO_AI.get(no_ai, NO_AI["error"]), ""]
    else:
        lines += ["Все ответы верные — отличная работа.", ""]

    acc = result.accuracy_by_type()
    lines += ["| Тип вопросов | Верно |", "| --- | --- |"]
    for qtype, (correct, total) in sorted(acc.items()):
        lines.append(f"| {TYPE_RU.get(qtype, qtype)} | {correct} из {total} |")
    lines += ["", "### Следующее действие", next_line, "", DISCLAIMER]
    return "\n".join(lines)


def task_issue_body(task, choice_reason: str, material_link: str, site_link: str | None, form_link: str,
                    light: bool = False) -> str:
    how = (
        "Ответьте **комментарием в этой Issue**: по одному ответу на строку, например `1. TRUE`."
        if task.module == "reading"
        else "Ответьте **комментарием в этой Issue** — вставьте свой текст целиком."
    )
    lines = []
    if light:
        lines += ["> Вы давно не занимались — это короткое задание, чтобы вернуться в ритм.", ""]
    lines += [
        f"**{task.title}** · {task.time_minutes} минут",
        "",
        f"**Зачем:** {choice_reason}",
        "",
        f"**Материал:** [{task.rel_path}]({material_link})" + (f" · [открыть на сайте с таймером]({site_link})" if site_link else ""),
        "",
        f"**Как ответить:** {how} Или через [форму]({form_link}).",
        "",
    ]
    if task.module == "writing":
        lines += ["<details><summary>Текст задания</summary>", "", task.body.strip(), "", "</details>", ""]
    lines.append(f"<!-- ielts-task: {task.id} -->")
    return "\n".join(lines)
