from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plan_gh_backlog.github import GitHubClient, Response
from plan_gh_backlog.managed import ManagedContentError, managed_block, marker_ids, replace_managed
from plan_gh_backlog.models import BacklogError
from plan_gh_backlog.parser import parse_text
from plan_gh_backlog.publisher import PublishError, publish
from plan_gh_backlog.scheduler import build_plan
from plan_gh_backlog.validation import validate

ROOT = Path(__file__).resolve().parents[1]
VALID = (ROOT / "examples" / "backlog.md").read_text(encoding="utf-8")


class ParserValidationTests(unittest.TestCase):
    def test_complete_example_parses_and_validates(self) -> None:
        backlog = parse_text(VALID)
        validate(backlog)
        self.assertEqual([epic.id for epic in backlog.epics], ["E-01", "E-02"])
        self.assertEqual(len(backlog.tasks), 5)

    def test_parser_reports_actionable_source_line(self) -> None:
        broken = VALID.replace("- Owner: api-team\n", "", 1)
        with self.assertRaises(BacklogError) as caught:
            parse_text(broken)
        diagnostic = next(item for item in caught.exception.diagnostics if item.code == "required-field")
        self.assertGreater(diagnostic.line, 1)
        self.assertIn("Owner", diagnostic.message)

    def test_duplicate_ids_and_missing_dependencies_are_rejected(self) -> None:
        duplicate = VALID.replace("### Task `E-02.1`: Document the service contract", "### Task `E-01.1`: Document the service contract")
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(duplicate))
        self.assertIn("duplicate-id", {item.code for item in caught.exception.diagnostics})

        missing = VALID.replace("- Dependencies: `E-01.1`", "- Dependencies: `DOES-NOT-EXIST`", 1)
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(missing))
        self.assertIn("reference", {item.code for item in caught.exception.diagnostics})

    def test_self_future_and_cycles_are_rejected(self) -> None:
        self_dep = VALID.replace("- Dependencies: none\n- Conflict group: identity-core", "- Dependencies: `E-01.1`\n- Conflict group: identity-core", 1)
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(self_dep))
        self.assertIn("self-dependency", {item.code for item in caught.exception.diagnostics})

        future = VALID.replace("- Dependencies: none\n- Conflict group: identity-core", "- Dependencies: `E-01.3`\n- Conflict group: identity-core", 1)
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(future))
        self.assertIn("future-dependency", {item.code for item in caught.exception.diagnostics})

        cycle = VALID.replace("- Dependencies: none\n- Conflict group: identity-core", "- Dependencies: `E-01.2`\n- Conflict group: identity-core", 1)
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(cycle))
        self.assertIn("cycle", {item.code for item in caught.exception.diagnostics})

    def test_max_parallel_range(self) -> None:
        broken = VALID.replace("- Max parallel: 3", "- Max parallel: 65")
        with self.assertRaises(BacklogError) as caught:
            validate(parse_text(broken))
        self.assertIn("range", {item.code for item in caught.exception.diagnostics})


class SchedulingTests(unittest.TestCase):
    def test_cap_dependencies_waves_and_conflicts(self) -> None:
        backlog = parse_text(VALID)
        batches = build_plan(backlog, max_parallel=2)
        self.assertEqual(batches[0].task_ids, ("E-01.1", "E-02.1"))
        self.assertTrue(all(len(batch.task_ids) <= 2 for batch in batches))
        positions = {task_id: batch.index for batch in batches for task_id in batch.task_ids}
        self.assertLess(positions["E-01.1"], positions["E-01.2"])
        self.assertLess(positions["E-01.2"], positions["E-01.3"])
        self.assertNotEqual(positions["E-01.3"], positions["E-02.2"])
        self.assertTrue(all(batch.wave == 1 for batch in batches[:2]))


class ManagedContentTests(unittest.TestCase):
    def test_replacement_preserves_user_content_outside_block(self) -> None:
        original = "User introduction\n\n" + managed_block("E-01.1", "old") + "\n\nUser footer"
        updated = replace_managed(original, "E-01.1", "new")
        self.assertTrue(updated.startswith("User introduction\n\n"))
        self.assertTrue(updated.endswith("\n\nUser footer"))
        self.assertIn("new", updated)
        self.assertNotIn("\nold\n", updated)
        self.assertEqual(marker_ids(updated), ["E-01.1"])

    def test_conflicting_or_malformed_markers_stop(self) -> None:
        with self.assertRaises(ManagedContentError):
            replace_managed("<!-- plan-gh-backlog:id=A -->", "A", "new")
        with self.assertRaises(ManagedContentError):
            replace_managed(managed_block("A", "x") + "\n<!-- plan-gh-backlog:id=B -->", "A", "new")


class GitHubClientTests(unittest.TestCase):
    def test_pagination_follows_next_link(self) -> None:
        calls: list[str] = []

        def transport(url: str, method: str, headers: dict[str, str], body: bytes | None) -> Response:
            calls.append(url)
            if "page=2" in url:
                return Response(200, {}, b'[{"id":2}]')
            return Response(200, {"Link": '<https://api.github.test/items?page=2>; rel="next"'}, b'[{"id":1}]')

        client = GitHubClient("secret", transport=transport, api_url="https://api.github.test")
        self.assertEqual(client.paginate("/items"), [{"id": 1}, {"id": 2}])
        self.assertEqual(len(calls), 2)

    def test_rate_limit_retry_uses_retry_after(self) -> None:
        responses = [
            Response(429, {"Retry-After": "3"}, b'{"message":"slow down"}'),
            Response(200, {}, b'{"ok":true}'),
        ]
        sleeps: list[float] = []
        client = GitHubClient("secret", transport=lambda *_: responses.pop(0), sleeper=sleeps.append)
        self.assertEqual(client.request("GET", "/ok"), {"ok": True})
        self.assertEqual(sleeps, [3.0])


