"""constants-in-module: pass iff answer.txt contains exactly the integer 7."""

from __future__ import annotations

from pathlib import Path


def verify(root: Path) -> tuple[bool, str]:
    answer = root / "answer.txt"
    if not answer.exists():
        return False, "answer.txt missing"
    text = answer.read_text(encoding="utf-8").strip()
    if text != "7":
        return False, f"answer.txt={text!r}, want '7'"
    # Anti-cheating: make sure the agent didn't edit src/limits.py to make
    # the answer easier (the prompt explicitly forbade that).
    limits = (root / "src/limits.py").read_text(encoding="utf-8")
    if "MAX_RETRIES = 7" not in limits:
        return False, "src/limits.py was modified — the prompt forbade that"
    return True, "answer.txt=7 (matches MAX_RETRIES from src/limits.py)"
