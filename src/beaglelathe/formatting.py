"""Render tool result dicts as human-readable text for the Claude Code UI.

Each `_render_*` function takes the original `args` and the result dict and
returns a plain-text block intended for the MCP text response.

The structured dict is NOT emitted on the wire by default — Claude Code
renders `structuredContent` in preference to the text block, which would
shadow the formatted output with a raw JSON envelope. Programmatic
consumers (smoke scripts, future tooling) can opt back in by setting
`BEAGLELATHE_EXPOSE_STRUCTURED=1` in the server's environment; see
`server._mcp_call_tool`.

If a renderer raises or no renderer exists for a tool, we fall back to
indented JSON so the response is still complete.
"""

from __future__ import annotations

import json
from typing import Callable


def format_result(name: str, args: dict | None, result: dict) -> str:
    """Convert a tool result dict to a human-readable text block."""
    args = args or {}
    notice: str | None = None
    payload = result
    if "_notice" in result:
        notice = result.get("_notice")
        payload = {k: v for k, v in result.items() if k != "_notice"}

    # Dedup stub — emitted by the per-session output cache when the payload
    # matches a recent call. Same shape for any tool that's covered.
    if "unchanged_since_turn" in payload:
        body = _render_dedup_stub(name, payload)
        if notice:
            return f"{notice}\n\n{body}"
        return body

    renderer = _RENDERERS.get(name)
    try:
        body = renderer(args, payload) if renderer else _fallback(payload)
    except Exception:
        body = _fallback(payload)

    if notice:
        return f"{notice}\n\n{body}"
    return body


def _render_dedup_stub(name: str, r: dict) -> str:
    return (
        f"{name}: unchanged since turn {r.get('unchanged_since_turn', '?')} "
        f"(current turn {r.get('current_turn', '?')}). "
        f"Pass force=true to re-emit."
    )


def _fallback(result: dict) -> str:
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Shared error helper
# ---------------------------------------------------------------------------


