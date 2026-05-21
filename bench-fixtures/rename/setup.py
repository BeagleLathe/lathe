"""rename: rename a symbol across multiple files.

Exercises the basic refactor pattern — find every reference, replace it,
re-verify. This is the historical bench fixture; preserved verbatim so we
can compare new bench runs against the pre-L1 baseline in bench-logs/.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/auth/login.ts": (
        "export function handleAuth() {\n"
        "  return true;\n"
        "}\n"
    ),
    "src/auth/session.ts": (
        "import { handleAuth } from './login';\n"
        "// uses handleAuth\n"
        "export const session = handleAuth();\n"
    ),
    "src/auth/token.ts": (
        "import { handleAuth } from './login';\n"
        "export const a = handleAuth() ? 1 : 0;\n"
        "export const b = handleAuth();\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
