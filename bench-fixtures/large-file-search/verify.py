"""large-file-search: pass iff answer.txt contains the target class name."""

from __future__ import annotations

from pathlib import Path

TARGET_CLASS: str = "OrderProcessor"


def verify(root: Path) -> tuple[bool, str]:
    answer = root / "answer.txt"
    if not answer.exists():
        return False, "answer.txt missing"
    text = answer.read_text(encoding="utf-8").strip()
    if text != TARGET_CLASS:
        return False, f"answer.txt={text!r}, want {TARGET_CLASS!r}"
    # Anti-cheating: prompt said do not modify src/library.py.
    lib = (root / "src/library.py").read_text(encoding="utf-8")
    if '"authoritative"' not in lib:
        return False, "src/library.py was modified (target string removed)"
    return True, f"answer.txt={TARGET_CLASS} (the class whose handle() returns 'authoritative')"
