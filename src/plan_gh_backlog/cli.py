from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .github import GitHubError
from .models import BacklogError
from .parser import parse_file
from .publisher import PublishError, publish
from .scheduler import plan_dict
from .validation import validate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plan-gh-backlog",
        description="Validate, plan, and explicitly publish deterministic Markdown backlogs to GitHub.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser("validate", help="parse and validate a backlog without network access")
    validate_parser.add_argument("backlog", metavar="BACKLOG.md")

    plan_parser = commands.add_parser("plan", help="compute capped, conflict-safe implementation batches")
    plan_parser.add_argument("backlog", metavar="BACKLOG.md")
    plan_parser.add_argument("--max-parallel", type=int, help="override the document cap (1-64)")
    plan_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    publish_parser = commands.add_parser("publish", help="preview publication; writes require --apply")
    publish_parser.add_argument("backlog", metavar="BACKLOG.md")
    publish_parser.add_argument("--repo", required=True, metavar="OWNER/REPO")
    publish_parser.add_argument("--apply", action="store_true", help="perform serialized GitHub writes")
    publish_parser.add_argument("--create-repo", action="store_true", help="create the repository if absent (requires --apply and --visibility)")
    publish_parser.add_argument("--visibility", choices=("public", "private"))
    publish_parser.add_argument("--report", default=".plan-gh-backlog-report.json", metavar="PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        backlog = parse_file(args.backlog)
        if args.command == "validate":
            validate(backlog)
            print(f"Valid backlog: {len(backlog.epics)} epics, {len(backlog.tasks)} tasks, {len(backlog.milestones)} milestones.")
            return 0
        if args.command == "plan":
            plan = plan_dict(backlog, args.max_parallel)
            if args.json:
                print(json.dumps(plan, indent=2, sort_keys=True))
            else:
                print(f"Implementation plan: {plan['task_count']} tasks, max parallel {plan['max_parallel']}")
                for batch in plan["batches"]:
                    print(f"Batch {batch['index']} (wave {batch['wave']}): {', '.join(batch['task_ids'])}")
                print("Note: this plans dispatch only; it does not execute implementation agents.")
            return 0
        report = publish(
            backlog,
            args.repo,
            apply=args.apply,
            create_repo=args.create_repo,
            visibility=args.visibility,
            report_path=args.report,
        )
        mode = "Applied" if args.apply else "Dry run"
        print(f"{mode}: {args.repo}")
        print(f"Report: {Path(args.report)}")
        print(json.dumps(report["summary"], sort_keys=True))
        return 0
    except BacklogError as error:
        for diagnostic in error.diagnostics:
            print(diagnostic.render(args.backlog), file=sys.stderr)
        return 2
    except (PublishError, GitHubError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
