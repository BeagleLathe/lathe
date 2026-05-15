"""`extract_todos` tool: find TODO/FIXME/XXX/HACK comments with blame metadata.

Replaces a manual grep + per-line git-blame loop with one structured call:
returns each marker with file:line, surrounding context, and (when in a git
repo) the author and age in days from `git blame`.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import ripgrep

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path_glob": {
            "type": "string",
            "description": "Limit scan to files matching this glob. Default: **/*",
        },
        "markers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Comment markers to look for. Default: ['TODO', 'FIXME', 'XXX', 'HACK'].",
        },
        "include_blame": {
            "type": "boolean",
            "description": "Include git blame author + age when in a git repo. Default: true.",
            "default": True,
        },
        "context_lines": {
            "type": "integer",
            "description": "Lines of context around each match. Default: 1.",
            "default": 1,
        },
        "sort_by": {
            "type": "string",
            "enum": ["age", "file"],
            "description": "Sort order: oldest-first by age, or by file path. Default: age.",
            "default": "age",
        },
        "max_results": {
            "type": "integer",
            "description": "Cap on results. Default: 100.",
            "default": 100,
        },
    },
    "required": [],
}

DESCRIPTION = (
    "Scan the codebase for TODO / FIXME / XXX / HACK comments and return them structured: "
    "marker, file:line, the comment text, surrounding context, and (in git repos) author + "
    "age in days via blame. Sort by age (oldest first) or by file. Replaces grep + per-line "
    "blame loop with one call."
)

DEFAULT_MARKERS = ("TODO", "FIXME", "XXX", "HACK")
RIPGREP_TIMEOUT = 30
BLAME_TIMEOUT = 15
MAX_BLAMED_FILES = 200


def run_extract_todos(args: dict, cwd: Path) -> dict:
    path_glob = args.get("path_glob") or "**/*"
    markers_raw = args.get("markers")
    markers: tuple[str, ...]
    if markers_raw:
        cleaned = tuple(m for m in markers_raw if isinstance(m, str) and m.strip())
        markers = cleaned or DEFAULT_MARKERS
    else:
        markers = DEFAULT_MARKERS
    include_blame = bool(args.get("include_blame", True))
    try:
        context_lines = int(args.get("context_lines") or 1)
    except (TypeError, ValueError):
        return {"error": "`context_lines` must be an integer"}
    if context_lines < 0:
        context_lines = 0
    sort_by = args.get("sort_by") or "age"
    if sort_by not in ("age", "file"):
        return {"error": "`sort_by` must be 'age' or 'file'"}
    try:
        max_results = int(args.get("max_results") or 100)
    except (TypeError, ValueError):
        return {"error": "`max_results` must be an integer"}
    if max_results <= 0:
        max_results = 100

    pattern = r"\b(?:" + "|".join(re.escape(m) for m in markers) + r")\b"

    try:
        events = list(
            ripgrep.run(
                pattern=pattern,
                cwd=cwd,
                path_glob=path_glob,
                max_results=max_results * 2,
                context_lines=context_lines,
                case_sensitive=True,
                timeout=RIPGREP_TIMEOUT,
            )
        )
    except ripgrep.RipgrepNotFound:
        return {"error": "ripgrep not found"}
    except ripgrep.RipgrepError as e:
        return {"error": f"ripgrep failed: {e}"}

    matches = _collect_matches(events, markers, context_lines, cwd)
    if include_blame and matches:
        _attach_blame(matches, cwd)

    if sort_by == "age":
        # Oldest first; entries without blame (no age_days) sort last.
        matches.sort(
            key=lambda m: (m.get("age_days") is None, -(m.get("age_days") or 0), m["file"], m["line"])
        )
    else:
        matches.sort(key=lambda m: (m["file"], m["line"]))

    total = len(matches)
    truncated = total > max_results
    visible = matches[:max_results]

    by_marker: dict[str, int] = {}
    for m in matches:
        by_marker[m["marker"]] = by_marker.get(m["marker"], 0) + 1

    return {
        "todos": visible,
        "total": total,
        "by_marker": by_marker,
        "truncated": truncated,
    }


_MARKER_TEXT_RE = re.compile(
    r"(?P<marker>TODO|FIXME|XXX|HACK)(?:\([^)]*\))?\s*[:\-]?\s*(?P<text>.*)"
)


def _collect_matches(
    events: list[dict],
    markers: tuple[str, ...],
    context_lines: int,
    cwd: Path,
) -> list[dict]:
    marker_set = {m.upper() for m in markers}
    by_path: dict[str, list[tuple[int, str]]] = {}  # path -> [(lineno, text)]
    for ev in events:
        if ev.get("type") != "match":
            continue
        data = ev.get("data") or {}
        path = ((data.get("path") or {}).get("text")) or ""
        lineno = int(data.get("line_number") or 0)
        text = ((data.get("lines") or {}).get("text")) or ""
        if not path or not lineno:
            continue
        by_path.setdefault(path, []).append((lineno, text.rstrip("\n")))

    matches: list[dict] = []
    for path, line_entries in by_path.items():
        # Load the file once for context excerpts. ripgrep emits paths relative
        # to its working directory, so resolve them against `cwd`.
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            file_lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            file_lines = None

        for lineno, text in line_entries:
            mm = _MARKER_TEXT_RE.search(text)
            if not mm:
                continue
            marker = mm.group("marker").upper()
            if marker not in marker_set:
                continue
            todo_text = mm.group("text").strip() or text.strip()
            context = _build_context(file_lines, lineno, context_lines)
            matches.append({
                "marker": marker,
                "file": path,
                "line": lineno,
                "text": todo_text,
                "context": context,
            })
    return matches


def _build_context(file_lines: list[str] | None, lineno: int, context: int) -> str | None:
    if not file_lines or context <= 0:
        return None
    lo = max(0, lineno - 1 - context)
    hi = min(len(file_lines), lineno + context)
    if lo >= hi:
        return None
    return "\n".join(f"  {i + 1}: {file_lines[i]}" for i in range(lo, hi)) + "\n"


def _attach_blame(matches: list[dict], cwd: Path) -> None:
    by_file: dict[str, list[dict]] = {}
    for m in matches:
        by_file.setdefault(m["file"], []).append(m)
    if len(by_file) > MAX_BLAMED_FILES:
        # Too many files; skip blame altogether rather than thrashing.
        return

    now = int(time.time())
    for file, entries in by_file.items():
        blame = _blame_file(file, cwd)
        if blame is None:
            continue
        for entry in entries:
            info = blame.get(entry["line"])
            if not info:
                continue
            author_time = info.get("author_time")
            entry["author"] = info.get("author_mail") or info.get("author")
            entry["commit"] = info.get("sha")
            if author_time is not None:
                entry["age_days"] = max(0, (now - author_time) // 86400)


def _blame_file(file: str, cwd: Path) -> dict[int, dict] | None:
    try:
        proc = subprocess.run(
            ["git", "blame", "--line-porcelain", "--", file],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=BLAME_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None

    result: dict[int, dict] = {}
    current: dict[str, Any] = {}
    current_lineno: int | None = None
    for raw in (proc.stdout or "").splitlines():
        if raw.startswith("\t"):
            # End of one porcelain block; record it.
            if current_lineno is not None:
                result[current_lineno] = current
            current = {}
            current_lineno = None
            continue
        if not raw:
            continue
        parts = raw.split(" ", 3)
        head = parts[0]
        if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
            current = {"sha": head[:7]}
            if len(parts) >= 3:
                try:
                    current_lineno = int(parts[2])
                except ValueError:
                    current_lineno = None
            continue
        rest = raw.split(" ", 1)
        key = rest[0]
        val = rest[1] if len(rest) > 1 else ""
        if key == "author":
            current["author"] = val
        elif key == "author-mail":
            current["author_mail"] = val.strip("<>")
        elif key == "author-time":
            try:
                current["author_time"] = int(val)
            except ValueError:
                pass
    return result
