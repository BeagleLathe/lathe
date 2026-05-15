"""Language registry: extension-to-name dispatch + LanguageConfig table.

Adding a new language is one entry here plus one .scm file in queries/.
Per-language Python handlers (in handlers/) only show up for languages that
need imperative logic the .scm engine can't express.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .language_config import LanguageConfig

_QUERIES_DIR = Path(__file__).parent / "queries"


def _q(name: str) -> str:
    return str(_QUERIES_DIR / f"{name}.scm")


# Python: `body` field is named "body". Generic engine handles it.
PYTHON = LanguageConfig(
    name="python",
    extensions=(".py",),
    tags_query_path=_q("python"),
    body_field_name="body",
    function_capture_names=("function", "method"),
)

# TypeScript: function_declaration, method_definition, arrow_function bodies.
TYPESCRIPT = LanguageConfig(
    name="typescript",
    extensions=(".ts",),
    tags_query_path=_q("typescript"),
    body_field_name="body",
    function_capture_names=("function", "method"),
)

# TSX uses the same query as TypeScript plus JSX awareness; the grammar differs
# from TS so we route .tsx to its own grammar via the TSXHandler.
TSX = LanguageConfig(
    name="tsx",
    extensions=(".tsx",),
    tags_query_path=_q("tsx"),
    body_field_name="body",
    function_capture_names=("function", "method"),
    handler=None,  # TSXHandler attached at module-load time below
)


LANGUAGES: dict[str, LanguageConfig] = {
    PYTHON.name: PYTHON,
    TYPESCRIPT.name: TYPESCRIPT,
    TSX.name: TSX,
}


def _ext_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for cfg in LANGUAGES.values():
        for ext in cfg.extensions:
            out[ext.lower()] = cfg.name
    return out


_EXT_TO_NAME = _ext_index()


def language_for_path(path: str) -> Optional[str]:
    """Resolve a file path to a registered language key, or None."""
    suffix = Path(path).suffix.lower()
    return _EXT_TO_NAME.get(suffix)


def get(language: str) -> Optional[LanguageConfig]:
    return LANGUAGES.get(language)


# Wire the TSX handler at import time. Done here (rather than in TSX dataclass)
# to avoid a circular import with handlers/tsx.py.
def _wire_handlers() -> None:
    from .handlers.tsx import TSXHandler

    LANGUAGES["tsx"] = LanguageConfig(
        **{**TSX.__dict__, "handler": TSXHandler},
    )


_wire_handlers()
