"""Минимальный клиент GitHub REST API (без внешних зависимостей) и его заглушка для тестов."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LABELS = {
    "start": ("0e8a16", "Анкета ученика"),
    "writing": ("1d76db", "Работа Writing"),
    "reading": ("5319e7", "Ответы Reading"),
    "question": ("fbca04", "Вопрос наставнику"),
    "task": ("c5def5", "Задание от наставника"),
    "diagnostic": ("bfdadc", "Диагностика"),
    "weekly": ("d4c5f9", "Итоги недели"),
    "checked": ("0e8a16", "Проверено агентом"),
    "needs-recheck": ("d93f0b", "Нужна повторная проверка"),
}


class GitHub:
    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com"):
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")

    @classmethod
    def from_env(cls) -> "GitHub":
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
        if not repository or not token:
            raise RuntimeError("Нужны переменные GITHUB_REPOSITORY и GITHUB_TOKEN")
        return cls(repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    def _request(self, method: str, path: str, data: dict | None = None) -> Any:
        url = f"{self.api_url}/repos/{self.repository}{path}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"GitHub API {method} {path}: {exc.code} {detail}") from exc
        return json.loads(raw) if raw else None

    # --- операции -------------------------------------------------------------
    def comment(self, issue: int, body: str) -> None:
        self._request("POST", f"/issues/{issue}/comments", {"body": body})

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        data = self._request("POST", "/issues", {"title": title, "body": body, "labels": labels})
        return int(data["number"])

    def add_labels(self, issue: int, labels: list[str]) -> None:
        self._request("POST", f"/issues/{issue}/labels", {"labels": labels})

    def close_issue(self, issue: int) -> None:
        self._request("PATCH", f"/issues/{issue}", {"state": "closed", "state_reason": "completed"})

    def open_issues(self, label: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"labels": label, "state": "open", "per_page": 100})
        return [i for i in self._request("GET", f"/issues?{query}") if "pull_request" not in i]

    def ensure_labels(self) -> None:
        existing = {l["name"] for l in self._request("GET", "/labels?per_page=100")}
        for name, (color, description) in LABELS.items():
            if name not in existing:
                try:
                    self._request("POST", "/labels", {"name": name, "color": color, "description": description})
                except RuntimeError:
                    pass  # метку могли создать параллельно

    # --- ссылки ---------------------------------------------------------------
    @property
    def repo_url(self) -> str:
        return f"{self.server_url}/{self.repository}"

    def blob_url(self, rel_path: str) -> str:
        return f"{self.repo_url}/blob/HEAD/{rel_path}"

    def form_url(self, template: str, **fields: str) -> str:
        return form_url(self.repo_url, template, **fields)


def form_url(repo_url: str, template: str, **fields: str) -> str:
    """Ссылка на новую Issue по форме с заполненными полями (параметры = id полей)."""
    params = {"template": f"{template}.yml", **{k: v for k, v in fields.items() if v}}
    return f"{repo_url}/issues/new?{urllib.parse.urlencode(params)}"


class FakeGitHub(GitHub):
    """Записывает действия вместо вызова API — для тестов и сухого прогона."""

    def __init__(self, repository: str = "student/ielts-self-prep"):
        super().__init__(repository, token="fake")
        self.server_url = "https://github.com"
        self.calls: list[tuple[str, Any]] = []
        self.issues: dict[int, dict[str, Any]] = {}
        self._next = 100

    def comment(self, issue: int, body: str) -> None:
        self.calls.append(("comment", {"issue": issue, "body": body}))

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        self._next += 1
        self.issues[self._next] = {"number": self._next, "title": title, "body": body, "labels": labels}
        self.calls.append(("create_issue", self.issues[self._next]))
        return self._next

    def add_labels(self, issue: int, labels: list[str]) -> None:
        self.calls.append(("add_labels", {"issue": issue, "labels": labels}))

    def close_issue(self, issue: int) -> None:
        self.calls.append(("close_issue", {"issue": issue}))

    def open_issues(self, label: str) -> list[dict[str, Any]]:
        return [
            {"number": n, "title": i["title"], "body": i["body"], "created_at": i.get("created_at", "")}
            for n, i in self.issues.items()
            if label in i["labels"]
        ]

    def ensure_labels(self) -> None:
        self.calls.append(("ensure_labels", None))

    def comments(self) -> list[str]:
        return [c[1]["body"] for c in self.calls if c[0] == "comment"]
