"""`read` tool: AST-aware structural read with three modes.

mode=full       — file verbatim (matches vanilla Read).
mode=truncated  — function/method bodies stubbed, signatures kept. Default.
mode=skeleton   — regex-picked declaration lines only. Cheapest fallback for
                  unsupported languages and giant files.

The schema follows AST_SUBSYSTEM_PLAN.md §15 — fields are path, mode, symbol.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .. import ast as bh_ast
from ..ast import language_for_path, parse, symbols, truncate

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "mode": {
            "type": "string",
            "enum": ["full", "truncated", "skeleton"],
            "default": "truncated",
            "description": "full: verbatim. truncated: function bodies stubbed via tree-sitter (default). skeleton: regex-picked declaration lines only.",
        },
        "symbol": {
            "type": "string",
            "description": "If set with mode=truncated, keep this symbol's body in full. Use 'ClassName.method' for nested symbols.",
        },
    },
    "required": ["path"],
}

DESCRIPTION = (
    "Read a source file with substantial token savings by stubbing function and method "
    "bodies while keeping every signature, type, import, and module-level declaration "
    "intact — the structural information you need to navigate or plan changes, without the "
    "implementation bytes you usually don't. Prefer this over the built-in Read tool for any "
    "source file over ~300 lines, or whenever you only need to understand a file's structure "
    "before deciding what to modify; the truncated output is almost always sufficient and the "
    "context savings compound across a session. mode=truncated (default) is AST-aware via "
    "tree-sitter. mode=full returns the file verbatim — use when you genuinely need every "
    "line (small files, exact diff context, line-precise edits). mode=skeleton returns just "
    "declaration lines via regex — cheapest fallback for unsupported languages or files "
    ">10k lines. Pass symbol='ClassName.method' with mode=truncated to keep one symbol's "
    "body in full while stubbing the rest — ideal when zeroing in on a specific function in "
    "a large file."
)

MAX_OUTPUT_CHARS = 200_000

# Per-language skeleton patterns. Each pattern is checked against the line's
# leading non-whitespace content. Falls back to _GENERIC_SKELETON_KEYWORDS
# when the language isn't listed (so unsupported files still get a useful
# overview).
_SKELETON_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(r"^\s*(def\s|class\s|import\s|from\s|@)"),
    "typescript": re.compile(
        r"^\s*(export\s|import\s|function\s|class\s|interface\s|type\s|enum\s|abstract\s|declare\s)"
    ),
    "tsx": re.compile(
        r"^\s*(export\s|import\s|function\s|class\s|interface\s|type\s|enum\s|abstract\s|declare\s)"
    ),
}

_GENERIC_SKELETON_KEYWORDS = re.compile(
    r"^\s*(def\s|class\s|function\s|interface\s|type\s|enum\s|import\s|from\s|export\s|fn\s|func\s|impl\s|trait\s|struct\s|module\s|namespace\s|public\s|private\s|protected\s|@)"
)


def _is_binary(b: bytes) -> bool:
    return b"\x00" in b[:8192]


def _line_count(s: str) -> int:
    if not s:
        return 0
    n = s.count("\n")
    if not s.endswith("\n"):
        n += 1
    return n


def _hard_cap(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_OUTPUT_CHARS:
        return content, False
    half = MAX_OUTPUT_CHARS // 2
    head = content[:half]
    tail = content[-half:]
    return (
        head
        + f"\n\n/* ... {len(content) - MAX_OUTPUT_CHARS} chars elided ... */\n\n"
        + tail,
        True,
    )


def _skeleton(content: str, language: Optional[str]) -> str:
    pattern = _SKELETON_PATTERNS.get(language or "", _GENERIC_SKELETON_KEYWORDS)
    out_lines: list[str] = []
    for line in content.splitlines():
        if pattern.match(line):
            out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if out_lines else "")


def _resolve_path(path_str: str, cwd: Path) -> tuple[Optional[Path], Optional[dict]]:
    cwd = cwd.resolve()
    p = Path(path_str)
    if not p.is_absolute():
        p = cwd / p
    try:
        p_resolved = p.resolve(strict=False)
    except (OSError, RuntimeError):
        return None, {"error": "invalid path"}
    try:
        p_resolved.relative_to(cwd)
    except ValueError:
        return None, {"error": "path outside workspace"}
    if not p_resolved.exists():
        return None, {"error": "file not found"}
    if not p_resolved.is_file():
        return None, {"error": "not a regular file"}
    return p_resolved, None


def _symbols_payload(content_bytes: bytes, language: Optional[str]) -> list[dict]:
    if not language:
        return []
    try:
        return [asdict(s) for s in symbols(content_bytes, language)]
    except Exception:
        return []


def run_read(args: dict, cwd: Path) -> dict:
    path_str = args.get("path")
    if not path_str:
        return {"error": "`path` is required"}
    mode = (args.get("mode") or "truncated").lower()
    if mode not in ("full", "truncated", "skeleton"):
        return {"error": f"mode must be one of full|truncated|skeleton; got {mode!r}"}
    keep_symbol = args.get("symbol") or None

    p_resolved, err = _resolve_path(path_str, cwd)
    if err is not None:
        return err
    assert p_resolved is not None

    raw = p_resolved.read_bytes()
    if _is_binary(raw):
        return {"error": "binary file"}
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "not utf-8"}

    rel = str(p_resolved.relative_to(cwd.resolve()))
    total_lines = _line_count(content)
    language = language_for_path(str(p_resolved))
    note: Optional[str] = None
    effective_mode = mode

    if mode == "full" or language is None:
        if mode != "full" and language is None:
            note = "unsupported language; returning verbatim"
            effective_mode = "full"
        out, capped = _hard_cap(content)
        result = {
            "path": rel,
            "language": language or "plain",
            "mode": effective_mode,
            "lines": total_lines,
            "truncated_to_lines": _line_count(out),
            "content": out,
            "size_capped": capped,
            "symbols": _symbols_payload(raw, language),
        }
        if note:
            result["note"] = note
        return result

    if mode == "skeleton":
        rendered = _skeleton(content, language)
        out, capped = _hard_cap(rendered)
        return {
            "path": rel,
            "language": language,
            "mode": "skeleton",
            "lines": total_lines,
            "truncated_to_lines": _line_count(out),
            "content": out,
            "size_capped": capped,
            "symbols": _symbols_payload(raw, language),
        }

    # mode == "truncated"
    try:
        truncated_bytes = truncate(raw, language, keep_symbol=keep_symbol)
    except Exception as e:  # noqa: BLE001 — graceful fallback
        out, capped = _hard_cap(content)
        return {
            "path": rel,
            "language": language,
            "mode": "full",
            "lines": total_lines,
            "truncated_to_lines": _line_count(out),
            "content": out,
            "size_capped": capped,
            "symbols": _symbols_payload(raw, language),
            "note": f"AST truncation failed, returning verbatim: {e}",
        }

    # If truncate() returned the source unchanged, the file had a syntax error
    # or no truncatable bodies. Either way, surface it as mode=full with a note
    # when a syntax error is suspected.
    if truncated_bytes == raw:
        tree = parse(raw, language)
        if tree.root_node.has_error:
            note = "syntax error in source; truncation skipped"
            effective_mode = "full"

    rendered = truncated_bytes.decode("utf-8", errors="replace")
    out, capped = _hard_cap(rendered)
    result = {
        "path": rel,
        "language": language,
        "mode": effective_mode,
        "lines": total_lines,
        "truncated_to_lines": _line_count(out),
        "content": out,
        "size_capped": capped,
        "symbols": _symbols_payload(raw, language),
    }
    if note:
        result["note"] = note
    return result
