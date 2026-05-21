"""run-tests-fix-bug: pass iff `python -m unittest test_calc` returns 0 AND divide actually divides."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def verify(root: Path) -> tuple[bool, str]:
    calc_path = root / "src/calc.py"
    test_path = root / "test_calc.py"
    if not calc_path.exists():
        return False, "src/calc.py missing"
    if not test_path.exists():
        return False, "test_calc.py missing"

    # Run the actual test suite. The fixture uses stdlib unittest so no extra
    # deps are required in the tmp dir's interpreter.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "test_calc", "-v"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "unittest run timed out"
    if result.returncode != 0:
        # unittest prints results to stderr; truncate for readability.
        err = (result.stderr or result.stdout or "").strip()
        return False, f"unittest exited {result.returncode}: {err[-300:]}"

    # Behavioural sanity: divide(10, 2) must return 5 (not 25 or any other
    # value the agent might "fix" the test against).
    text = calc_path.read_text(encoding="utf-8")
    try:
        ns: dict = {}
        exec(compile(text, str(calc_path), "exec"), ns)
    except Exception as e:  # noqa: BLE001
        return False, f"src/calc.py failed to import after fix: {e}"
    calculator_cls = ns.get("Calculator")
    if calculator_cls is None:
        return False, "Calculator class missing from src/calc.py"
    inst = calculator_cls()
    got = inst.divide(10, 2)
    if got != 5:
        return False, f"divide(10, 2) returned {got!r}, want 5 — fix didn't actually fix divide"

    # Anti-cheating: prompt said do not modify test_calc.py.
    test_text = test_path.read_text(encoding="utf-8")
    if "self.assertEqual(self.calc.divide(10, 2), 5)" not in test_text:
        return False, "test_calc.py was modified — the prompt forbade that"

    return True, "all 6 unittest tests pass; divide(10,2)=5"
