"""rename: pass iff every fixture file has been renamed from handleAuth to handleLogin."""

from __future__ import annotations

from pathlib import Path

# Verify is invoked by the harness as a standalone module (importlib), so no
# relative import to setup.py. The file list is duplicated by intent — if
# setup gains a new file, verify must too. The harness sanity test
# (tests/test_bench_fixtures.py) cross-checks the lists.
RELS = ("src/auth/login.ts", "src/auth/session.ts", "src/auth/token.ts")


def verify(root: Path) -> tuple[bool, str]:
    renamed = 0
    leftover_files: list[str] = []
    missing_new: list[str] = []
    for rel in RELS:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except FileNotFoundError:
            missing_new.append(rel)
            continue
        if "handleAuth" in text:
            leftover_files.append(rel)
        if "handleLogin" in text and "handleAuth" not in text:
            renamed += 1

    total = len(RELS)
    if renamed == total and not leftover_files and not missing_new:
        return True, f"renamed {renamed}/{total} files"
    parts = [f"renamed={renamed}/{total}"]
    if leftover_files:
        parts.append(f"leftover handleAuth in: {leftover_files}")
    if missing_new:
        parts.append(f"missing files: {missing_new}")
    return False, "; ".join(parts)
