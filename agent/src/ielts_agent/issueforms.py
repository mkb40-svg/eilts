"""Разбор тела Issue, созданного по форме (.github/ISSUE_TEMPLATE/*.yml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .repo import Repo

NO_RESPONSE = "_No response_"
_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)
_CHECKBOX = re.compile(r"^-\s*\[(x|X| )\]\s*(.+)$", re.M)
TASK_MARKER = re.compile(r"<!--\s*ielts-task:\s*([A-Za-z0-9\-]+)\s*-->")


@dataclass
class FormField:
    id: str
    label: str
    type: str
    required: bool


@dataclass
class Template:
    name: str  # имя файла без .yml: start, writing, reading, question
    labels: list[str]
    fields: list[FormField] = field(default_factory=list)

    @property
    def required_labels(self) -> list[str]:
        return [f.label for f in self.fields if f.required]


def load_templates(repo: Repo) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(repo.templates.glob("*.yml")):
        if path.stem == "config":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fields = []
        for item in data.get("body", []):
            if item.get("type") == "markdown" or not item.get("id"):
                continue
            attrs = item.get("attributes", {})
            fields.append(
                FormField(
                    id=item["id"],
                    label=str(attrs.get("label", item["id"])).strip(),
                    type=item.get("type", "input"),
                    required=bool((item.get("validations") or {}).get("required")),
                )
            )
        templates[path.stem] = Template(path.stem, list(data.get("labels") or []), fields)
    return templates


def _sections(body: str) -> dict[str, str]:
    body = (body or "").replace("\r\n", "\n")
    parts = _HEADING.split(body)
    # parts = [до первого заголовка, заголовок1, текст1, заголовок2, текст2, ...]
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def detect(body: str, labels: list[str], templates: dict[str, Template]) -> Template | None:
    """Форма определяется по меткам, а если их нет — по заголовкам полей."""
    for template in templates.values():
        if any(label in labels for label in template.labels):
            return template
    sections = _sections(body)
    best, best_score = None, 0
    for template in templates.values():
        required = template.required_labels
        if required and all(label in sections for label in required):
            if len(required) > best_score:
                best, best_score = template, len(required)
    return best


def parse(body: str, template: Template) -> dict[str, object]:
    sections = _sections(body)
    values: dict[str, object] = {}
    for f in template.fields:
        raw = sections.get(f.label)
        if raw is None or raw == NO_RESPONSE or raw == "":
            values[f.id] = None
            continue
        if f.type == "checkboxes":
            values[f.id] = [m.group(2).strip() for m in _CHECKBOX.finditer(raw) if m.group(1).lower() == "x"]
        else:
            values[f.id] = raw
    return values


def task_marker(text: str) -> str | None:
    match = TASK_MARKER.search(text or "")
    return match.group(1).upper() if match else None
