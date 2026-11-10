"""Командная строка агента: `python -m ielts_agent <команда>` (PYTHONPATH=agent/src)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import calibrate, commands, site
from .github import FakeGitHub, GitHub
from .llm import LLM
from .repo import Repo
from .validate import validate_content


def _github(dry_run: bool):
    return FakeGitHub(os.environ.get("GITHUB_REPOSITORY", "student/ielts-self-prep")) if dry_run else GitHub.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ielts_agent", description="Агент IELTS Self-Prep")
    parser.add_argument("--dry-run", action="store_true", help="не обращаться к GitHub API, печатать действия")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("event", help="обработать событие GitHub (Issue или комментарий)")
    ev.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    ev.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    sub.add_parser("daily", help="создать задание на день")
    sub.add_parser("weekly", help="итоги недели")
    sub.add_parser("dashboard", help="пересобрать PROGRESS.md и график")
    bs = sub.add_parser("build-site", help="собрать сайт для GitHub Pages")
    bs.add_argument("--out", default="_site")
    sub.add_parser("validate", help="проверить учебные материалы")
    sub.add_parser("calibrate", help="сверить оценки ИИ с эталонными")
    args = parser.parse_args(argv)

    repo = Repo.discover()

    if args.command == "validate":
        problems = validate_content(repo)
        for p in problems:
            print(f"✗ {p}")
        print("Материалы в порядке." if not problems else f"Проблем: {len(problems)}")
        return 1 if problems else 0

    if args.command == "build-site":
        count = site.build(repo, Path(args.out), server=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
        print(f"Сайт собран: {count} заданий → {args.out}")
        return 0

    if args.command == "calibrate":
        config = repo.config()
        ok, report = calibrate.run(repo, config, LLM(repo, config))
        print(report)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            Path(summary).write_text(report + "\n", encoding="utf-8")
        return 0 if ok else 1

    gh = _github(args.dry_run)
    ctx = commands.Context(repo, gh)
    if args.command == "event":
        if not args.event_path or not args.event_name:
            print("Нужны GITHUB_EVENT_PATH и GITHUB_EVENT_NAME", file=sys.stderr)
            return 2
        event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
        outcome = commands.handle_event(ctx, args.event_name, event)
        commands.dashboard(ctx)
    elif args.command == "daily":
        outcome = commands.daily(ctx)
    elif args.command == "weekly":
        outcome = commands.weekly(ctx)
        commands.dashboard(ctx)
    else:
        outcome = commands.dashboard(ctx)
    print(outcome)
    if args.dry_run and isinstance(gh, FakeGitHub):
        for name, payload in gh.calls:
            print(f"--- {name}\n{json.dumps(payload, ensure_ascii=False, indent=2) if payload else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
