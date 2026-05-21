"""multi-file-rename: pass iff every fixture file has been renamed."""

from __future__ import annotations

from pathlib import Path

# Mirrors setup.FIXTURE_FILES keys. Duplicated by intent — if setup gains a
# new file, verify must too. tests/test_bench_fixtures.py cross-checks the
# two lists.
RELS = (
    "src/auth/login.ts",
    "src/auth/session.ts",
    "src/auth/token.ts",
    "src/api/users.ts",
    "src/api/posts.ts",
    "src/api/comments.ts",
    "src/middleware/guard.ts",
    "src/middleware/logger.ts",
    "src/admin/dashboard.ts",
    "src/admin/settings.ts",
    "src/admin/audit.ts",
    "src/index.ts",
)


def verify(root: Path) -> tuple[bool, str]:
    renamed = 0
    leftover_files: list[str] = []
    missing_new: list[str] = []
    missing_files: list[str] = []
    for rel in RELS:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            missing_files.append(rel)
            continue
        if "handleAuth" in text:
            leftover_files.append(rel)
        if "handleLogin" not in text:
            missing_new.append(rel)
            continue
        if "handleAuth" not in text:
            renamed += 1

    total = len(RELS)
    if renamed == total and not leftover_files and not missing_new and not missing_files:
        return True, f"renamed {renamed}/{total} files"
    parts = [f"renamed={renamed}/{total}"]
    if missing_files:
        parts.append(f"missing source files: {missing_files}")
    if leftover_files:
        parts.append(f"leftover handleAuth in: {leftover_files}")
    if missing_new:
        parts.append(f"handleLogin missing in: {missing_new}")
    return False, "; ".join(parts)
