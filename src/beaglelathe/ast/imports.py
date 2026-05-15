"""Import detection.

Captures @import nodes from the language's .scm query and best-effort parses
out the module path with a small per-language regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from tree_sitter import QueryCursor

from .parser import parse
from . import registry
from .truncate import _compiled_query


@dataclass(frozen=True)
class Import:
    raw: str
    module: Optional[str]
    names: tuple[str, ...]
    start_line: int
    end_line: int


_PY_FROM = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$")
_PY_PLAIN = re.compile(r"^\s*import\s+([\w., ]+)$")
_TS_FROM = re.compile(r"""from\s+['"]([^'"]+)['"]""")


def imports(source: bytes, language: str) -> list[Import]:
    config = registry.get(language)
    if config is None:
        return []
    tree = parse(source, language)
    query = _compiled_query(config.name, config.tags_query_path)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    out: list[Import] = []
    seen: set[tuple[int, int]] = set()
    for cap_name in config.import_capture_names:
        for node in captures.get(cap_name, []):
            key = (node.start_byte, node.end_byte)
            if key in seen:
                continue
            seen.add(key)
            raw = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            module, names = _parse_import(raw, language)
            out.append(
                Import(
                    raw=raw.strip(),
                    module=module,
                    names=tuple(names),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                )
            )
    out.sort(key=lambda i: i.start_line)
    return out


def _parse_import(raw: str, language: str) -> tuple[Optional[str], list[str]]:
    if language == "python":
        m = _PY_FROM.match(raw)
        if m:
            module = m.group(1)
            names_str = m.group(2).rstrip()
            names = [n.strip().split(" as ")[0].strip() for n in names_str.split(",") if n.strip()]
            return module, names
        m = _PY_PLAIN.match(raw)
        if m:
            mods = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip()]
            return (mods[0] if len(mods) == 1 else None), mods
        return None, []
    if language in ("typescript", "tsx", "javascript"):
        m = _TS_FROM.search(raw)
        module = m.group(1) if m else None
        # Names: best-effort grab from the destructured / default import section.
        names: list[str] = []
        head = raw.split("from", 1)[0]
        head = head.replace("import", "", 1).strip()
        # Strip a trailing `;` or comma garbage.
        head = head.rstrip(";").strip()
        if head:
            # Default import: bare identifier.
            if head.startswith("{"):
                inside = head.strip("{}").strip()
                names = [n.strip().split(" as ")[0].strip() for n in inside.split(",") if n.strip()]
            elif head.startswith("*"):
                # `import * as X from ...`
                m2 = re.search(r"\*\s+as\s+(\w+)", head)
                if m2:
                    names = [m2.group(1)]
            else:
                # Default import, possibly followed by `, { x, y }`.
                parts = head.split(",", 1)
                default = parts[0].strip()
                if default:
                    names.append(default)
                if len(parts) == 2 and parts[1].strip().startswith("{"):
                    inside = parts[1].strip().strip("{}").strip()
                    names.extend(
                        n.strip().split(" as ")[0].strip()
                        for n in inside.split(",")
                        if n.strip()
                    )
        return module, names
    return None, []
