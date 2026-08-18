# Publishing and safety

## Workflow

```bash
plan-gh-backlog validate BACKLOG.md
plan-gh-backlog plan BACKLOG.md --max-parallel 8
plan-gh-backlog publish BACKLOG.md --repo OWNER/REPO
plan-gh-backlog publish BACKLOG.md --repo OWNER/REPO --apply
```

The first three commands are read-only; the dry-run publish is deliberately offline. Apply performs serialized REST calls.

To create an absent repository explicitly:

```bash
plan-gh-backlog publish BACKLOG.md \
  --repo OWNER/REPO --create-repo --visibility private --apply
```

`--create-repo` requires both `--apply` and `--visibility public|private`. If the repository already exists, the publisher uses it and does not alter visibility.

## Authentication and permissions

Set `GH_TOKEN`, or authenticate GitHub CLI so `gh auth token` succeeds. The token is passed only in an HTTP authorization header and is never printed. Use repository permissions sufficient for metadata and issues. In fine-grained tokens/GitHub Apps, this normally means:

- Metadata: read
- Issues: read and write
- Administration: write only when creating a repository

Organization policy may separately restrict issue types, sub-issues, labels, milestones, or repository creation.

## Stable identity and managed content

Issues are identified by exactly one marker, independent of title:

```html
<!-- plan-gh-backlog:id=E-01.1 -->
```

Generated text lives in a versioned block:

```html
<!-- plan-gh-backlog:managed:start version=1 id=E-01.1 -->
...
<!-- plan-gh-backlog:managed:end -->
```

On update, text outside that block is preserved byte-for-byte. Duplicate markers, conflicting markers, malformed blocks, or the same marker on multiple remote issues stop publication before backlog mutations. Do not manually copy markers between issues.

Existing user labels on managed issues are replaced by the schema's label set because labels are managed fields. Issue title, milestone, and managed body are also reconciled. Issues are never closed or deleted. Unlisted managed issues are left untouched; pruning is intentionally out of scope.

## Apply phases and partial reruns

1. Validate locally.
2. Verify/create repository.
3. Fetch all issues and milestones and preflight markers.
4. Reconcile labels and milestones.
5. Create missing epic/task issues to obtain numbers.
6. Reconcile final bodies with exact `#number` dependencies and checklists.
7. Attach native GitHub sub-issues.

Every operation is deterministic and serialized. A report is atomically rewritten as progress is made, so failures leave useful state. Rerun the same command after correcting a transient error. Existing markers prevent duplicate issues.

The client follows REST pagination and retries primary/secondary rate limits, HTTP 429, and transient 5xx responses with bounded delay. It does not parallelize publication.

## Report

The default report is `.plan-gh-backlog-report.json`, which this repository gitignores. Choose another path with `--report PATH`. It contains the repository, apply/network mode, issue ID mapping, resource statuses (`planned`, `created`, `updated`, `skipped`), native sub-issue results, and errors. It never contains the token.

## Limits

- No issue closing, deletion, or pruning.
- No implementation-agent execution.
- No conversion of arbitrary prose into guessed tasks.
- No project boards, assignees, issue types, or custom fields.
- Publication requires GitHub REST support for native sub-issues; policy or feature restrictions surface as an error and can be safely retried.
