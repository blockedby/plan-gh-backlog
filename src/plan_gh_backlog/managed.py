from __future__ import annotations

import re

MARKER_RE = re.compile(r"<!--\s*plan-gh-backlog:id=([A-Za-z0-9][A-Za-z0-9._-]*)\s*-->")
BLOCK_RE = re.compile(
    r"<!-- plan-gh-backlog:managed:start version=1 id=([A-Za-z0-9][A-Za-z0-9._-]*) -->\n"
    r".*?"
    r"\n<!-- plan-gh-backlog:managed:end -->",
    re.DOTALL,
)


class ManagedContentError(ValueError):
    pass


def marker(item_id: str) -> str:
    return f"<!-- plan-gh-backlog:id={item_id} -->"


def managed_block(item_id: str, content: str) -> str:
    clean = content.strip()
    return (
        f"<!-- plan-gh-backlog:managed:start version=1 id={item_id} -->\n"
        f"{marker(item_id)}\n\n{clean}\n"
        "<!-- plan-gh-backlog:managed:end -->"
    )


def marker_ids(body: str | None) -> list[str]:
    return MARKER_RE.findall(body or "")


def replace_managed(existing: str | None, item_id: str, content: str) -> str:
    existing = existing or ""
    ids = marker_ids(existing)
    blocks = list(BLOCK_RE.finditer(existing))
    if ids:
        if ids != [item_id]:
            raise ManagedContentError(f"body has conflicting or duplicate managed markers: {ids}")
        if len(blocks) != 1 or blocks[0].group(1) != item_id:
            raise ManagedContentError("managed marker exists outside one valid version=1 managed block")
        return existing[: blocks[0].start()] + managed_block(item_id, content) + existing[blocks[0].end() :]
    if blocks:
        raise ManagedContentError("managed block exists without a valid ID marker")
    if not existing.strip():
        return managed_block(item_id, content)
    return existing.rstrip() + "\n\n" + managed_block(item_id, content)
