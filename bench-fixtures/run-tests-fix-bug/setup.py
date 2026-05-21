"""run-tests-fix-bug: agent runs a test suite, identifies a failing test, fixes the bug.

Exercises BL's `run_tests` tool — which auto-detects pytest / unittest /
cargo / jest and returns structured pass/fail output — vs vanilla running
`Bash python -m unittest test_calc` and parsing the text. Probes whether
the agent reaches for the BL workflow tool when given an actual
test-running task.

The bug is intentionally obvious from any failing-test output:
Calculator.divide returns `a * b` instead of `a / b`, so test_divide_basic
will report `25 != 5`. An agent that reads the failure message can identify
the bug without much exploration.

Uses stdlib `unittest` so no third-party packages need to be in the tmp
dir's environment.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_FILES: dict[str, str] = {
    "src/__init__.py": "",
    "src/calc.py": (
        '"""Simple calculator with a bug in divide()."""\n'
        "\n"
        "\n"
        "class Calculator:\n"
        "    def add(self, a: float, b: float) -> float:\n"
        "        return a + b\n"
        "\n"
        "    def sub(self, a: float, b: float) -> float:\n"
        "        return a - b\n"
        "\n"
        "    def mul(self, a: float, b: float) -> float:\n"
        "        return a * b\n"
        "\n"
        "    def divide(self, a: float, b: float) -> float:\n"
        "        if b == 0:\n"
        "            raise ZeroDivisionError('division by zero')\n"
        "        return a * b  # BUG: should be a / b\n"
    ),
    "test_calc.py": (
        '"""Unit tests for src.calc.Calculator."""\n'
        "\n"
        "import unittest\n"
        "\n"
        "from src.calc import Calculator\n"
        "\n"
        "\n"
        "class TestCalculator(unittest.TestCase):\n"
        "    def setUp(self) -> None:\n"
        "        self.calc = Calculator()\n"
        "\n"
        "    def test_add(self) -> None:\n"
        "        self.assertEqual(self.calc.add(2, 3), 5)\n"
        "\n"
        "    def test_sub(self) -> None:\n"
        "        self.assertEqual(self.calc.sub(5, 3), 2)\n"
        "\n"
        "    def test_mul(self) -> None:\n"
        "        self.assertEqual(self.calc.mul(2, 3), 6)\n"
        "\n"
        "    def test_divide_basic(self) -> None:\n"
        "        self.assertEqual(self.calc.divide(10, 2), 5)\n"
        "\n"
        "    def test_divide_negative(self) -> None:\n"
        "        self.assertEqual(self.calc.divide(-10, 2), -5)\n"
        "\n"
        "    def test_divide_by_zero(self) -> None:\n"
        "        with self.assertRaises(ZeroDivisionError):\n"
        "            self.calc.divide(1, 0)\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}


def setup(root: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
