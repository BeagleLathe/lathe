"""re-read-after-edit: pass iff src/config.py has DEBUG=True AND debug-status.txt reflects it."""

from __future__ import annotations

from pathlib import Path


def verify(root: Path) -> tuple[bool, str]:
    config = root / "src/config.py"
    if not config.exists():
        return False, "src/config.py missing"
    config_text = config.read_text(encoding="utf-8")
    if "DEBUG = True" not in config_text:
        return False, f"src/config.py does not have DEBUG = True; got:\n{config_text!r}"
    if "MAX_RETRIES = 3" not in config_text:
        return False, "src/config.py: MAX_RETRIES was changed (it should be left at 3)"

    status_file = root / "debug-status.txt"
    if not status_file.exists():
        return False, "debug-status.txt missing (the agent didn't perform the re-read step)"
    status = status_file.read_text(encoding="utf-8").strip()
    if status != "DEBUG=True":
        return False, f"debug-status.txt={status!r}, want 'DEBUG=True'"
    return True, "config edited; re-read confirmed DEBUG=True"
