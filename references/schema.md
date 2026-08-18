# Backlog schema v1

The parser is intentionally strict. Normalize source material into this schema; do not ask the publisher to infer structure from prose.

## Document structure

A UTF-8 file must contain these sections in order:

```markdown
# GitHub Backlog

- Version: 1
- Max parallel: 8

## Labels
## Milestones
## Epics
## Tasks
```

`Max parallel` is an integer from 1 through 64. IDs are case-sensitive and must match `[A-Za-z0-9][A-Za-z0-9._-]*`. IDs are globally unique across managed milestones, epics, and tasks.

## Labels

One line per label:

```markdown
- `type:task` | `1D76DB` | Managed implementation task
```

The color is exactly six hexadecimal characters. Every label used by an epic or task must be declared.

## Milestones

```markdown
### Milestone `M1`: Foundation
- Due: 2027-03-31

#### Body

A human-readable outcome.
```

`Due` is `YYYY-MM-DD` or `none`. The body is required. The publisher tracks milestones with a stable marker in the managed portion of their description.

## Epics

```markdown
### Epic `E-01`: Identity foundation
- Milestone: `M1`
- Wave: 1
- Area: identity
- Owner: platform-team
- Labels: `type:epic`, `area:identity`
- Checklist:
  - [ ] `E-01.1` Define claims
  - [ ] `E-01.2` Validate tokens

#### Body

Epic context and acceptance outcome.
```

All fields and a nonempty body are required. The checklist must contain every child task exactly once, no other task, and the exact task title. Checked state is copied to the generated GitHub checklist; it does not close issues.

## Tasks

```markdown
### Task `E-01.2`: Validate tokens
- Epic: `E-01`
- Milestone: `M1`
- Wave: 2
- Area: identity
- Owner: platform-team
- Dependencies: `E-01.1`
- Conflict group: identity-core
- Paths: `src/identity/`, `tests/identity/`
- Labels: `type:task`, `area:identity`

#### Body

Validate issuer, audience, expiry, and signature.
```

Use `none` for an empty dependency list, conflict group, or path list. A task milestone must match its parent epic. A task wave cannot precede its epic or depend on a task in a later wave. Dependencies may be in the same wave, but the scheduler puts them in a later batch.

## Scheduling semantics

The planner finishes lower waves before starting higher waves. Inside a wave it repeatedly chooses dependency-ready tasks in ID order, up to the concurrency cap.

Two tasks cannot share a batch when:

- they have the same nonempty conflict group; or
- one declared path ownership root equals or contains the other.

Glob suffixes are reduced to the literal ownership prefix. For example, `src/api/**` conflicts with `src/api/routes.py`. A path with no literal prefix (such as `**`) conflicts with every declared path and should be avoided. `Area` and `Owner` are metadata, not implicit locks.

The plan is only a dispatch plan. The CLI never runs coding agents.

## Diagnostics

Parser and validation errors use `path:line: error[code]: message`. Fix all errors before planning or publishing. Publication always validates before constructing a GitHub client, and dry-run uses no network.
