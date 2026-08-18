# Migrating a free-form roadmap

Do not publish free-form prose directly. Extraction is an editorial step because prose rarely defines stable IDs, exact parentage, or safe dependency ordering.

## Normalize deliberately

1. Preserve the original document as source evidence.
2. List explicit outcomes as milestones; assign stable IDs such as `M1`.
3. Group deliverables into epics; assign IDs such as `E-01`.
4. Split only explicit, independently verifiable work into tasks; assign IDs such as `E-01.1`.
5. Record every task's parent, milestone, wave, area, owner, labels, conflicts, and owned paths.
6. Add a dependency only when the downstream task cannot start before the upstream result exists.
7. Make each epic checklist exactly match its child tasks and titles.
8. Put uncertain statements in bodies or an editorial TODO outside the publishable file; do not invent dependencies or tasks.
9. Run `validate`, inspect line-level errors, then inspect `plan --json` with a human.
10. Preview `publish` before explicit `--apply`.

## Ambiguity report

When information is missing, keep a separate review note rather than guessing:

```markdown
- Source paragraph: "Improve access controls later"
- Missing: measurable outcome, owner, affected paths, dependency order
- Decision needed: fold into E-01 or create a later epic?
```

Copy `examples/backlog.md` as the scaffold. This project intentionally has no automatic prose converter: confidently invented structure is more dangerous than a visible unresolved question.
