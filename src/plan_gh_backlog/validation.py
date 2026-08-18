from __future__ import annotations

import heapq
from collections import Counter
from datetime import date

from .models import Backlog, BacklogError, Diagnostic, Task

_MAX_PARALLEL = 64


def _duplicates(values: list[tuple[str, int]], kind: str) -> list[Diagnostic]:
    counts = Counter(value for value, _ in values)
    return [Diagnostic(line, f"duplicate {kind} ID {value!r}", "duplicate-id") for value, line in values if counts[value] > 1]


def validate(backlog: Backlog, max_parallel: int | None = None) -> None:
    diagnostics: list[Diagnostic] = []
    if backlog.version != 1:
        diagnostics.append(Diagnostic(1, f"unsupported Version {backlog.version}; expected 1", "version"))
    parallel = backlog.max_parallel if max_parallel is None else max_parallel
    if not 1 <= parallel <= _MAX_PARALLEL:
        diagnostics.append(Diagnostic(1, f"Max parallel must be between 1 and {_MAX_PARALLEL}, got {parallel}", "range"))

    diagnostics += _duplicates([(x.name.casefold(), x.source.line) for x in backlog.labels], "label (case-insensitive)")
    diagnostics += _duplicates([(x.id, x.source.line) for x in backlog.milestones], "milestone")
    diagnostics += _duplicates([(x.id, x.source.line) for x in backlog.epics], "epic")
    diagnostics += _duplicates([(x.id, x.source.line) for x in backlog.tasks], "task")

    all_managed = [(x.id, x.source.line) for x in (*backlog.milestones, *backlog.epics, *backlog.tasks)]
    counts = Counter(item_id for item_id, _ in all_managed)
    diagnostics += [Diagnostic(line, f"managed ID {item_id!r} is reused across resource types", "duplicate-id") for item_id, line in all_managed if counts[item_id] > 1]

    label_names = {x.name for x in backlog.labels}
    milestone_ids = {x.id for x in backlog.milestones}
    epic_by_id = {x.id: x for x in backlog.epics}
    task_by_id = {x.id: x for x in backlog.tasks}

    for milestone in backlog.milestones:
        if milestone.due:
            try:
                date.fromisoformat(milestone.due)
            except ValueError:
                diagnostics.append(Diagnostic(milestone.source.line, f"milestone Due must be YYYY-MM-DD or none, got {milestone.due!r}", "date"))

    for epic in backlog.epics:
        if epic.wave < 1:
            diagnostics.append(Diagnostic(epic.source.line, "Epic Wave must be at least 1", "range"))
        if epic.milestone not in milestone_ids:
            diagnostics.append(Diagnostic(epic.source.line, f"unknown milestone {epic.milestone!r}", "reference"))
        for label in epic.labels:
            if label not in label_names:
                diagnostics.append(Diagnostic(epic.source.line, f"unknown label {label!r}", "reference"))
        check_ids = [item.id for item in epic.checklist]
        diagnostics += _duplicates([(item.id, item.source.line) for item in epic.checklist], "checklist item")
        children = sorted(task.id for task in backlog.tasks if task.epic == epic.id)
        if sorted(check_ids) != children:
            missing = sorted(set(children) - set(check_ids))
            extra = sorted(set(check_ids) - set(children))
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if extra:
                detail.append(f"unknown {', '.join(extra)}")
            diagnostics.append(Diagnostic(epic.source.line, f"epic checklist does not match child tasks ({'; '.join(detail)})", "checklist"))
        for item in epic.checklist:
            task = task_by_id.get(item.id)
            if task and item.title != task.title:
                diagnostics.append(Diagnostic(item.source.line, f"checklist title for {item.id} must match task title {task.title!r}", "checklist-title"))

    for task in backlog.tasks:
        if task.wave < 1:
            diagnostics.append(Diagnostic(task.source.line, "Task Wave must be at least 1", "range"))
        epic = epic_by_id.get(task.epic)
        if not epic:
            diagnostics.append(Diagnostic(task.source.line, f"unknown parent epic {task.epic!r}", "reference"))
        else:
            if task.wave < epic.wave:
                diagnostics.append(Diagnostic(task.source.line, f"task wave {task.wave} cannot precede parent epic wave {epic.wave}", "wave"))
            if task.milestone != epic.milestone:
                diagnostics.append(Diagnostic(task.source.line, f"task milestone {task.milestone!r} must match parent epic milestone {epic.milestone!r}", "parent"))
        if task.milestone not in milestone_ids:
            diagnostics.append(Diagnostic(task.source.line, f"unknown milestone {task.milestone!r}", "reference"))
        for label in task.labels:
            if label not in label_names:
                diagnostics.append(Diagnostic(task.source.line, f"unknown label {label!r}", "reference"))
        if task.id in task.dependencies:
            diagnostics.append(Diagnostic(task.source.line, "task cannot depend on itself", "self-dependency"))
        for dependency in task.dependencies:
            upstream = task_by_id.get(dependency)
            if not upstream:
                diagnostics.append(Diagnostic(task.source.line, f"unknown dependency {dependency!r}", "reference"))
            elif upstream.wave > task.wave:
                diagnostics.append(Diagnostic(task.source.line, f"dependency {dependency} is in future wave {upstream.wave} (task is wave {task.wave})", "future-dependency"))
        for path in task.paths:
            if path.startswith("/") or ".." in path.split("/") or "\\" in path:
                diagnostics.append(Diagnostic(task.source.line, f"path ownership key must be relative POSIX syntax, got {path!r}", "path"))

    diagnostics.extend(_cycle_diagnostics(backlog.tasks))
    if diagnostics:
        raise BacklogError(diagnostics)


def _cycle_diagnostics(tasks: tuple[Task, ...]) -> list[Diagnostic]:
    by_id = {task.id: task for task in tasks}
    dependencies = {
        task.id: {dependency for dependency in task.dependencies if dependency in by_id}
        for task in tasks
    }
    dependents: dict[str, set[str]] = {task.id: set() for task in tasks}
    for item_id, upstream_ids in dependencies.items():
        for upstream_id in upstream_ids:
            dependents[upstream_id].add(item_id)
    indegree = {item_id: len(upstream_ids) for item_id, upstream_ids in dependencies.items()}
    ready = [item_id for item_id, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    completed: set[str] = set()
    while ready:
        item_id = heapq.heappop(ready)
        completed.add(item_id)
        for dependent in sorted(dependents[item_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    remaining = set(by_id) - completed
    if not remaining:
        return []

    # Following residual dependency edges must eventually enter a cycle.
    path: list[str] = []
    positions: dict[str, int] = {}
    current = min(remaining)
    while current not in positions:
        positions[current] = len(path)
        path.append(current)
        residual_dependencies = sorted(dependencies[current] & remaining)
        if not residual_dependencies:  # Defensive: Kahn's algorithm makes this unreachable.
            return [Diagnostic(by_id[current].source.line, "dependency graph could not be ordered", "cycle")]
        current = residual_dependencies[0]
    cycle = path[positions[current] :] + [current]
    return [Diagnostic(by_id[current].source.line, f"dependency cycle: {' -> '.join(cycle)}", "cycle")]
