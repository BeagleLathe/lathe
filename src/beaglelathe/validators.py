"""Per-language post-edit syntax validators.

Returns (status, detail) where status is "ok" | "skipped" | "syntax_error".
Never raises — a validator that crashes returns ("skipped", reason).

Python and JSON use stdlib parsers. TypeScript/JavaScript/TSX route through
the shared AST subsystem (`beaglelathe.ast`).
"""

from __future__ import annotations

import ast as py_ast
import json
from pathlib import Path
from typing import Optional

from .ast import language_for_path, parse

_TS_FAMILY_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _has_errors(source: str, language: str) -> Optional[str]:
    """Return None if clean, or a short string describing the first error."""
    tree = parse(source.encode("utf-8"), language)
    root = tree.root_node
    if not root.has_error:
        return None
    bad = _first_bad(root)
    if bad is None:
        return "unknown parse error"
    line = bad.start_point[0] + 1
    col = bad.start_point[1] + 1
    kind = "missing" if bad.is_missing else "error"
    return f"{kind} node at line {line}, col {col} (type={bad.type!r})"


def _first_bad(node):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            return n
        stack.extend(reversed(n.children))
    return None


def validate(path: Path, content: str) -> tuple[str, Optional[str]]:
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        try:
            py_ast.parse(content)
            return "ok", None
        except SyntaxError as e:
            return "syntax_error", f"{e.msg} at line {e.lineno}, col {e.offset}"
        except Exception as e:  # noqa: BLE001
            return "skipped", f"validator crashed: {e}"

    if suffix == ".json":
        try:
            json.loads(content)
            return "ok", None
        except json.JSONDecodeError as e:
            return "syntax_error", f"{e.msg} at line {e.lineno}, col {e.colno}"
        except Exception as e:  # noqa: BLE001
            return "skipped", f"validator crashed: {e}"

    if suffix in _TS_FAMILY_EXTS:
        lang = language_for_path(str(path))
        if not lang:
            return "skipped", None
        try:
            err = _has_errors(content, lang)
            return ("ok", None) if err is None else ("syntax_error", err)
        except Exception as e:  # noqa: BLE001
            return "skipped", f"validator crashed: {e}"

    return "skipped", None
