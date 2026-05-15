"""Public API for the BeagleLathe AST subsystem.

Four entry points: parse(), truncate(), symbols(), imports(). The MCP `read`
tool calls into these; nothing else in the codebase reaches into ast/ internals.
"""

from __future__ import annotations

from .imports import Import, imports
from .parser import parse
from .registry import LANGUAGES, language_for_path
from .symbols import Symbol, symbols
from .truncate import truncate

__all__ = [
    "Import",
    "LANGUAGES",
    "Symbol",
    "imports",
    "language_for_path",
    "parse",
    "symbols",
    "truncate",
]
