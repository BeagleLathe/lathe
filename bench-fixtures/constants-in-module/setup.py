"""constants-in-module: the answer is a module-level constant that `mode=structure` drops.

Exercises the failure mode where an agent over-eagerly picks `mode=structure`
on a small file. Structure mode strips module-level assignments, so the
agent would see no value for MAX_RETRIES. If the agent picks `mode=truncated`
(default), `mode=full`, or `mode=skeleton`, the constant is preserved.

A passing run demonstrates the agent either chooses the right read mode for
the task, or falls through to a mode that surfaces the constant.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/limits.py": (
        '"""Limits for the request pipeline."""\n'
        "\n"
        "MAX_RETRIES = 7\n"
        "TIMEOUT_MS = 5000\n"
        "BACKOFF_BASE = 1.5\n"
        "\n"
        "def call_api(url):\n"
        "    \"\"\"Stub for the API caller — body intentionally elided.\"\"\"\n"
        "    raise NotImplementedError\n"
        "\n"
        "class RetryPolicy:\n"
        "    def __init__(self, max_retries):\n"
        "        self.max_retries = max_retries\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
