"""add-method: pass iff multiply_then_add exists, behaves correctly, and reuses self.add."""

from __future__ import annotations

import ast
from pathlib import Path


def verify(root: Path) -> tuple[bool, str]:
    calc_path = root / "src/calc.py"
    if not calc_path.exists():
        return False, "src/calc.py missing"
    text = calc_path.read_text(encoding="utf-8")

    # Behavioral check: exec the module and call the method.
    try:
        ns: dict = {}
        exec(compile(text, str(calc_path), "exec"), ns)
    except Exception as e:  # noqa: BLE001
        return False, f"src/calc.py failed to import: {e}"

    calculator_cls = ns.get("Calculator")
    if calculator_cls is None:
        return False, "Calculator class missing from src/calc.py"
    inst = calculator_cls()
    if not hasattr(inst, "multiply_then_add"):
        return False, "multiply_then_add method missing"
    try:
        got = inst.multiply_then_add(2, 3, 1)
    except Exception as e:  # noqa: BLE001
        return False, f"multiply_then_add raised: {e}"
    if got != 7:
        return False, f"multiply_then_add(2,3,1) returned {got!r}, want 7"

    # Structural check: the new method must call self.add. Parse the AST and
    # look for a Call node like `self.add(...)` inside multiply_then_add.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "multiply_then_add":
            calls_self_add = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "self"
                and c.func.attr == "add"
                for c in ast.walk(node)
            )
            if not calls_self_add:
                return False, "multiply_then_add does not delegate to self.add (the spec required reuse)"
            return True, "multiply_then_add(2,3,1)=7; delegates to self.add"
    return False, "multiply_then_add not found via AST scan"
