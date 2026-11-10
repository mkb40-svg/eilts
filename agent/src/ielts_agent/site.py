"""Статический сайт для GitHub Pages: список заданий, текст с таймером и кнопка «Отправить ответ».

Сайт ничего не хранит и не знает ключей ИИ: кнопка открывает форму Issue с заполненным ID задания,
а отправляет её сам ученик, уже вошедший в GitHub. Ключи ответов на сайт не попадают.
"""

from __future__ import annotations

import os
import shutil
from html import escape
from pathlib import Path

import markdown

from .content import Task, load_tasks
from .github import form_url
from .repo import Repo

SECTIONS = [
    ("reading-academic", "Reading · Academic"),
    ("reading-general", "Reading · General Training"),
    ("task2", "Writing · Task 2"),
    ("task1-academic", "Writing · Task 1 Academic"),
    ("task1-general", "Writing · Task 1 General"),
]

CSS = """
:root{--bg:#f7f7f4;--card:#ffffff;--ink:#1f2328;--muted:#5d6670;--line:#e3e3dc;--accent:#2f6fd6;--accent-ink:#ffffff;--warn:#b35c00}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--ink:#e8eaed;--muted:#a3abb4;--line:#2e3238;--accent:#6ea0f5;--accent-ink:#0b1220;--warn:#f0a35c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 64px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:24px}
header a{color:var(--muted);text-decoration:none}h1{font-size:28px;margin:0}h2{font-size:20px;margin:32px 0 12px}
.lead{color:var(--muted);margin:0 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:inherit;text-decoration:none}
.card:hover{border-color:var(--accent)}.card .id{color:var(--muted);font-size:13px}.card .t{font-weight:600;margin:2px 0}
.bar{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;margin-bottom:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.timer{font:600 22px/1 ui-monospace,Consolas,monospace;min-width:84px}.timer.over{color:var(--warn)}
button,.btn{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:8px 12px;font:inherit;cursor:pointer;text-decoration:none}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);margin-left:auto}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 20px 20px;overflow-wrap:anywhere}
table{border-collapse:collapse;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:6px 10px}
.note{color:var(--muted);font-size:14px;margin-top:16px}
"""

TIMER_JS = """
(function(){var el=document.getElementById('t'),total=+el.dataset.minutes*60,left=total,id=null;
function show(){var s=Math.abs(left),m=Math.floor(s/60),r=s%60;el.textContent=(left<0?'+':'')+m+':'+(r<10?'0':'')+r;el.classList.toggle('over',left<0);}
document.getElementById('start').onclick=function(){if(id){clearInterval(id);id=null;this.textContent='Продолжить';return;}
this.textContent='Пауза';id=setInterval(function(){left--;show();},1000);};
document.getElementById('reset').onclick=function(){clearInterval(id);id=null;left=total;document.getElementById('start').textContent='Старт';show();};show();})();
"""


def _page(title: str, body: str, base: str = "") -> str:
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<header><a href='{base}index.html'><strong>IELTS Self-Prep</strong></a>"
        "<span class='lead'>подготовка без регистрации · проверка в GitHub</span></header>"
        f"{body}</div></body></html>"
    )


def _task_page(task: Task, repo_url: str) -> str:
    template = "reading" if task.module == "reading" else "writing"
    title = f"{'Reading' if task.module == 'reading' else 'Writing'}: {task.id}"
    send = form_url(repo_url, template, task_id=task.id, title=title) if repo_url else "#"
    html = markdown.markdown(task.body, extensions=["tables", "sane_lists"])
    hint = (
        "Ответы — по одному на строку: <code>1. TRUE</code>. Орфография важна, как на экзамене."
        if task.module == "reading"
        else f"Минимум {task.min_words} слов. Пишите без словаря — так оценка будет честнее."
    )
    body = (
        "<div class='bar'>"
        f"<span class='timer' id='t' data-minutes='{task.time_minutes}'></span>"
        "<button id='start'>Старт</button><button id='reset'>Сброс</button>"
        f"<a class='btn primary' href='{escape(send)}' target='_blank' rel='noopener'>Отправить ответ</a>"
        "</div>"
        f"<p class='lead'>{escape(task.id)} · {task.time_minutes} минут</p>"
        f"<article>{html}</article>"
        f"<p class='note'>{hint} Кнопка «Отправить ответ» откроет форму в вашем репозитории на GitHub; "
        "проверку и разбор пришлёт агент комментарием. Оценки ориентировочные, не официальные.</p>"
        f"<script>{TIMER_JS}</script>"
    )
    return _page(f"{task.id} · {task.title}", body, base="../")


def build(repo: Repo, out: Path, repository: str | None = None, server: str = "https://github.com") -> int:
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"{server.rstrip('/')}/{repository}" if repository else ""
    if out.exists():
        shutil.rmtree(out)
    (out / "tasks").mkdir(parents=True)
    index = load_tasks(repo)

    sections = []
    for kind, label in SECTIONS:
        tasks = index.by_kind(kind)
        if not tasks:
            continue
        cards = "".join(
            f"<a class='card' href='tasks/{t.id}.html'><div class='id'>{t.id} · {t.time_minutes} мин</div>"
            f"<div class='t'>{escape(t.title)}</div></a>"
            for t in tasks
        )
        sections.append(f"<h2>{label}</h2><div class='grid'>{cards}</div>")
        for t in tasks:
            (out / "tasks" / f"{t.id}.html").write_text(_task_page(t, repo_url), encoding="utf-8")

    start = form_url(repo_url, "start") if repo_url else "#"
    intro = (
        "<h1>Задания</h1>"
        "<p class='lead'>Выберите задание, включите таймер и отправьте ответ — ИИ-наставник проверит его "
        "в GitHub и пришлёт разбор. Ваш прогресс хранится в репозитории, а не на этом сайте.</p>"
        f"<p><a class='btn' href='{escape(start)}' target='_blank' rel='noopener'>Анкета «Старт»</a> "
        + (f"<a class='btn' href='{escape(repo_url)}/blob/HEAD/PROGRESS.md' target='_blank' rel='noopener'>Мой прогресс</a>"
           if repo_url else "")
        + "</p>"
    )
    (out / "index.html").write_text(_page("IELTS Self-Prep", intro + "".join(sections)), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return len(index)
