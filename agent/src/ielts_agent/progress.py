"""PROGRESS.md и SVG-график динамики — работают и в приватном репозитории без Pages (ФТ-37, 38)."""

from __future__ import annotations

from datetime import date
from html import escape

from . import planner
from .reading import TYPE_RU
from .repo import Repo
from .store import Store

COLORS = {"writing": "#2f6fd6", "reading": "#c2571a"}
NAMES = {"writing": "Writing", "reading": "Reading"}


def chart_svg(results: list[dict], target: float | None) -> str:
    width, height = 720, 300
    left, right, top, bottom = 48, 24, 36, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    lo, hi = 3.0, 9.0
    scored = [r for r in results if r.get("band") is not None]

    def y(band: float) -> float:
        return top + (hi - band) / (hi - lo) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'font-family="Segoe UI, Arial, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" rx="10" fill="#ffffff"/>',
        f'<text x="{left}" y="22" font-size="14" font-weight="600" fill="#1f2328">Динамика band по работам</text>',
    ]
    for band in range(3, 10):
        yy = y(band)
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e6e8eb"/>')
        parts.append(f'<text x="{left - 10}" y="{yy + 4:.1f}" text-anchor="end" fill="#656d76">{band}</text>')
    if target:
        yy = y(target)
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#1a7f37" stroke-dasharray="6 4"/>'
        )
        parts.append(f'<text x="{width - right}" y="{yy - 6:.1f}" text-anchor="end" fill="#1a7f37">цель {target:.1f}</text>')
    if not scored:
        parts.append(
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" fill="#656d76">Пока нет оценённых работ</text>'
        )
    else:
        n = len(scored)
        step = plot_w / max(n - 1, 1)
        for module in ("writing", "reading"):
            points = [(left + i * step, y(r["band"])) for i, r in enumerate(scored) if r.get("module") == module]
            if not points:
                continue
            if len(points) > 1:
                path = " ".join(f"{x:.1f},{yy:.1f}" for x, yy in points)
                parts.append(f'<polyline points="{path}" fill="none" stroke="{COLORS[module]}" stroke-width="2.5"/>')
            for x, yy in points:
                parts.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="4" fill="{COLORS[module]}"/>')
        lx = left
        for module in ("writing", "reading"):
            parts.append(f'<rect x="{lx}" y="{height - 22}" width="12" height="12" rx="2" fill="{COLORS[module]}"/>')
            parts.append(f'<text x="{lx + 18}" y="{height - 12}" fill="#1f2328">{NAMES[module]}</text>')
            lx += 110
        parts.append(
            f'<text x="{width - right}" y="{height - 12}" text-anchor="end" fill="#656d76">работы по порядку, всего {n}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render(repo: Repo, today: date, budget: float) -> str:
    store = Store(repo)
    profile = store.profile() or {}
    results = store.results()
    target = float(profile["target_band"]) if profile.get("target_band") else None
    current = planner.current_bands(results)

    lines = [
        "# Прогресс",
        "",
        f"_Обновлено {today.isoformat()}. Файл создаёт агент после каждой проверки — не правьте вручную._",
        "",
    ]
    if not profile:
        lines += ["Профиля ещё нет: откройте Issue по форме **«Старт: анкета ученика»**.", ""]

    lines += ["| Модуль | Сейчас | Цель | Разница | Работ |", "| --- | --- | --- | --- | --- |"]
    for module in ("writing", "reading"):
        now = current[module]
        gap = "—" if now is None or target is None else f"{now - target:+.1f}"
        count = sum(1 for r in results if r.get("module") == module)
        lines.append(
            f"| {NAMES[module]} | {'—' if now is None else f'{now:.1f}'} | "
            f"{'—' if target is None else f'{target:.1f}'} | {gap} | {count} |"
        )
    lines += ["| Listening | появится на этапе v1 | | | |", "| Speaking | появится на этапе v1 | | | |", ""]
    lines.append("«Сейчас» — медиана трёх последних оценённых работ модуля.")
    lines.append("")

    facts = [f"Серия: **{planner.streak(results, today)}** дн. подряд"]
    if profile.get("exam_date"):
        days = (date.fromisoformat(profile["exam_date"]) - today).days
        facts.insert(0, f"До экзамена: **{max(days, 0)}** дн.")
    lines += [" · ".join(facts), "", "![Динамика band](progress/charts/bands.svg)", ""]

    top = store.top_errors(limit=5)
    if top:
        categories = repo.rubric_yaml("error_categories.yml")
        lines += ["## Повторяющиеся ошибки", "", "| Ошибка | Модуль | Раз | Последний раз |", "| --- | --- | --- | --- |"]
        for e in top:
            name = categories.get(e["module"], {}).get(e["category"], e["category"])
            lines.append(f"| {name} | {NAMES.get(e['module'], e['module'])} | {e['count']} | {e.get('last_seen', '')} |")
        lines.append("")

    acc = planner.reading_accuracy(results)
    if acc:
        lines += ["## Reading: точность по типам вопросов", "", "| Тип | Верно | % |", "| --- | --- | --- |"]
        for qtype, (c, t) in sorted(acc.items(), key=lambda kv: kv[1][0] / kv[1][1]):
            lines.append(f"| {TYPE_RU.get(qtype, qtype)} | {c} из {t} | {c / t:.0%} |")
        lines.append("")

    if results:
        lines += ["## Последние работы", "", "| Дата | Задание | Результат | Issue |", "| --- | --- | --- | --- |"]
        for r in reversed(results[-10:]):
            if r.get("module") == "reading":
                res = f"{r.get('correct')}/{r.get('total')} · ≈{r['band']:.1f}"
            else:
                res = "без оценки ИИ" if r.get("band") is None else f"{r['band']:.1f}"
                res += f" · {r.get('words', 0)} слов"
            issue = f"#{r['issue']}" if r.get("issue") else ""
            lines.append(f"| {str(r.get('date', ''))[:10]} | {escape(str(r.get('task', '')))} | {res} | {issue} |")
        lines.append("")

    spent = store.month_spend()
    lines += [f"Расходы на ИИ в этом месяце: **${spent:.2f}** из ${budget:.2f}.", ""]
    return "\n".join(lines)


def write_dashboard(repo: Repo, config: dict, today: date) -> None:
    store = Store(repo)
    profile = store.profile() or {}
    target = float(profile["target_band"]) if profile.get("target_band") else None
    repo.write_text(repo.charts / "bands.svg", chart_svg(store.results(), target))
    repo.write_text(repo.root / "PROGRESS.md", render(repo, today, float(config["agent"].get("budget_usd_month", 0))))
