"""LanguageConfig dataclass: declarative description of one supported language.

Most languages are described by a single LanguageConfig entry in registry.py
plus a queries/<name>.scm file. Languages with real syntactic quirks set
`handler` to a LanguageHandler subclass; everything else goes through the
generic engine in truncate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .handlers._base import LanguageHandler


@dataclass(frozen=True)
class LanguageConfig:
    name: str
    extensions: tuple[str, ...]
    tags_query_path: str
    body_field_name: str = "body"
    function_capture_names: tuple[str, ...] = ("function", "method")
    class_capture_names: tuple[str, ...] = ("class", "interface", "type", "enum")
    import_capture_names: tuple[str, ...] = ("import",)
    constant_capture_names: tuple[str, ...] = ("constant",)
    handler: Optional[type["LanguageHandler"]] = None
