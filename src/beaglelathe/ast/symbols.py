"""Symbol extraction.

Walks the same .scm queries used for truncation and emits a flat list of
named symbols (functions, methods, classes, types, constants, ...) with
byte ranges and 1-indexed line numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tree_sitter import QueryCursor

from .parser import parse
from . import registry
from .truncate import _compiled_query


# Order matters: the first capture name found in a match's dict wins as the
# symbol's `kind`. More specific kinds (method, interface) come before broader
# ones (class, function).
_KIND_PRIORITY: tuple[str, ...] = (
    "method",
    "function",
    "interface",
    "class",
    "type",
    "enum",
    "constant",
)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    parent: Optional[str]


def symbols(source: bytes, language: str) -> list[Symbol]:
    config = registry.get(language)
    if config is None:
        return []
    tree = parse(source, language)
    query = _compiled_query(config.name, config.tags_query_path)
    cursor = QueryCursor(query)
    matches = cursor.matches(tree.root_node)

    out: list[Symbol] = []
    seen: set[tuple[str, int, int]] = set()
    for _pattern_idx, captured in matches:
        kind = _pick_kind(captured)
        if kind is None:
            continue
        target_node = captured[kind][0]
        name_nodes = captured.get("name", [])
        if not name_nodes:
            continue
        # Pick the @name capture that's inside the target node — defends against
        # patterns where multiple identifiers happen to be captured.
        name_node = next(
            (n for n in name_nodes if target_node.start_byte <= n.start_byte
             and n.end_byte <= target_node.end_byte),
            name_nodes[0],
        )
        name = source[name_node.start_byte:name_node.end_byte].decode(
            "utf-8", errors="replace"
        )

        key = (kind, target_node.start_byte, target_node.end_byte)
        if key in seen:
            continue
        seen.add(key)

        out.append(
            Symbol(
                name=name,
                kind=kind,
                start_byte=target_node.start_byte,
                end_byte=target_node.end_byte,
                start_line=target_node.start_point[0] + 1,
                end_line=target_node.end_point[0] + 1,
                parent=_find_parent(target_node, source),
            )
        )
    out.sort(key=lambda s: (s.start_byte, s.end_byte))
    return out


def _pick_kind(captured: dict[str, list]) -> Optional[str]:
    for k in _KIND_PRIORITY:
        if k in captured:
            return k
    return None


def _find_parent(node, source: bytes) -> Optional[str]:
    cur = node.parent
    while cur is not None:
        if cur.type in (
            "class_definition",
            "class_declaration",
            "interface_declaration",
            "impl_item",
            "struct_item",
        ):
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                return source[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
            return None
        cur = cur.parent
    return None
