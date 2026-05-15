"""Generic AST truncation engine.

Walks the captures from a language's .scm query, replaces function/method
bodies with stubs, applies edits in reverse byte order so earlier edits don't
shift later positions.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from tree_sitter import Query, QueryCursor

from . import stubs
from .language_config import LanguageConfig
from .parser import get_language, parse
from . import registry


@lru_cache(maxsize=32)
def _compiled_query(language: str, query_path: str) -> Query:
    with open(query_path, "rb") as f:
        text = f.read()
    return Query(get_language(language), text.decode("utf-8"))


def truncate(
    source: bytes,
    language: str,
    *,
    keep_symbol: Optional[str] = None,
) -> bytes:
    config = registry.get(language)
    if config is None:
        return source
    if config.handler is not None:
        return config.handler.truncate(source, config, keep_symbol=keep_symbol)
    return _generic_truncate(source, config, keep_symbol)


def _generic_truncate(
    source: bytes,
    config: LanguageConfig,
    keep_symbol: Optional[str],
) -> bytes:
    tree = parse(source, config.name)
    if tree.root_node.has_error:
        return source

    query = _compiled_query(config.name, config.tags_query_path)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    candidates: list[tuple[int, int, bytes]] = []
    seen: set[tuple[int, int]] = set()
    for cap_name in config.function_capture_names:
        for node in captures.get(cap_name, []):
            key = (node.start_byte, node.end_byte)
            if key in seen:
                continue
            seen.add(key)

            symbol_path = _qualify(node, source)
            if keep_symbol is not None and symbol_path == keep_symbol:
                continue

            body = node.child_by_field_name(config.body_field_name)
            if body is None:
                continue
            # Skip bodies that aren't real blocks (arrow expressions etc).
            if not _is_block_body(body, config):
                continue
            # Avoid no-op work if body is already a stub-shaped placeholder.
            if _looks_stubbed(source, body, config):
                continue

            stub_bytes = stubs.generate(config, source, node, body)
            candidates.append((body.start_byte, body.end_byte, stub_bytes))

    if not candidates:
        return source

    # Drop nested edits: when a function/method body sits inside another body
    # that's also being stubbed, the outer stub will subsume the inner. Keeping
    # both produces overlapping byte slices that corrupt later content. Sort by
    # widest range first, then keep an edit only if it isn't contained in one
    # already accepted.
    candidates.sort(key=lambda e: (e[0], -e[1]))
    accepted: list[tuple[int, int, bytes]] = []
    for start, end, repl in candidates:
        if any(a_start <= start and end <= a_end for a_start, a_end, _ in accepted):
            continue
        accepted.append((start, end, repl))

    out = bytearray(source)
    for start, end, replacement in sorted(accepted, reverse=True):
        out[start:end] = replacement
    return bytes(out)


def _is_block_body(body_node, config: LanguageConfig) -> bool:
    if config.name == "python":
        return body_node.type == "block"
    if config.name in ("typescript", "tsx", "javascript"):
        return body_node.type == "statement_block"
    return True


def _looks_stubbed(source: bytes, body_node, config: LanguageConfig) -> bool:
    """Idempotence safety net: skip bodies that already match our stub shape."""
    text = source[body_node.start_byte:body_node.end_byte]
    if config.name == "python":
        stripped = text.strip()
        return stripped == b"..." or stripped == b"pass"
    if config.name in ("typescript", "tsx", "javascript", "c", "cpp", "php"):
        stripped = b"".join(text.split())
        return stripped == b"{/*...*/}"
    return False


def _qualify(node, source: bytes) -> str:
    """Return 'ClassName.method' or 'function' for symbol matching."""
    parent_class: Optional[str] = None
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
                parent_class = source[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
            break
        cur = cur.parent

    name_node = node.child_by_field_name("name")
    own = (
        source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
        if name_node is not None
        else "<anon>"
    )
    if parent_class:
        return f"{parent_class}.{own}"
    return own
