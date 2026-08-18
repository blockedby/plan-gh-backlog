from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Callable

from .github import GitHubClient, GitHubError
from .managed import ManagedContentError, managed_block, marker_ids, replace_managed
from .models import Backlog, Epic, Milestone, Task
from .validation import validate

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class PublishError(RuntimeError):
    pass


def _write_report(path: str | Path, report: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record(kind: str, item_id: str, title: str, status: str, **extra: object) -> dict:
    return {"kind": kind, "id": item_id, "title": title, "status": status, **extra}


def _issue_content(item: Epic | Task, numbers: dict[str, int], tasks: dict[str, Task]) -> str:
    lines = [item.body, "", "---", "", "### Managed backlog metadata"]
    if isinstance(item, Epic):
        lines += [
            f"- **ID:** `{item.id}`",
            f"- **Milestone:** `{item.milestone}`",
            f"- **Wave:** {item.wave}",
            f"- **Area:** {item.area}",
            f"- **Owner:** {item.owner}",
            "",
            "### Tasks",
        ]
        for check in item.checklist:
            lines.append(f"- [{'x' if check.checked else ' '}] #{numbers[check.id]} — `{check.id}` {check.title}")
    else:
        lines += [
            f"- **ID:** `{item.id}`",
            f"- **Epic:** #{numbers[item.epic]} (`{item.epic}`)",
            f"- **Milestone:** `{item.milestone}`",
            f"- **Wave:** {item.wave}",
            f"- **Area:** {item.area}",
            f"- **Owner:** {item.owner}",
            f"- **Conflict group:** {f'`{item.conflict_group}`' if item.conflict_group else 'none'}",
            f"- **Paths:** {', '.join(f'`{path}`' for path in item.paths) if item.paths else 'none'}",
            "",
            "### Dependencies",
        ]
        if item.dependencies:
            for dependency in item.dependencies:
                lines.append(f"- #{numbers[dependency]} — `{dependency}` {tasks[dependency].title}")
        else:
            lines.append("- None")
    return "\n".join(lines).strip()


def _milestone_content(item: Milestone) -> str:
    return f"{item.body}\n\n---\n\nManaged milestone ID: `{item.id}`"


def _remote_by_marker(items: list[dict], body_key: str, kind: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for item in items:
        body = item.get(body_key) or ""
        ids = marker_ids(body)
        if len(ids) > 1:
            raise PublishError(f"remote {kind} {item.get('number', item.get('title'))} has duplicate/conflicting managed markers: {ids}")
        if not ids:
            continue
        item_id = ids[0]
        if item_id in found:
            raise PublishError(f"managed marker {item_id!r} appears on multiple remote {kind}s")
        try:
            replace_managed(body, item_id, "preflight")
        except ManagedContentError as error:
            raise PublishError(f"remote {kind} marker {item_id!r} has malformed managed content: {error}") from error
        found[item_id] = item
    return found


def _ensure_repository(client: GitHubClient, repo: str, create_repo: bool, visibility: str | None) -> dict:
    try:
        repository = client.request("GET", f"/repos/{repo}")
        if create_repo:
            # Existing repositories are intentionally reused, never recreated or visibility-changed.
            return repository  # type: ignore[return-value]
        return repository  # type: ignore[return-value]
    except GitHubError as error:
        if "returned 404" not in str(error) or not create_repo:
            raise
    owner, name = repo.split("/", 1)
    user = client.request("GET", "/user")
    owner_info = client.request("GET", f"/users/{urllib.parse.quote(owner)}")
    payload = {"name": name, "private": visibility == "private"}
    if owner_info.get("type") == "Organization":  # type: ignore[union-attr]
        return client.request("POST", f"/orgs/{urllib.parse.quote(owner)}/repos", payload)  # type: ignore[return-value]
    if user.get("login", "").lower() != owner.lower():  # type: ignore[union-attr]
        raise PublishError(f"cannot create user repository for {owner!r}: authenticated user differs")
    return client.request("POST", "/user/repos", payload)  # type: ignore[return-value]


def publish(
    backlog: Backlog,
    repo: str,
    *,
    apply: bool = False,
    create_repo: bool = False,
    visibility: str | None = None,
    report_path: str | Path = ".plan-gh-backlog-report.json",
    client: GitHubClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    validate(backlog)
    if not _REPO_RE.match(repo):
        raise PublishError("repo must be exactly OWNER/REPO")
    if create_repo and not apply:
        raise PublishError("--create-repo requires --apply")
    if create_repo and visibility not in {"public", "private"}:
        raise PublishError("--create-repo requires --visibility public|private")
    if visibility and not create_repo:
        raise PublishError("--visibility is only valid with --create-repo")

    report: dict = {
        "schema_version": 1,
        "repo": repo,
        "apply": apply,
        "network_used": apply,
        "resources": [],
        "issues": {},
        "native_subissues": [],
        "summary": {},
    }
    resources: list[dict] = report["resources"]  # type: ignore[assignment]

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    if not apply:
        for label in sorted(backlog.labels, key=lambda item: item.name):
            resources.append(_record("label", label.name, label.name, "planned"))
        for milestone in sorted(backlog.milestones, key=lambda item: item.id):
            resources.append(_record("milestone", milestone.id, milestone.title, "planned"))
        for item in sorted((*backlog.epics, *backlog.tasks), key=lambda value: value.id):
            resources.append(_record("issue", item.id, item.title, "planned"))
            report["issues"][item.id] = {"number": None, "url": None, "status": "planned"}  # type: ignore[index]
        report["summary"] = {"planned": len(resources), "created": 0, "updated": 0, "skipped": 0}
        _write_report(report_path, report)
        return report

    github = client or GitHubClient(progress=progress)
    try:
        emit(f"repository: verifying {repo}")
        repository = _ensure_repository(github, repo, create_repo, visibility)
        report["repository_url"] = repository.get("html_url")
        emit(f"repository: ready {report['repository_url'] or repo}")

        # Read and validate all stable remote markers before the first backlog mutation.
        emit("preflight: loading remote issues and milestones")
        remote_issues = [item for item in github.paginate(f"/repos/{repo}/issues?state=all") if "pull_request" not in item]
        remote_milestones = github.paginate(f"/repos/{repo}/milestones?state=all")
        issues_by_id = _remote_by_marker(remote_issues, "body", "issue")
        milestones_by_id = _remote_by_marker(remote_milestones, "description", "milestone")
        cross_type_ids = sorted(set(issues_by_id) & set(milestones_by_id))
        if cross_type_ids:
            raise PublishError(f"managed IDs appear on both issues and milestones: {', '.join(cross_type_ids)}")
        expected_issue_ids = {item.id for item in (*backlog.epics, *backlog.tasks)}
        expected_milestone_ids = {item.id for item in backlog.milestones}
        wrong_issue_ids = sorted(set(issues_by_id) & expected_milestone_ids)
        wrong_milestone_ids = sorted(set(milestones_by_id) & expected_issue_ids)
        if wrong_issue_ids or wrong_milestone_ids:
            raise PublishError(
                "managed marker is attached to the wrong resource type: "
                f"issues={wrong_issue_ids}, milestones={wrong_milestone_ids}"
            )
        emit(
            f"preflight: {len(remote_issues)} issues, {len(remote_milestones)} milestones, "
            f"{len(issues_by_id)} managed issue markers"
        )

        remote_labels = {item["name"].casefold(): item for item in github.paginate(f"/repos/{repo}/labels")}
        sorted_labels = sorted(backlog.labels, key=lambda item: item.name)
        emit(f"labels: reconciling {len(sorted_labels)}")
        for index, label in enumerate(sorted_labels, start=1):
            desired = {"name": label.name, "color": label.color, "description": label.description}
            existing = remote_labels.get(label.name.casefold())
            if not existing:
                created = github.request("POST", f"/repos/{repo}/labels", desired)
                resources.append(_record("label", label.name, label.name, "created", url=created.get("url")))
            elif existing.get("color", "").upper() == label.color and (existing.get("description") or "") == label.description:
                resources.append(_record("label", label.name, label.name, "skipped", url=existing.get("url")))
            else:
                encoded = urllib.parse.quote(existing["name"], safe="")
                updated = github.request("PATCH", f"/repos/{repo}/labels/{encoded}", desired)
                resources.append(_record("label", label.name, label.name, "updated", url=updated.get("url")))
            _write_report(report_path, report)
            emit(f"labels {index}/{len(sorted_labels)}: {label.name} {resources[-1]['status']}")

        milestone_numbers: dict[str, int] = {}
        sorted_milestones = sorted(backlog.milestones, key=lambda item: item.id)
        emit(f"milestones: reconciling {len(sorted_milestones)}")
        for index, milestone in enumerate(sorted_milestones, start=1):
            existing = milestones_by_id.get(milestone.id)
            description = replace_managed(existing.get("description") if existing else None, milestone.id, _milestone_content(milestone))
            due_on = f"{milestone.due}T23:59:59Z" if milestone.due else None
            desired = {
                "title": milestone.title,
                "description": description,
            }
            if due_on is not None:
                desired["due_on"] = due_on
            if not existing:
                result = github.request("POST", f"/repos/{repo}/milestones", desired)
                status = "created"
            else:
                same = (
                    existing.get("title") == desired["title"]
                    and (existing.get("description") or "") == description
                    and (existing.get("due_on") or None) == due_on
                )
                if same:
                    result, status = existing, "skipped"
                else:
                    result = github.request("PATCH", f"/repos/{repo}/milestones/{existing['number']}", desired)
                    status = "updated"
            milestone_numbers[milestone.id] = result["number"]
            resources.append(_record("milestone", milestone.id, milestone.title, status, number=result["number"], url=result.get("html_url")))
            _write_report(report_path, report)
            emit(f"milestones {index}/{len(sorted_milestones)}: {milestone.id} {status}")

        # First pass ensures every managed issue has a stable issue number.
        numbers: dict[str, int] = {}
        urls: dict[str, str] = {}
        created_ids: set[str] = set()
        all_items = sorted((*backlog.epics, *backlog.tasks), key=lambda item: item.id)
        emit(f"issues: resolving stable numbers for {len(all_items)}")
        for index, item in enumerate(all_items, start=1):
            existing = issues_by_id.get(item.id)
            if existing:
                numbers[item.id] = existing["number"]
                urls[item.id] = existing["html_url"]
                report["issues"][item.id] = {"number": numbers[item.id], "url": urls[item.id], "status": "discovered"}  # type: ignore[index]
                _write_report(report_path, report)
                emit(f"issues resolve {index}/{len(all_items)}: {item.id} discovered #{numbers[item.id]}")
                continue
            placeholder = managed_block(item.id, f"{item.body}\n\nPublication is resolving managed links.")
            created = github.request("POST", f"/repos/{repo}/issues", {
                "title": item.title,
                "body": placeholder,
                "labels": list(item.labels),
                "milestone": milestone_numbers[item.milestone],
            })
            issues_by_id[item.id] = created
            numbers[item.id] = created["number"]
            urls[item.id] = created["html_url"]
            created_ids.add(item.id)
            report["issues"][item.id] = {"number": numbers[item.id], "url": urls[item.id], "status": "created-pending-links"}  # type: ignore[index]
            _write_report(report_path, report)
            emit(f"issues resolve {index}/{len(all_items)}: {item.id} created #{numbers[item.id]}")

        tasks = {task.id: task for task in backlog.tasks}
        emit(f"issues: reconciling managed fields for {len(all_items)}")
        for index, item in enumerate(all_items, start=1):
            existing = issues_by_id[item.id]
            body = replace_managed(existing.get("body"), item.id, _issue_content(item, numbers, tasks))
            desired_labels = sorted(item.labels)
            existing_labels = sorted(label["name"] for label in existing.get("labels", []))
            same = (
                existing.get("title") == item.title
                and (existing.get("body") or "") == body
                and existing_labels == desired_labels
                and existing.get("milestone", {}).get("number") == milestone_numbers[item.milestone]
            )
            if same:
                result = existing
                status = "created" if item.id in created_ids else "skipped"
            else:
                result = github.request("PATCH", f"/repos/{repo}/issues/{numbers[item.id]}", {
                    "title": item.title,
                    "body": body,
                    "labels": list(item.labels),
                    "milestone": milestone_numbers[item.milestone],
                })
                status = "created" if item.id in created_ids else "updated"
            entry = _record("issue", item.id, item.title, status, number=numbers[item.id], url=urls[item.id])
            resources.append(entry)
            report["issues"][item.id] = {"number": numbers[item.id], "url": urls[item.id], "status": status}  # type: ignore[index]
            _write_report(report_path, report)
            emit(f"issues reconcile {index}/{len(all_items)}: {item.id} {status} #{numbers[item.id]}")

        subissue_total = sum(len(epic.checklist) for epic in backlog.epics)
        subissue_index = 0
        emit(f"sub-issues: reconciling {subissue_total}")
        for epic in sorted(backlog.epics, key=lambda item: item.id):
            endpoint = f"/repos/{repo}/issues/{numbers[epic.id]}/sub_issues"
            existing_children = {item["number"] for item in github.paginate(endpoint)}
            for check in epic.checklist:
                subissue_index += 1
                child_number = numbers[check.id]
                if child_number in existing_children:
                    status = "skipped"
                else:
                    github.request("POST", endpoint, {"sub_issue_id": issues_by_id[check.id]["id"]})
                    status = "created"
                report["native_subissues"].append({  # type: ignore[union-attr]
                    "epic_id": epic.id,
                    "task_id": check.id,
                    "parent_number": numbers[epic.id],
                    "child_number": child_number,
                    "status": status,
                })
                _write_report(report_path, report)
                emit(
                    f"sub-issues {subissue_index}/{subissue_total}: "
                    f"{epic.id} -> {check.id} {status}"
                )

        counts = {status: sum(1 for item in resources if item["status"] == status) for status in ("created", "updated", "skipped")}
        report["summary"] = {"planned": 0, **counts}
        _write_report(report_path, report)
        emit(
            "complete: "
            f"created={counts['created']} updated={counts['updated']} skipped={counts['skipped']}"
        )
        return report
    except Exception as error:
        report["error"] = str(error)
        _write_report(report_path, report)
        emit(f"failed: {error}")
        raise
