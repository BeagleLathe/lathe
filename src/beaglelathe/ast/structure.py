"""mode=structure: declarations + exports only.

Tighter than mode=truncated. Body-stubbing is inherited from truncate(), so
function/method bodies still re-parse cleanly. On top of that, structure mode:

  - drops module-level statements (assignments, expressions, conditionals)
  - drops decorators (Python `@decorator` lines)
  - drops class-body statements that aren't methods

Result: only `import`, `def`/`class`/`type`/`interface`/`enum`, and `export`
forms survive. Useful for a fast "what's in this file?" overview when the
default truncated mode is still too noisy.
"""

from __future__ import annotations

from .parser import parse
from .truncate import truncate


# Top-level node types that survive structure mode, per language.
_KEEP_TOPLEVEL: dict[str, set[str]] = {
    "python": {
        "import_statement",
        "import_from_statement",
        "function_definition",
        "class_definition",
        "decorated_definition",
    },
    "typescript": {
        "import_statement",
        "export_statement",
        "interface_declaration",
        "type_alias_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "function_declaration",
        "function_signature",
        "enum_declaration",
        "ambient_declaration",
    },
}
_KEEP_TOPLEVEL["tsx"] = _KEEP_TOPLEVEL["typescript"]


# Inside a class body, only these node types survive.
_KEEP_CLASS_MEMBER: dict[str, set[str]] = {
    "python": {
        "function_definition",
        "decorated_definition",
    },
    "typescript": {
        "method_definition",
        "method_signature",
        "abstract_method_signature",
    },
}
_KEEP_CLASS_MEMBER["tsx"] = _KEEP_CLASS_MEMBER["typescript"]


def structure(source: bytes, language: str) -> bytes:
    if language not in _KEEP_TOPLEVEL:
        return source

    # Stub bodies first so the slices we copy below are already AST-truncated.
    truncated_src = truncate(source, language)
    tree = parse(truncated_src, language)
    if tree.root_node.has_error:
        return truncated_src

    chunks: list[str] = []
    keep = _KEEP_TOPLEVEL[language]
    for child in tree.root_node.children:
        if child.type not in keep:
            continue
        chunk = _emit_node(child, truncated_src, language)
        if chunk:
            chunks.append(chunk)

    if not chunks:
        return truncated_src

    return ("\n\n".join(chunks) + "\n").encode("utf-8")


def _emit_node(node, source: bytes, language: str) -> str:
    """Render a kept node. For classes we filter their body; for decorated
    Python defs we unwrap the decorator. Everything else is emitted verbatim
    from the (already body-stubbed) source slice."""
    if language == "python" and node.type == "decorated_definition":
        inner = _find_inner_python_def(node)
        if inner is None:
            return ""
        return _emit_node(inner, source, language)

    if node.type in ("class_definition", "class_declaration", "abstract_class_declaration"):
        return _emit_class(node, source, language)

    if node.type == "export_statement":
        # Keep only exports that wrap a kept declaration. Skip `export const FOO = ...`
        # and `export default <expr>`. Inspect the declaration child.
        inner = _find_export_declaration(node)
        if inner is None:
            return ""
        # If the inner is a class, filter its body too.
        if inner.type in ("class_declaration", "abstract_class_declaration"):
            # Render the `export` prefix plus a filtered class.
            return _emit_export_with_class(node, inner, source, language)
        return _slice(node, source)

    return _slice(node, source)


def _slice(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_inner_python_def(decorated_node):
    for child in decorated_node.children:
        if child.type in ("function_definition", "class_definition"):
            return child
    return None


def _find_export_declaration(export_node):
    """Inside an `export_statement`, return the kept declaration child or None."""
    keep_inner = {
        "function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }
    for child in export_node.children:
        if child.type in keep_inner:
            return child
    return None


# ── Class-body filtering ──────────────────────────────────────────────────────


def _emit_class(node, source: bytes, language: str) -> str:
    """Emit a class with only method/method-signature children in its body."""
    if language == "python":
        return _emit_python_class(node, source)
    return _emit_ts_class(node, source, language)


def _emit_python_class(node, source: bytes) -> str:
    """Reconstruct the class header verbatim and filter the body to keep only
    method definitions (and decorated method definitions, with their decorators
    stripped)."""
    body = node.child_by_field_name("body")
    if body is None:
        return _slice(node, source)
    header = source[node.start_byte:body.start_byte].decode("utf-8", errors="replace")
    # Drop any trailing whitespace/newline after the `:` so we control spacing.
    header = header.rstrip()

    keep = _KEEP_CLASS_MEMBER["python"]
    method_chunks: list[str] = []
    for child in body.children:
        if child.type not in keep:
            continue
        if child.type == "decorated_definition":
            inner = _find_inner_python_def(child)
            if inner is None or inner.type != "function_definition":
                continue
            method_text = _slice(inner, source)
        else:
            method_text = _slice(child, source)
        # Method text was sliced from the original (indented) source, so its
        # indentation is already correct for the class body.
        method_chunks.append(method_text.rstrip())

    if not method_chunks:
        return header + "\n    ..."
    return header + "\n" + "\n\n".join(method_chunks)


def _emit_ts_class(node, source: bytes, language: str) -> str:
    """Reconstruct a TS class header verbatim and filter the class body to
    keep only method definitions."""
    body = node.child_by_field_name("body")
    if body is None:
        return _slice(node, source)

    # Header is everything up to (and including) the opening `{` of the body.
    body_open = body.start_byte
    # body.start_byte points at `{`; include it.
    header = source[node.start_byte:body_open + 1].decode("utf-8", errors="replace")
    keep = _KEEP_CLASS_MEMBER[language]
    method_chunks: list[str] = []
    for child in body.children:
        if child.type not in keep:
            continue
        method_text = _slice(child, source).rstrip()
        method_chunks.append("  " + method_text)

    if not method_chunks:
        return header + "}"
    return header + "\n" + "\n".join(method_chunks) + "\n}"


def _emit_export_with_class(export_node, class_node, source: bytes, language: str) -> str:
    """Emit `export ... class Foo { ... }` with the class body filtered."""
    # Prefix up to where the class starts (so we keep `export `, `export default `, etc.).
    prefix = source[export_node.start_byte:class_node.start_byte].decode("utf-8", errors="replace")
    class_text = _emit_ts_class(class_node, source, language)
    return prefix + class_text
