"""multi-file-rename: rename a symbol across a dozen files in nested directories.

Scaled-up counterpart to `rename`. The single-source bench-fixture/rename has
only 3 files; that's too small to exercise BL's multi-file edit advantage —
vanilla Read+Edit per file is still tractable at n=3. With 12 files, vanilla
has to do ~12 Read + ~12 Edit calls; BL collapses that into 1 search + 1
multi-file edit.

The files live in 4 nested directories so a flat glob isn't enough — the
agent has to either recurse via search or know to enumerate subdirs.
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
        "export const session = handleAuth();\n"
    ),
    "src/auth/token.ts": (
        "import { handleAuth } from './login';\n"
        "export const a = handleAuth() ? 1 : 0;\n"
        "export const b = handleAuth();\n"
    ),
    "src/api/users.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function getUsers() {\n"
        "  if (!handleAuth()) return [];\n"
        "  return [];\n"
        "}\n"
    ),
    "src/api/posts.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function getPosts() {\n"
        "  return handleAuth() ? [] : null;\n"
        "}\n"
    ),
    "src/api/comments.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function getComments(id: string) {\n"
        "  if (!handleAuth()) return null;\n"
        "  const ok = handleAuth();\n"
        "  return ok ? [] : null;\n"
        "}\n"
    ),
    "src/middleware/guard.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export const guard = (req: unknown) => {\n"
        "  if (!handleAuth()) {\n"
        "    throw new Error('Unauthorized');\n"
        "  }\n"
        "};\n"
    ),
    "src/middleware/logger.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function log(msg: string) {\n"
        "  const auth = handleAuth();\n"
        "  console.log({ msg, auth });\n"
        "}\n"
    ),
    "src/admin/dashboard.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function loadDashboard() {\n"
        "  return handleAuth() ? { ok: true } : { ok: false };\n"
        "}\n"
    ),
    "src/admin/settings.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function updateSettings() {\n"
        "  if (!handleAuth()) throw new Error('forbidden');\n"
        "  return { updated: true };\n"
        "}\n"
    ),
    "src/admin/audit.ts": (
        "import { handleAuth } from '../auth/login';\n"
        "export function auditLog() {\n"
        "  const ok = handleAuth();\n"
        "  return ok ? [{ event: 'audit' }] : [];\n"
        "}\n"
    ),
    "src/index.ts": (
        "import { handleAuth } from './auth/login';\n"
        "import { getUsers } from './api/users';\n"
        "import { guard } from './middleware/guard';\n"
        "import { loadDashboard } from './admin/dashboard';\n"
        "\n"
        "export { handleAuth, getUsers, guard, loadDashboard };\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
