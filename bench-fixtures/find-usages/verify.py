"""find-usages: pass iff callers.txt lists exactly the 6 real callers, sorted."""

from __future__ import annotations

from pathlib import Path

EXPECTED_CALLERS: list[str] = sorted(
    [
        "src/server/auth.py",
        "src/server/db.py",
        "src/cli/main.py",
        "src/cli/init.py",
        "src/jobs/scheduler.py",
        "src/jobs/worker.py",
    ]
)


def verify(root: Path) -> tuple[bool, str]:
    callers_file = root / "callers.txt"
    if not callers_file.exists():
        return False, "callers.txt missing"
    raw = callers_file.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if lines == EXPECTED_CALLERS:
        return True, f"{len(EXPECTED_CALLERS)} callers identified correctly"

    got_set = set(lines)
    want_set = set(EXPECTED_CALLERS)
    missing = sorted(want_set - got_set)
    extra = sorted(got_set - want_set)
    sorted_correctly = lines == sorted(lines)
    parts: list[str] = []
    if missing:
        parts.append(f"missing real callers: {missing}")
    if extra:
        parts.append(f"included non-callers: {extra}")
    if not missing and not extra and not sorted_correctly:
        parts.append("right set, wrong order (must be lexicographic)")
    return False, "; ".join(parts) or f"got {lines}, want {EXPECTED_CALLERS}"
