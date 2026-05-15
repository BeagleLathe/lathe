"""Per-language stub generation for AST truncation.

The stub must be syntactically valid in the source language so the truncated
file re-parses cleanly. Python and Ruby have whitespace-sensitive grammars,
so they need their indent preserved. The other languages don't care and the
static template works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .language_config import LanguageConfig

_STATIC_STUBS: dict[str, bytes] = {
    "javascript": b"{ /* ... */ }",
    "typescript": b"{ /* ... */ }",
    "tsx": b"{ /* ... */ }",
    "rust": b"{ unimplemented!() }",
    "go": b'{ panic("stub") }',
    "java": b"{ throw new UnsupportedOperationException(); }",
    "php": b"{ /* ... */ }",
    "csharp": b"{ throw new System.NotImplementedException(); }",
    "c": b"{ /* ... */ }",
    "cpp": b"{ /* ... */ }",
}


def generate(config: "LanguageConfig", source: bytes, function_node, body_node) -> bytes:
    name = config.name
    # Tree-sitter's Python `body` field starts AT the first body statement
    # (the indent stays in the source before body.start_byte), so the stub is
    # just `...` — the existing indent prefixes it naturally.
    if name == "python":
        return b"..."
    if name == "ruby":
        return b"# ..."
    return _STATIC_STUBS.get(name, b"{ /* ... */ }")
