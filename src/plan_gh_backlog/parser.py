from __future__ import annotations

import re
from pathlib import Path

from .models import (
    Backlog,
    BacklogError,
    ChecklistItem,
    Diagnostic,
    Epic,
    Label,
    Milestone,
    Source,
    Task,
)

_HEADER_RE = re.compile(r"^# GitHub Backlog\s*$")
_BLOCK_RE = re.compile(r"^### (Milestone|Epic|Task) `([^`]+)`: (.+?)\s*$")
_FIELD_RE = re.compile(r"^- ([A-Za-z][A-Za-z -]*):\s*(.*?)\s*$")
_LABEL_RE = re.compile(r"^- `([^`]+)` \| `([0-9A-Fa-f]{6})` \| (.+?)\s*$")
_CHECK_RE = re.compile(r"^  - \[([ xX])\] `([^`]+)` (.+?)\s*$")
_SECTIONS = ("Labels", "Milestones", "Epics", "Tasks")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _csv(value: str) -> tuple[str, ...]:
    if value.lower() in {"", "none", "-"}:
        return ()
    result = []
    for item in value.split(","):
        item = item.strip()
        if item.startswith("`") and item.endswith("`"):
            item = item[1:-1]
        if item:
            result.append(item)
    return tuple(result)


def _value(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def _integer(value: str, line: int, name: str, diagnostics: list[Diagnostic]) -> int:
    try:
        return int(_value(value))
    except ValueError:
        diagnostics.append(Diagnostic(line, f"{name} must be an integer, got {value!r}", "integer"))
        return 0


def _body(lines: list[str], start: int, end: int, diagnostics: list[Diagnostic]) -> str:
    body_heading = next((i for i in range(start, end) if lines[i] == "#### Body"), None)
    if body_heading is None:
        diagnostics.append(Diagnostic(start + 1, "block requires a '#### Body' heading", "required-field"))
        return ""
    content = lines[body_heading + 1 : end]
    while content and not content[0].strip():
        content.pop(0)
    while content and not content[-1].strip():
        content.pop()
    if not content:
        diagnostics.append(Diagnostic(body_heading + 1, "Body must not be empty", "required-field"))
    return "\n".join(content)


def _fields(lines: list[str], start: int, end: int, diagnostics: list[Diagnostic]) -> tuple[dict[str, tuple[str, int]], int | None]:
    result: dict[str, tuple[str, int]] = {}
    body_heading = next((i for i in range(start, end) if lines[i] == "#### Body"), end)
    checklist_line = None
    for index in range(start, body_heading):
        text = lines[index]
        if not text.strip() or _CHECK_RE.match(text):
            continue
        match = _FIELD_RE.match(text)
        if not match:
            diagnostics.append(Diagnostic(index + 1, "expected '- Field: value' or '#### Body'", "syntax"))
            continue
        name, value = match.group(1).lower().replace(" ", "-"), match.group(2)
        if name in result:
            diagnostics.append(Diagnostic(index + 1, f"duplicate field {match.group(1)!r}", "duplicate-field"))
        else:
            result[name] = (value, index + 1)
        if name == "checklist":
            checklist_line = index
    return result, checklist_line


def _required(fields: dict[str, tuple[str, int]], names: tuple[str, ...], line: int, diagnostics: list[Diagnostic]) -> None:
    for name in names:
        if name not in fields or (name != "checklist" and not _value(fields[name][0])):
            diagnostics.append(Diagnostic(line, f"missing required field '- {name.replace('-', ' ').title()}: ...'", "required-field"))


def parse_text(text: str, path: str = "<input>") -> Backlog:
    lines = text.splitlines()
    diagnostics: list[Diagnostic] = []
    if not lines or not _HEADER_RE.match(lines[0]):
        diagnostics.append(Diagnostic(1, "first line must be '# GitHub Backlog'", "header"))

    section_at: dict[str, int] = {}
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line[3:].strip()
            if section in section_at:
                diagnostics.append(Diagnostic(i + 1, f"duplicate section '## {section}'", "duplicate-section"))
            section_at[section] = i
    for section in _SECTIONS:
        if section not in section_at:
            diagnostics.append(Diagnostic(1, f"missing section '## {section}'", "required-section"))
    present_order = [name for name, _ in sorted(section_at.items(), key=lambda pair: pair[1]) if name in _SECTIONS]
    if present_order != list(_SECTIONS):
        diagnostics.append(Diagnostic(1, f"sections must appear in order: {', '.join(_SECTIONS)}", "section-order"))

    first_section = min(section_at.values(), default=len(lines))
    top_fields, _ = _fields(lines, 1, first_section, diagnostics)
    _required(top_fields, ("version", "max-parallel"), 1, diagnostics)
    version = _integer(*top_fields.get("version", ("0", 1)), "Version", diagnostics)
    max_parallel = _integer(*top_fields.get("max-parallel", ("0", 1)), "Max parallel", diagnostics)

    labels: list[Label] = []
    if "Labels" in section_at:
        start = section_at["Labels"] + 1
        end = section_at.get("Milestones", len(lines))
        for i in range(start, end):
            if not lines[i].strip():
                continue
            match = _LABEL_RE.match(lines[i])
            if not match:
                diagnostics.append(Diagnostic(i + 1, "expected '- `name` | `RRGGBB` | description'", "label-syntax"))
            else:
                labels.append(Label(match.group(1), match.group(2).upper(), match.group(3), Source(i + 1)))

    milestones: list[Milestone] = []
    epics: list[Epic] = []
    tasks: list[Task] = []
    blocks: list[tuple[int, re.Match[str]]] = []
    for i, line in enumerate(lines):
        match = _BLOCK_RE.match(line)
        if match:
            blocks.append((i, match))
    blocks.append((len(lines), re.match(r"", "")))  # sentinel

    for block_index in range(len(blocks) - 1):
        start, match = blocks[block_index]
        next_block = blocks[block_index + 1][0]
        next_section = next((i for i in sorted(section_at.values()) if start < i < next_block), next_block)
        end = min(next_block, next_section)
        kind, item_id, title = match.group(1), match.group(2), match.group(3)
        expected_section = {"Milestone": "Milestones", "Epic": "Epics", "Task": "Tasks"}[kind]
        section_start = section_at.get(expected_section, len(lines))
        following_sections = [i for i in section_at.values() if i > section_start]
        section_end = min(following_sections, default=len(lines))
        if not section_start < start < section_end:
            diagnostics.append(Diagnostic(start + 1, f"{kind} block must be inside '## {expected_section}'", "section"))
        if not _ID_RE.match(item_id):
            diagnostics.append(Diagnostic(start + 1, f"invalid managed ID {item_id!r}", "id-format"))
        fields, checklist_at = _fields(lines, start + 1, end, diagnostics)
        body = _body(lines, start + 1, end, diagnostics)
        if kind == "Milestone":
            _required(fields, ("due",), start + 1, diagnostics)
            due_value = _value(fields.get("due", ("none", start + 1))[0])
            milestones.append(Milestone(item_id, title, None if due_value.lower() == "none" else due_value, body, Source(start + 1)))
        elif kind == "Epic":
            required = ("milestone", "wave", "area", "owner", "labels", "checklist")
            _required(fields, required, start + 1, diagnostics)
            checklist: list[ChecklistItem] = []
            if checklist_at is not None:
                i = checklist_at + 1
                while i < end and (not lines[i].strip() or _CHECK_RE.match(lines[i])):
                    check = _CHECK_RE.match(lines[i])
                    if check:
                        checklist.append(ChecklistItem(check.group(2), check.group(3), check.group(1).lower() == "x", Source(i + 1)))
                    i += 1
            epics.append(Epic(
                item_id, title,
                _value(fields.get("milestone", ("", start + 1))[0]),
                _integer(*fields.get("wave", ("0", start + 1)), "Wave", diagnostics),
                _value(fields.get("area", ("", start + 1))[0]),
                _value(fields.get("owner", ("", start + 1))[0]),
                _csv(fields.get("labels", ("", start + 1))[0]), tuple(checklist), body, Source(start + 1),
            ))
        else:
            required = ("epic", "milestone", "wave", "area", "owner", "dependencies", "conflict-group", "paths", "labels")
            _required(fields, required, start + 1, diagnostics)
            conflict = _value(fields.get("conflict-group", ("none", start + 1))[0])
            tasks.append(Task(
                item_id, title,
                _value(fields.get("epic", ("", start + 1))[0]),
                _value(fields.get("milestone", ("", start + 1))[0]),
                _integer(*fields.get("wave", ("0", start + 1)), "Wave", diagnostics),
                _value(fields.get("area", ("", start + 1))[0]),
                _value(fields.get("owner", ("", start + 1))[0]),
                _csv(fields.get("dependencies", ("", start + 1))[0]),
                None if conflict.lower() in {"none", "-", ""} else conflict,
                _csv(fields.get("paths", ("", start + 1))[0]),
                _csv(fields.get("labels", ("", start + 1))[0]), body, Source(start + 1),
            ))

    if diagnostics:
        raise BacklogError(diagnostics)
    return Backlog(version, max_parallel, tuple(labels), tuple(milestones), tuple(epics), tuple(tasks))


def parse_file(path: str | Path) -> Backlog:
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BacklogError([Diagnostic(1, str(error), "io")]) from error
    return parse_text(text, str(file_path))
