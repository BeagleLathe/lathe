"""re-read-after-edit: edit a file, then re-read it and write the new value out.

Exercises BL's output dedup. The agent searches/reads, edits the file, then
must re-read to observe the new value. If the dedup cache wrongly returned
a `{unchanged_since_turn: K}` stub for the post-edit read (a stale-cache bug),
the agent would have to guess the new value or get it wrong. Dedup keys on
hashed payload so content changes invalidate it — this fixture confirms.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/config.py": (
        "DEBUG = False\n"
        "MAX_RETRIES = 3\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
