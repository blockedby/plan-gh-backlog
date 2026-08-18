from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import Backlog, Task
from .validation import validate


@dataclass(frozen=True)
class Batch:
    index: int
    wave: int
    task_ids: tuple[str, ...]


def _path_root(path: str) -> str:
    parts = []
    for part in path.strip("/").split("/"):
        if any(char in part for char in "*?["):
            break
        parts.append(part)
    return "/".join(parts)


def paths_conflict(left: str, right: str) -> bool:
    left_root, right_root = _path_root(left), _path_root(right)
    if not left_root or not right_root:
        return True
    return left_root == right_root or left_root.startswith(right_root + "/") or right_root.startswith(left_root + "/")


def tasks_conflict(left: Task, right: Task) -> bool:
    if left.conflict_group and left.conflict_group == right.conflict_group:
        return True
    return any(paths_conflict(a, b) for a in left.paths for b in right.paths)


def build_plan(backlog: Backlog, max_parallel: int | None = None) -> list[Batch]:
    cap = backlog.max_parallel if max_parallel is None else max_parallel
    validate(backlog, cap)
    by_id = {task.id: task for task in backlog.tasks}
    completed: set[str] = set()
    batches: list[Batch] = []

    while len(completed) < len(backlog.tasks):
        current_wave = min(task.wave for task in backlog.tasks if task.id not in completed)
        ready = [
            task for task in backlog.tasks
            if task.id not in completed
            and task.wave == current_wave
            and set(task.dependencies) <= completed
        ]
        ready.sort(key=lambda task: task.id)
        selected: list[Task] = []
        for candidate in ready:
            if len(selected) >= cap:
                break
            if not any(tasks_conflict(candidate, chosen) for chosen in selected):
                selected.append(candidate)
        if not selected:
            # Validation proves a DAG. This guard catches scheduler regressions.
            raise RuntimeError(f"no dependency-ready task in wave {current_wave}")
        completed.update(task.id for task in selected)
        batches.append(Batch(len(batches) + 1, current_wave, tuple(task.id for task in selected)))
    return batches


def plan_dict(backlog: Backlog, max_parallel: int | None = None) -> dict:
    cap = backlog.max_parallel if max_parallel is None else max_parallel
    batches = build_plan(backlog, cap)
    tasks = {task.id: task for task in backlog.tasks}
    return {
        "schema_version": 1,
        "max_parallel": cap,
        "task_count": len(backlog.tasks),
        "batches": [
            {
                **asdict(batch),
                "task_ids": list(batch.task_ids),
                "tasks": [
                    {
                        "id": task_id,
                        "title": tasks[task_id].title,
                        "area": tasks[task_id].area,
                        "owner": tasks[task_id].owner,
                        "conflict_group": tasks[task_id].conflict_group,
                        "paths": list(tasks[task_id].paths),
                    }
                    for task_id in batch.task_ids
                ],
            }
            for batch in batches
        ],
        "note": "Batches are an implementation dispatch plan only; this CLI does not execute agents.",
    }
