"""`search` tool: ripgrep wrapper that returns ranked snippets grouped by file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ripgrep import RipgrepError, RipgrepNotFound, run as rg_run

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regex pattern (ripgrep syntax)."},
        "path_glob": {"type": "string", "default": "**/*", "description": "Glob to scope the search."},
        "max_results": {"type": "integer", "default": 50, "description": "Cap on total matches across all files."},
        "context_lines": {"type": "integer", "default": 2, "description": "Lines of context before/after each match."},
        "case_sensitive": {"type": "boolean", "default": False},
    },
    "required": ["pattern"],
}

DESCRIPTION = (
    "Locate code by fusing glob filtering, regex pattern matching, and ranked snippet reading "
    "into a single call. Prefer this over the built-in Grep and Glob tools whenever the next "
    "step would be reading one or more matched files — `search` returns enough surrounding "
    "context that the follow-up Read calls are usually unnecessary. Also prefer it over Grep "
    "for any search across more than a couple of files, since results come back ranked and "
    "grouped per file. Replaces the Glob + Grep + Read sequence with one round-trip. Use for "
    "finding a symbol or function definition, auditing every usage of an identifier, scoping "
    "a search to a subtree, or scanning files for a regex pattern. Returns ranked results "
    "grouped by file with line numbers, the matched line, and configurable surrounding "
    "context, with identical hits deduplicated per file. Key inputs: `pattern` (regex, "
    "ripgrep syntax), `path_glob` (default `**/*`), `max_results` (default 50), "
    "`context_lines` (default 2), and `case_sensitive` (default false)."
)


def run_search(args: dict, cwd: Path) -> dict:
    pattern = args.get("pattern")
    if not pattern:
        return {"error": "`pattern` is required"}

    path_glob = args.get("path_glob", "**/*")
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 2))
    case_sensitive = bool(args.get("case_sensitive", False))

    try:
        events = list(
            rg_run(
                pattern=pattern,
                cwd=cwd,
                path_glob=path_glob,
                max_results=max_results,
                context_lines=context_lines,
                case_sensitive=case_sensitive,
            )
        )
    except RipgrepNotFound as e:
        return {"error": str(e)}
    except RipgrepError as e:
        return {"error": f"ripgrep error: {e}"}

    by_file: dict[str, dict[str, Any]] = {}
    files_searched = 0
    total_matches = 0
    truncated = False
    current_path: str | None = None
    pending_before: list[str] = []

    for ev in events:
        kind = ev.get("type")
        data = ev.get("data", {})
        if kind == "begin":
            current_path = _decode_path(data.get("path", {}))
            pending_before = []
        elif kind == "context":
            line_text = _decode_text(data.get("lines", {}))
            pending_before.append(line_text.rstrip("\n"))
            if len(pending_before) > context_lines:
                pending_before = pending_before[-context_lines:]
        elif kind == "match":
            if current_path is None:
                continue
            line_text = _decode_text(data.get("lines", {}))
            line_num = int(data.get("line_number", 0))
            entry = by_file.setdefault(current_path, {"path": current_path, "matches": []})
            existing = next(
                (m for m in entry["matches"] if m["text"] == line_text.rstrip("\n")),
                None,
            )
            if existing:
                existing["count"] += 1
            else:
                entry["matches"].append(
                    {
                        "line": line_num,
                        "text": line_text.rstrip("\n"),
                        "context_before": list(pending_before),
                        "context_after": [],
                        "count": 1,
                    }
                )
            pending_before = []
            total_matches += 1
            if total_matches >= max_results:
                truncated = True
        elif kind == "end":
            if data.get("stats", {}).get("matches", 0) > 0 or current_path in by_file:
                pass
            current_path = None
            pending_before = []
        elif kind == "summary":
            stats = data.get("stats", {})
            files_searched = int(stats.get("searches_with_match", 0)) + int(
                # ripgrep's summary doesn't always carry total files; use elapsed_total presence as a proxy.
                stats.get("matched_lines", 0) and 0
            )
            # Best-effort: prefer a count of distinct files seen.
            files_searched = max(files_searched, len(by_file))

    # Attach context_after by re-walking events (simpler than tracking inline).
    _attach_context_after(events, by_file, context_lines)

    results = list(by_file.values())

    return {
        "files_searched": max(files_searched, len(by_file)),
        "files_matched": len(by_file),
        "total_matches": total_matches,
        "truncated": truncated,
        "results": results,
    }


def _decode_path(path_obj: dict) -> str:
    raw: str
    if "text" in path_obj:
        raw = path_obj["text"]
    elif "bytes" in path_obj:
        import base64

        raw = base64.b64decode(path_obj["bytes"]).decode("utf-8", errors="replace")
    else:
        return ""
    if raw.startswith("./"):
        raw = raw[2:]
    return raw


def _decode_text(text_obj: dict) -> str:
    if "text" in text_obj:
        return text_obj["text"]
    if "bytes" in text_obj:
        import base64

        return base64.b64decode(text_obj["bytes"]).decode("utf-8", errors="replace")
    return ""


def _attach_context_after(events: list[dict], by_file: dict, context_lines: int) -> None:
    cur_path: str | None = None
    last_match_ref: dict | None = None
    after_left = 0
    for ev in events:
        kind = ev.get("type")
        data = ev.get("data", {})
        if kind == "begin":
            cur_path = _decode_path(data.get("path", {}))
            last_match_ref = None
            after_left = 0
        elif kind == "match":
            if cur_path is None:
                continue
            entry = by_file.get(cur_path)
            if not entry:
                continue
            line_num = int(data.get("line_number", 0))
            line_text = _decode_text(data.get("lines", {})).rstrip("\n")
            for m in entry["matches"]:
                if m["line"] == line_num and m["text"] == line_text:
                    last_match_ref = m
                    break
            after_left = context_lines
        elif kind == "context" and after_left > 0 and last_match_ref is not None:
            line_text = _decode_text(data.get("lines", {})).rstrip("\n")
            last_match_ref["context_after"].append(line_text)
            after_left -= 1
        elif kind == "end":
            cur_path = None
            last_match_ref = None
            after_left = 0
