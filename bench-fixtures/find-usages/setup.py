"""find-usages: identify real callers of a function across many files with red herrings.

Exercises BL's `search` advantage: a ranked, snippet-aware search lets the
agent discriminate by context in one call. Vanilla has to grep, then read
each match to check whether it's actually a call vs an import-only / comment
/ string / different-module call.

Setup creates 16 .py files in src/ plus the canonical definition site.
Real callers count: 6. Red herrings: 9. The agent must distinguish.

A pristine setup must NOT contain a `callers.txt` (verify checks for that
so a no-op agent fails).
"""

from __future__ import annotations

from pathlib import Path

# --- the definition site ---------------------------------------------------
_DEFINITION = (
    "src/lib/utils.py",
    (
        '"""Project-wide config helpers."""\n'
        "\n"
        "def parse_config(path: str) -> dict:\n"
        '    """Load and parse a YAML config from disk."""\n'
        "    return {}\n"
        "\n"
        "def read_yaml(path: str) -> dict:\n"
        '    """Lower-level YAML reader used by parse_config."""\n'
        "    return {}\n"
    ),
)

# --- real callers (6) — import from src.lib.utils AND call --------------
_REAL_CALLERS: list[tuple[str, str]] = [
    (
        "src/server/auth.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "def get_auth_settings(path: str) -> dict:\n"
            "    return parse_config(path)\n"
        ),
    ),
    (
        "src/server/db.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "DB_CONFIG_PATH = '/etc/app/db.yml'\n"
            "settings = parse_config(DB_CONFIG_PATH)\n"
        ),
    ),
    (
        "src/cli/main.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "def main(argv: list[str]) -> int:\n"
            "    cfg = parse_config(argv[1])\n"
            "    print(cfg)\n"
            "    return 0\n"
        ),
    ),
    (
        "src/cli/init.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "def initialize(path: str) -> None:\n"
            "    cfg = parse_config(path)\n"
            "    assert cfg is not None\n"
        ),
    ),
    (
        "src/jobs/scheduler.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "SCHED_FILE = '/etc/app/sched.yml'\n"
            "\n"
            "def load() -> dict:\n"
            "    return parse_config(SCHED_FILE)\n"
        ),
    ),
    (
        "src/jobs/worker.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "def start_worker(cfg_path: str) -> None:\n"
            "    cfg = parse_config(cfg_path)\n"
            "    _ = cfg.get('worker_id')\n"
        ),
    ),
]

# --- import-only (3) — import but never call -----------------------------
_IMPORT_ONLY: list[tuple[str, str]] = [
    (
        "src/server/health.py",
        (
            "from src.lib.utils import parse_config  # noqa: F401\n"
            "\n"
            "def healthcheck() -> bool:\n"
            "    return True\n"
        ),
    ),
    (
        "src/admin/dashboard.py",
        (
            "from src.lib.utils import parse_config  # planned, not yet wired\n"
            "\n"
            "def render() -> str:\n"
            "    return '<html>dashboard</html>'\n"
        ),
    ),
    (
        "src/test/fixtures.py",
        (
            "from src.lib.utils import parse_config\n"
            "\n"
            "__all__ = ['parse_config']  # re-export for tests\n"
        ),
    ),
]

# --- comment / docstring mentions (2) --------------------------------------
_COMMENT_MENTIONS: list[tuple[str, str]] = [
    (
        "src/docs/migration.py",
        (
            '"""Replaces the deprecated parse_config call with read_yaml."""\n'
            "\n"
            "from src.lib.utils import read_yaml\n"
            "\n"
            "def migrate(path: str) -> dict:\n"
            "    return read_yaml(path)\n"
        ),
    ),
    (
        "src/legacy/old.py",
        (
            "from src.lib.utils import read_yaml\n"
            "\n"
            "# was: parse_config(path) — switched to read_yaml in v2.0\n"
            "def load(path: str) -> dict:\n"
            "    return read_yaml(path)\n"
        ),
    ),
]

# --- string-literal mentions (2) -------------------------------------------
_STRING_MENTIONS: list[tuple[str, str]] = [
    (
        "src/errors.py",
        (
            'ERR_MSG = "parse_config: invalid format"\n'
            "\n"
            "def fmt(e: str) -> str:\n"
            "    return f'[error] {e}'\n"
        ),
    ),
    (
        "src/log/messages.py",
        (
            "MESSAGES = {\n"
            "    'cfg_error': 'parse_config failed: %s',\n"
            "    'cfg_ok': 'config loaded',\n"
            "}\n"
        ),
    ),
]

# --- DIFFERENT-MODULE parse_config (2) -------------------------------------
# These DO call a function named `parse_config`, but it's not the one from
# src.lib.utils. The agent must not be fooled.
_DIFFERENT_MODULE: list[tuple[str, str]] = [
    (
        "src/external/api.py",
        (
            "from external.lib import parse_config\n"
            "\n"
            "def call_api() -> dict:\n"
            "    return parse_config('api.yml')\n"
        ),
    ),
    (
        "src/external/billing.py",
        (
            "from external.lib import parse_config\n"
            "\n"
            "def load_billing() -> dict:\n"
            "    return parse_config('/etc/app/billing.yml')\n"
        ),
    ),
]

FIXTURE_FILES: dict[str, str] = dict(
    [_DEFINITION]
    + _REAL_CALLERS
    + _IMPORT_ONLY
    + _COMMENT_MENTIONS
    + _STRING_MENTIONS
    + _DIFFERENT_MODULE
)

EXPECTED_CALLERS: list[str] = sorted(rel for rel, _ in _REAL_CALLERS)


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
