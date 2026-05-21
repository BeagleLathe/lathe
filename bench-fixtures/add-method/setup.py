"""add-method: add a new method that reuses an existing one.

Exercises the "find call sites, understand the existing API surface, add
new code that fits in" pattern — distinct from a flat rename. Catches
regressions in how well the agent uses BL's `search` to discover related
methods before adding new ones.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/calc.py": (
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "\n"
        "    def sub(self, a, b):\n"
        "        return a - b\n"
    ),
    "src/runner.py": (
        "from src.calc import Calculator\n"
        "\n"
        "calc = Calculator()\n"
        "print(calc.add(2, 3))\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
