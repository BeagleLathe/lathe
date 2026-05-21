"""hidden-bug-in-body: a logic error inside a function body, not visible from signature.

Exercises the case where structure-only reads (BL's mode=structure, mode=skeleton)
would miss the bug because the relevant code is inside the function body, not
in the declarations. An agent that defaults to too-aggressive truncation fails
this task; an agent that picks `mode=truncated` (default) or `mode=full` for
this kind of task passes.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/auth.py": (
        "def login(user, password):\n"
        "    # Returns True iff user is admin AND password is the admin secret.\n"
        "    # BUG: the first comparison uses != instead of ==, so any non-admin\n"
        "    # with the correct secret gets in.\n"
        "    if user != \"admin\" and password == \"secret\":\n"
        "        return True\n"
        "    return False\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