class BombClient:
    def __getattr__(self, name: str):
        raise AssertionError(f"dry run attempted network via {name}")


class FakeGitHub:
    def __init__(self) -> None:
        self.labels: list[dict] = []
        self.milestones: list[dict] = []
        self.issues: list[dict] = []
        self.subissues: dict[int, set[int]] = {}
        self.mutations: list[tuple[str, str]] = []

    def paginate(self, path: str) -> list[dict]:
        if path.endswith("/labels"):
            return [dict(item) for item in self.labels]
        if "/milestones?" in path:
            return [dict(item) for item in self.milestones]
        if "/issues?" in path:
            return [dict(item) for item in self.issues]
        if path.endswith("/sub_issues"):
            parent = int(path.split("/issues/")[1].split("/")[0])
            return [next(dict(item) for item in self.issues if item["number"] == number) for number in sorted(self.subissues.get(parent, set()))]
        raise AssertionError(path)

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if method == "GET" and path.startswith("/repos/"):
            return {"html_url": "https://github.test/acme/demo", "visibility": "private"}
        self.mutations.append((method, path))
        payload = payload or {}
        if method == "POST" and path.endswith("/labels"):
            item = {**payload, "url": "https://api.github.test/label"}
            self.labels.append(item)
            return dict(item)
        if method == "PATCH" and "/labels/" in path:
            item = next(label for label in self.labels if label["name"] == payload["name"])
            item.update(payload)
            return dict(item)
        if method == "POST" and path.endswith("/milestones"):
            number = len(self.milestones) + 1
            item = {**payload, "number": number, "html_url": f"https://github.test/milestone/{number}"}
            self.milestones.append(item)
            return dict(item)
        if method == "PATCH" and "/milestones/" in path:
            number = int(path.rsplit("/", 1)[1])
            item = next(value for value in self.milestones if value["number"] == number)
            item.update(payload)
            return dict(item)
        if method == "POST" and path.endswith("/issues"):
            number = len(self.issues) + 10
            item = {
                **payload,
                "number": number,
                "id": number + 1000,
                "html_url": f"https://github.test/issues/{number}",
                "labels": [{"name": label} for label in payload.get("labels", [])],
                "milestone": {"number": payload.get("milestone")},
            }
            self.issues.append(item)
            return dict(item)
        if method == "PATCH" and "/issues/" in path:
            number = int(path.rsplit("/", 1)[1])
            item = next(value for value in self.issues if value["number"] == number)
            item.update(payload)
            item["labels"] = [{"name": label} for label in payload.get("labels", [])]
            item["milestone"] = {"number": payload.get("milestone")}
            return dict(item)
        if method == "POST" and path.endswith("/sub_issues"):
            parent = int(path.split("/issues/")[1].split("/")[0])
            child = next(item for item in self.issues if item["id"] == payload["sub_issue_id"])
            self.subissues.setdefault(parent, set()).add(child["number"])
            return {"ok": True}
        raise AssertionError((method, path, payload))


class PublisherTests(unittest.TestCase):
    def test_dry_run_has_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = publish(parse_text(VALID), "acme/demo", report_path=Path(directory) / "report.json", client=BombClient())
        self.assertFalse(report["network_used"])
        self.assertGreater(report["summary"]["planned"], 0)

    def test_partial_rerun_is_idempotent(self) -> None:
        fake = FakeGitHub()
        backlog = parse_text(VALID)
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            first = publish(backlog, "acme/demo", apply=True, report_path=report_path, client=fake)
            mutation_count = len(fake.mutations)
            second = publish(backlog, "acme/demo", apply=True, report_path=report_path, client=fake)
            persisted = json.loads(report_path.read_text())
        self.assertEqual(len(fake.issues), 7)
        self.assertEqual(len(fake.mutations), mutation_count)
        self.assertEqual(first["summary"]["created"], 12)  # 4 labels + 1 milestone + 7 issues
        self.assertEqual(second["summary"]["updated"], 0)
        self.assertEqual(second["summary"]["skipped"], 12)
        self.assertEqual(len(second["native_subissues"]), 5)
        self.assertEqual(persisted["repo"], "acme/demo")
        task_body = next(item["body"] for item in fake.issues if marker_ids(item["body"]) == ["E-01.2"])
        self.assertRegex(task_body, r"#\d+ — `E-01.1`")

    def test_duplicate_remote_marker_stops_before_mutation(self) -> None:
        fake = FakeGitHub()
        body = managed_block("E-01", "x")
        fake.issues = [
            {"number": 1, "id": 101, "title": "one", "body": body, "html_url": "x", "labels": [], "milestone": None},
            {"number": 2, "id": 102, "title": "two", "body": body, "html_url": "y", "labels": [], "milestone": None},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PublishError):
                publish(parse_text(VALID), "acme/demo", apply=True, report_path=Path(directory) / "report.json", client=fake)
        self.assertEqual(fake.mutations, [])


if __name__ == "__main__":
    unittest.main()