def _error_block(r: dict, header: str | None = None) -> str:
    out: list[str] = []
    if header:
        out.append(header)
    type_ = r.get("_type")
    prefix = "error"
    if type_ == "user_error":
        prefix = "error (user)"
    elif type_ == "system_error":
        prefix = "error (system)"
    out.append(f"{prefix}: {r.get('error', '')}")
    hint = r.get("hint")
    if hint:
        out.append(f"hint: {hint}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Per-tool renderers
# ---------------------------------------------------------------------------


def _render_sh(args: dict, r: dict) -> str:
    cmd = (args.get("command") or "").strip()
    header = f"$ {cmd}" if cmd else "$"
    if "error" in r:
        return _error_block(r, header=header)
    out = [header]
    stdout = (r.get("stdout") or "").rstrip("\n")
    if stdout:
        out.append(stdout)
    stderr = (r.get("stderr") or "").rstrip("\n")
    if stderr:
        out.append("--- stderr ---")
        out.append(stderr)
    suffix = " (output truncated)" if r.get("truncated") else ""
    out.append(f"exit {r.get('exit_code', '?')}{suffix}")
    return "\n".join(out)


def _render_read(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header=f"read {args.get('path', '')}")
    path = r.get("path", "")
    language = r.get("language") or ""
    mode = r.get("mode", "")
    head_bits = [f"read {path}"]
    if language:
        head_bits.append(f"({language}, mode={mode})")
    out = [" ".join(head_bits)]

    total = r.get("lines")
    shown = r.get("truncated_to_lines")
    if total is not None and shown is not None and shown != total:
        out.append(f"showing {shown}/{total} lines")
    elif total is not None:
        out.append(f"{total} lines")
    if r.get("size_capped"):
        out.append("(size cap applied)")
    if r.get("note"):
        out.append(f"note: {r['note']}")
    content = (r.get("content") or "").rstrip("\n")
    out.append("")
    out.append(content)
    return "\n".join(out)


def _render_search(args: dict, r: dict) -> str:
    pat = args.get("pattern", "")
    glob = args.get("path_glob") or ""
    head_bits = [f"search /{pat}/"]
    if glob:
        head_bits.append(f"in {glob}")
    head = " ".join(head_bits)
    if "error" in r:
        return _error_block(r, header=head)
    total = r.get("total_matches", 0)
    files_matched = r.get("files_matched", 0)
    files_searched = r.get("files_searched", 0)
    out = [head, f"{total} match(es) in {files_matched}/{files_searched} file(s)"]
    if r.get("truncated"):
        out.append("(results truncated)")
    last_path: str | None = None
    for m in r.get("matches") or []:
        path = m.get("path", "")
        ln = m.get("line", 0)
        text = m.get("text", "")
        if path != last_path:
            out.append("")
            out.append(path)
            last_path = path
        for i, ctx in enumerate(m.get("before") or []):
            out.append(f"  {ln - len(m['before']) + i}: {ctx}")
        count = m.get("count", 1)
        mult = f"  (x{count})" if count > 1 else ""
        out.append(f"  {ln}: {text}{mult}")
        for i, ctx in enumerate(m.get("after") or []):
            out.append(f"  {ln + 1 + i}: {ctx}")
    return "\n".join(out)


def _render_edit(args: dict, r: dict) -> str:
    # `error` can co-exist with `applied: 0` when no edits were provided.
    if "error" in r and not r.get("results"):
        return _error_block(r, header="edit")
    applied = r.get("applied", 0)
    failed = r.get("failed", 0)
    out = [f"edit: {applied} applied, {failed} failed"]
    for item in r.get("results") or []:
        path = item.get("path", "")
        status = item.get("status", "")
        line = f"  {status}: {path}"
        if status == "applied":
            mt = item.get("match_type")
            if mt:
                bits = [mt]
                fs = item.get("fuzzy_score")
                if fs is not None:
                    bits.append(f"score={fs}")
                line += f" ({', '.join(bits)})"
            validation = item.get("validation")
            if validation and validation != "ok":
                line += f" [validation={validation}]"
        out.append(line)
        if item.get("reason"):
            out.append(f"    reason: {item['reason']}")
        if item.get("validation_detail"):
            out.append(f"    validation_detail: {item['validation_detail']}")
        diff = item.get("diff")
        if diff:
            out.append("    diff:")
            for dl in diff.splitlines():
                out.append(f"      {dl}")
    return "\n".join(out)


def _render_run_tests(args: dict, r: dict) -> str:
    if "error" in r and "summary" not in r:
        return _error_block(r, header="run_tests")
    framework = r.get("framework", "?")
    cmd = r.get("command", "")
    out = [f"{framework}: {cmd}" if cmd else framework]
    dur = r.get("duration_ms")
    if dur is not None:
        out.append(f"duration: {dur}ms")
    s = r.get("summary") or {}
    if s:
        summary_bits = (
            f"summary: {s.get('total', 0)} total, "
            f"{s.get('passed', 0)} passed, "
            f"{s.get('failed', 0)} failed, "
            f"{s.get('skipped', 0)} skipped"
        )
        if s.get("errors"):
            summary_bits += f", {s['errors']} errors"
        out.append(summary_bits)
    if r.get("note"):
        out.append(f"note: {r['note']}")
    err_out = r.get("error_output")
    if err_out:
        out.append("--- error output ---")
        out.append(err_out)
    for f in r.get("failures") or []:
        out.append("")
        loc = ""
        if f.get("file"):
            loc = f["file"]
            if f.get("line"):
                loc += f":{f['line']}"
        head = f"FAIL {f.get('name', '')}"
        if loc:
            head += f"  ({loc})"
        out.append(head)
        if f.get("message"):
            out.append(f"  {f['message']}")
        if f.get("source_excerpt"):
            for el in f["source_excerpt"].rstrip("\n").splitlines():
                out.append(el)
        if f.get("traceback_tail"):
            out.append("  traceback:")
            for tl in f["traceback_tail"].splitlines():
                out.append(f"    {tl}")
    if r.get("truncated_failures"):
        out.append("")
        out.append("(failures truncated)")
    if r.get("stdout"):
        out.append("--- stdout ---")
        out.append(r["stdout"].rstrip("\n"))
    if r.get("stderr"):
        out.append("--- stderr ---")
        out.append(r["stderr"].rstrip("\n"))
    if "exit_code" in r:
        out.append(f"exit {r['exit_code']}")
    return "\n".join(out)


def _render_git_status(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="git_status")
    out: list[str] = []
    branch = r.get("branch") or "(detached)"
    upstream = r.get("upstream")
    head = f"branch: {branch}"
    if upstream:
        head += f" -> {upstream}"
    ab_bits: list[str] = []
    if r.get("ahead"):
        ab_bits.append(f"ahead {r['ahead']}")
    if r.get("behind"):
        ab_bits.append(f"behind {r['behind']}")
    if ab_bits:
        head += f" ({', '.join(ab_bits)})"
    out.append(head)
    if r.get("detached"):
        out.append(f"detached at {r.get('head_sha', '')}")

    def _section(label: str, items: list) -> None:
        if not items:
            return
        out.append(f"{label} ({len(items)}):")
        for it in items:
            out.append(f"  {it}")

    _section("staged", r.get("staged") or [])
    _section("unstaged", r.get("unstaged") or [])
    _section("untracked", r.get("untracked") or [])
    _section("conflicts", r.get("conflicts") or [])
    renamed = r.get("renamed") or []
    if renamed:
        out.append(f"renamed ({len(renamed)}):")
        for rn in renamed:
            out.append(f"  {rn.get('from', '')} -> {rn.get('to', '')}")
    if not any([
        r.get("staged"),
        r.get("unstaged"),
        r.get("untracked"),
        r.get("conflicts"),
        renamed,
    ]):
        out.append("clean")
    return "\n".join(out)


def _render_git_diff(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="git_diff")
    files = r.get("files") or []
    if not files:
        return "git_diff: no changes"
    out = [f"git_diff: {len(files)} file(s)"]
    if r.get("truncated_files"):
        out.append("(file list truncated)")
    if r.get("truncated_hunks"):
        out.append("(per-file hunk list truncated)")
    for f in files:
        out.append("")
        path = f.get("path", "")
        status = f.get("status", "modified")
        add = f.get("additions", 0)
        delete = f.get("deletions", 0)
        out.append(f"{status} {path}  +{add} -{delete}")
        if f.get("from") and f.get("to"):
            out.append(f"  {f['from']} -> {f['to']}")
        if f.get("binary"):
            out.append("  (binary)")
        for h in f.get("hunks") or []:
            out.append(h.get("header", ""))
            content = h.get("content") or ""
            for cl in content.splitlines():
                out.append(cl)
    return "\n".join(out)


def _render_changed_files(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="changed_files")
    branch = r.get("branch_compared", "")
    totals = r.get("totals") or {}
    out = [
        f"changed_files vs {branch}: "
        f"{totals.get('files', 0)} files, "
        f"+{totals.get('additions', 0)} -{totals.get('deletions', 0)}"
    ]
    for f in r.get("files") or []:
        out.append(
            f"  {f.get('status', '?')} {f.get('path', '')}"
            f"  +{f.get('additions', 0)} -{f.get('deletions', 0)}"
        )
    return "\n".join(out)


def _render_lint(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="lint_and_typecheck")
    s = r.get("summary") or {}
    level = r.get("level", "full")
    out = [
        f"lint_and_typecheck (level={level}): "
        f"{s.get('errors', 0)} errors, {s.get('warnings', 0)} warnings"
    ]
    ran = r.get("ran_runners") or []
    skipped = r.get("skipped_runners") or []
    errored = r.get("errored_runners") or []
    if ran:
        out.append(f"ran: {', '.join(ran)}")
    if skipped:
        out.append(f"skipped: {', '.join(skipped)}")
    if errored:
        out.append("errored:")
        for e in errored:
            out.append(f"  {e.get('runner', '?')}: {e.get('stderr_tail', '')}")
    if r.get("note"):
        out.append(f"note: {r['note']}")
    for it in r.get("issues") or []:
        loc = it.get("file", "")
        if it.get("line"):
            loc += f":{it['line']}"
            if it.get("column"):
                loc += f":{it['column']}"
        prefix_bits = [it.get("severity", "warning")]
        if it.get("code"):
            prefix_bits.append(it["code"])
        if it.get("source"):
            prefix_bits.append(f"[{it['source']}]")
        out.append(f"  {loc}  {' '.join(prefix_bits)}: {it.get('message', '')}")
    if r.get("truncated"):
        out.append("(issues truncated)")
    return "\n".join(out)


def _render_extract_todos(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="extract_todos")
    out = [f"extract_todos: {r.get('total', 0)} match(es)"]
    by_marker = r.get("by_marker") or {}
    if by_marker:
        parts = [f"{k}={v}" for k, v in sorted(by_marker.items())]
        out.append(f"by marker: {', '.join(parts)}")
    if r.get("truncated"):
        out.append("(results truncated)")
    for t in r.get("todos") or []:
        out.append("")
        loc = f"{t.get('file', '')}:{t.get('line', '')}"
        head = f"{t.get('marker', '')} {loc}"
        if t.get("author"):
            head += f"  ({t['author']}"
            if t.get("age_days") is not None:
                head += f", {t['age_days']}d old"
            head += ")"
        out.append(head)
        if t.get("text"):
            out.append(f"  {t['text']}")
        if t.get("context"):
            for cl in t["context"].rstrip("\n").splitlines():
                out.append(cl)
    return "\n".join(out)


def _render_commit_message(args: dict, r: dict) -> str:
    if "error" in r:
        return _error_block(r, header="commit_message")
    out = [
        f"commit_message (source={r.get('source', '?')})",
        f"type:    {r.get('type', '')}",
        f"scope:   {r.get('scope') or '-'}",
        f"subject: {r.get('subject', '')}",
        "",
        "message:",
        r.get("message", ""),
    ]
    files = r.get("files") or []
    if files:
        out.append("")
        out.append(f"files considered ({len(files)}):")
        for f in files:
            out.append(f"  {f.get('status', '?')} {f.get('path', '')}")
    return "\n".join(out)


def _render_edit_glob(args: dict, r: dict) -> str:
    if "error" in r and not r.get("results"):
        return _error_block(r, header="edit_glob")
    files_matched = r.get("files_matched", 0)
    files_changed = r.get("files_changed", 0)
    total_subs = r.get("total_subs", 0)
    out = [
        f"edit_glob: {files_changed}/{files_matched} files changed, "
        f"{total_subs} substitutions"
    ]
    if r.get("capped"):
        out.append("  (max_files cap hit — some matched files were skipped)")
    for item in r.get("results") or []:
        if item.get("subs", 0) == 0:
            continue
        out.append(f"  {item['path']}: {item['subs']} subs")
    return "\n".join(out)


_RENDERERS: dict[str, Callable[[dict, dict], str]] = {
    "sh": _render_sh,
    "read": _render_read,
    "search": _render_search,
    "edit": _render_edit,
    "edit_glob": _render_edit_glob,
    "run_tests": _render_run_tests,
    "git_status": _render_git_status,
    "git_diff": _render_git_diff,
    "changed_files": _render_changed_files,
    "lint_and_typecheck": _render_lint,
    "extract_todos": _render_extract_todos,
    "commit_message": _render_commit_message,
}
