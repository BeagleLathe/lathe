"""`search` tool: ripgrep wrapper that returns ranked snippets as a flat list.

Output shape (post-L1 compact form):

    {
      "files_searched": int,
      "files_matched": int,
      "total_matches": int,
      "truncated": bool,
      "matches": [
        {"path": str, "line": int, "text": str, "count": int,
         "before": [str], "after": [str]},
        ...
      ],
    }

`before`/`after` keys are omitted entirely when their lists would be empty.
Within `matches`, ripgrep's file-grouped order is preserved (all matches for
one file appear consecutively, in source order), so agents that want to
re-group by file can do so with a single pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ripgrep import RipgrepError, RipgrepNotFound, run as rg_run

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regex (ripgrep syntax)."},
        "path_glob": {"type": "string", "default": "**/*"},
        "max_results": {"type": "integer", "default": 50},
        "context_lines": {"type": "integer", "default": 5},
        "max_total_lines": {"type": "integer", "default": 400},
        "case_sensitive": {"type": "boolean", "default": False},
        "force": {"type": "boolean", "default": False, "description": "Bypass output dedup."},
    },
    "required": ["pattern"],
}

DESCRIPTION = (
    "Find code in one call instead of grep-then-read. Prefer over built-in Grep/Glob when "
    "the next step would be reading the matched files — returned snippets usually remove "
    "the follow-up Read. Results are ranked, file-grouped, with line numbers and "
    "configurable context."
)


def run_search(args: dict, cwd: Path) -> dict:
    pattern = args.get("pattern")
    if not pattern:
        return {"error": "`pattern` is required"}

    path_glob = args.get("path_glob", "**/*")
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 5))
    max_total_lines = int(args.get("max_total_lines", 400))
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

    # Stage 1: walk events to collect per-file match records (with per-line
    # dedup + count), preserving ripgrep's file order in `file_order`.
    by_file: dict[str, list[dict[str, Any]]] = {}
    file_order: list[str] = []
    files_seen: set[str] = set()
    total_matches = 0
    truncated = False
    current_path: str | None = None
    pending_before: list[str] = []

    for ev in events:
        kind = ev.get("type")
        data = ev.get("data", {})
        if kind == "begin":
            current_path = _decode_path(data.get("path", {}))
            if current_path not in files_seen:
                files_seen.add(current_path)
                file_order.append(current_path)
            pending_before = []
        elif kind == "context":
            line_text = _decode_text(data.get("lines", {}))
            pending_before.append(line_text.rstrip("\n"))
            if len(pending_before) > context_lines:
                pending_before = pending_before[-context_lines:]
        elif kind == "match":
            if current_path is None:
                continue
            line_text = _decode_text(data.get("lines", {})).rstrip("\n")
            line_num = int(data.get("line_number", 0))
            file_matches = by_file.setdefault(current_path, [])
            existing = next((m for m in file_matches if m["text"] == line_text), None)
            if existing:
                existing["count"] += 1
            else:
                entry: dict[str, Any] = {
                    "path": current_path,
                    "line": line_num,
                    "text": line_text,
                    "count": 1,
                }
                if pending_before:
                    entry["before"] = list(pending_before)
                entry["_after_buf"] = []  # internal; stripped before return
                file_matches.append(entry)
            pending_before = []
            total_matches += 1
            if total_matches >= max_results:
                truncated = True
        elif kind == "end":
            current_path = None
            pending_before = []

    # Stage 2: attach context_after by re-walking events.
    _attach_context_after(events, by_file, context_lines)

    # Stage 3: flatten in file-order, finalize per-match shape (omit empty
    # `after`, strip internal `_after_buf`).
    flat: list[dict[str, Any]] = []
    for path in file_order:
        for m in by_file.get(path, []):
            after = m.pop("_after_buf", [])
            if after:
                m["after"] = after
            flat.append(m)

    # Stage 4: cap total context output. Drop hits from the bottom of the
    # ranked (ripgrep-order) list until total before+after lines fits under
    # the cap. Larger default context means a broad search can balloon —
    # without this cap a 200-hit search × 10 ctx-lines/hit hits 2000 lines.
    context_truncated = False
    if max_total_lines > 0:
        running = 0
        kept: list[dict[str, Any]] = []
        for m in flat:
            ctx = len(m.get("before", [])) + len(m.get("after", []))
            if running + ctx > max_total_lines:
                context_truncated = True
                break
            running += ctx
            kept.append(m)
        flat = kept

    out: dict[str, Any] = {
        "files_searched": len(files_seen),
        "files_matched": len(by_file),
        "total_matches": total_matches,
        "truncated": truncated,
        "matches": flat,
    }
    if context_truncated:
        out["context_truncated"] = True
    return out


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


def _attach_context_after(
    events: list[dict],
    by_file: dict[str, list[dict[str, Any]]],
    context_lines: int,
) -> None:
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
            file_matches = by_file.get(cur_path)
            if not file_matches:
                continue
            line_num = int(data.get("line_number", 0))
            line_text = _decode_text(data.get("lines", {})).rstrip("\n")
            for m in file_matches:
                if m["line"] == line_num and m["text"] == line_text:
                    last_match_ref = m
                    break
            after_left = context_lines
        elif kind == "context" and after_left > 0 and last_match_ref is not None:
            line_text = _decode_text(data.get("lines", {})).rstrip("\n")
            last_match_ref["_after_buf"].append(line_text)
            after_left -= 1
        elif kind == "end":
            cur_path = None
            last_match_ref = None
            after_left = 0
