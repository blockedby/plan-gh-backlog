from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    line: int


@dataclass(frozen=True)
class Label:
    name: str
    color: str
    description: str
    source: Source


@dataclass(frozen=True)
class Milestone:
    id: str
    title: str
    due: str | None
    body: str
    source: Source


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    title: str
    checked: bool
    source: Source


@dataclass(frozen=True)
class Epic:
    id: str
    title: str
    milestone: str
    wave: int
    area: str
    owner: str
    labels: tuple[str, ...]
    checklist: tuple[ChecklistItem, ...]
    body: str
    source: Source


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    epic: str
    milestone: str
    wave: int
    area: str
    owner: str
    dependencies: tuple[str, ...]
    conflict_group: str | None
    paths: tuple[str, ...]
    labels: tuple[str, ...]
    body: str
    source: Source


@dataclass(frozen=True)
class Backlog:
    version: int
    max_parallel: int
    labels: tuple[Label, ...] = field(default_factory=tuple)
    milestones: tuple[Milestone, ...] = field(default_factory=tuple)
    epics: tuple[Epic, ...] = field(default_factory=tuple)
    tasks: tuple[Task, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Diagnostic:
    line: int
    message: str
    code: str = "invalid"

    def render(self, path: str) -> str:
        return f"{path}:{self.line}: error[{self.code}]: {self.message}"


class BacklogError(Exception):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = sorted(diagnostics, key=lambda d: (d.line, d.code, d.message))
        super().__init__("backlog validation failed")
