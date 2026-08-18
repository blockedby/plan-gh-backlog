---
name: plan-gh-backlog
description: Normalize large roadmaps into a deterministic Markdown backlog, validate IDs/dependencies/waves/checklists/DAGs, preview conflict-safe implementation batches, and safely publish labels, milestones, epic/task issues, dependency links, checklists, and native GitHub sub-issues. Use when an agent must prepare or publish a structured GitHub issue backlog without guessing from ambiguous prose.
license: MIT
compatibility: Python 3.11+; GitHub CLI authentication or GH_TOKEN is required only for publish --apply.
---

# Plan GitHub Backlog

Normalize free-form source material before publishing; never infer ambiguous tasks or dependencies. Read [the schema](references/schema.md) while authoring and [migration guidance](references/migration.md) for prose roadmaps.

From this skill directory, run:

```bash
./scripts/plan-gh-backlog validate BACKLOG.md
./scripts/plan-gh-backlog plan BACKLOG.md --max-parallel 8
./scripts/plan-gh-backlog publish BACKLOG.md --repo OWNER/REPO
# Only after reviewing validation, plan, and offline dry-run:
./scripts/plan-gh-backlog publish BACKLOG.md --repo OWNER/REPO --apply
```

Treat `publish` as offline/read-only unless the user explicitly authorizes `--apply`. Do not add `--create-repo` without explicit repository-creation and visibility authorization. Publication is serialized; `--apply` streams phase, item-count, status, and bounded-retry progress to stderr without logging tokens or issue bodies. Plan batches describe implementation dispatch only and do not execute agents.

Use stable IDs permanently. Never copy or edit managed markers. On remote marker conflicts, stop rather than repair destructively. Never close, delete, or prune issues as part of this skill.

Read [publishing and safety](references/publishing.md) before apply. Start from [the complete example](examples/backlog.md).
