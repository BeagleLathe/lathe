"""Structured git tools: `git_status`, `git_diff`, `changed_files`.

Each replaces the typical 1-3 Bash calls + raw-output reparse cycle with one
structured JSON result. They share a `_run_git` helper that wraps `git` with
the same subprocess-style guards used by `tools/sh.py`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


class _GitError(RuntimeError):
    """Raised when a git invocation fails in a way the caller should surface."""


def _run_git(
    cwd: Path,
    args: list[str],
    timeout: int,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise _GitError(f"git timed out after {timeout}s")
    except FileNotFoundError:
        raise _GitError("git executable not found on PATH")


def _is_inside_repo(cwd: Path) -> bool:
    try:
        proc = _run_git(cwd, ["rev-parse", "--is-inside-work-tree"], timeout=5)
    except _GitError:
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


# ---------------------------------------------------------------------------
# Tool 1 -- git_status
# ---------------------------------------------------------------------------

GIT_STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_untracked": {
            "type": "boolean",
            "description": "Include untracked files in the result. Default true.",
            "default": True,
        },
    },
    "required": [],
}

GIT_STATUS_DESC = (
    "Return the structured state of the working tree: branch, upstream, ahead/behind counts, "
    "and lists of staged / unstaged / untracked / renamed / conflicted paths. Detached HEAD is "
    "reported with `detached: true` and `head_sha`. Replaces `git status` + manual parsing with "
    "one round-trip. Use this instead of Bash for any check of repo state."
)

_STATUS_TIMEOUT = 10

# porcelain v2 status codes -> labels for unstaged/staged columns
_STATUS_CHAR_MAP = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "type_changed",
    "U": "unmerged",
}


def run_git_status(args: dict, cwd: Path) -> dict:
    include_untracked = bool(args.get("include_untracked", True))

    if not _is_inside_repo(cwd):
        return {"error": "not a git repository"}

    untracked_flag = "normal" if include_untracked else "no"
    try:
        proc = _run_git(
            cwd,
            ["status", "--porcelain=v2", "--branch", f"--untracked-files={untracked_flag}"],
            timeout=_STATUS_TIMEOUT,
        )
    except _GitError as e:
        return {"error": str(e)}
    if proc.returncode != 0:
        return {"error": f"git status failed: {proc.stderr.strip()[:300]}"}

    return _parse_porcelain_v2(proc.stdout or "")


def _parse_porcelain_v2(text: str) -> dict:
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    detached = False
    head_sha: str | None = None

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    renamed: list[dict] = []
    conflicts: list[str] = []

    for raw in text.splitlines():
        if not raw:
            continue
        if raw.startswith("# branch.oid "):
            head_sha = raw[len("# branch.oid "):].strip()
            continue
        if raw.startswith("# branch.head "):
            name = raw[len("# branch.head "):].strip()
            if name == "(detached)":
                detached = True
            else:
                branch = name
            continue
        if raw.startswith("# branch.upstream "):
            upstream = raw[len("# branch.upstream "):].strip()
            continue
        if raw.startswith("# branch.ab "):
            m = re.match(r"# branch\.ab \+(\d+) -(\d+)", raw)
            if m:
                ahead = int(m.group(1))
                behind = int(m.group(2))
            continue
        if raw.startswith("# "):
            continue

        tag = raw[0]
        if tag == "1":
            # 1 XY sub mH mI mW hH hI path
            parts = raw.split(" ", 8)
            if len(parts) < 9:
                continue
            xy = parts[1]
            path = parts[8]
            _classify_xy(xy, path, staged, unstaged)
        elif tag == "2":
            # 2 XY sub mH mI mW hH hI X<score> new\told
            parts = raw.split(" ", 9)
            if len(parts) < 10:
                continue
            xy = parts[1]
            tail = parts[9]
            if "\t" in tail:
                new_path, old_path = tail.split("\t", 1)
            else:
                new_path, old_path = tail, ""
            renamed.append({"from": old_path, "to": new_path})
            _classify_xy(xy, new_path, staged, unstaged)
        elif tag == "u":
            parts = raw.split(" ", 10)
            if len(parts) >= 11:
                conflicts.append(parts[10])
        elif tag == "?":
            untracked.append(raw[2:])
        # tag == "!" -> ignored; skip

    result: dict[str, Any] = {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "renamed": renamed,
        "conflicts": conflicts,
    }
    if detached:
        result["detached"] = True
        result["head_sha"] = head_sha
    return result


def _classify_xy(xy: str, path: str, staged: list[str], unstaged: list[str]) -> None:
    if len(xy) != 2:
        return
    x, y = xy[0], xy[1]
    if x != "." and x in _STATUS_CHAR_MAP:
        staged.append(path)
    if y != "." and y in _STATUS_CHAR_MAP:
        unstaged.append(path)


# ---------------------------------------------------------------------------
# Tool 2 -- git_diff
# ---------------------------------------------------------------------------

GIT_DIFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Limit diff to this path. Optional."},
        "from_ref": {"type": "string", "description": "Base ref. Optional."},
        "to_ref": {"type": "string", "description": "Target ref. Optional."},
        "staged": {
            "type": "boolean",
            "description": "Diff staged changes only. Default false.",
            "default": False,
        },
        "max_hunks_per_file": {"type": "integer", "default": 20},
        "max_files": {"type": "integer", "default": 50},
    },
    "required": [],
}

GIT_DIFF_DESC = (
    "Return a structured unified diff: per-file additions/deletions and a list of hunks with "
    "`old_start`, `old_lines`, `new_start`, `new_lines`, header, and content. Handles added / "
    "modified / deleted / renamed / copied / type-changed / binary files. Inputs: optional "
    "`path`, `from_ref`, `to_ref`, `staged`. Replaces `git diff` + manual hunk parsing in one "
    "call. Use this instead of Bash for inspecting changes."
)

_DIFF_TIMEOUT = 30

_HUNK_HEADER_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header_tail>.*)"
)
_DIFF_GIT_RE = re.compile(r"diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")


def run_git_diff(args: dict, cwd: Path) -> dict:
    path = args.get("path") or None
    from_ref = args.get("from_ref") or None
    to_ref = args.get("to_ref") or None
    staged = bool(args.get("staged", False))
    max_files = int(args.get("max_files") or 50)
    max_hunks_per_file = int(args.get("max_hunks_per_file") or 20)

    if not _is_inside_repo(cwd):
        return {"error": "not a git repository"}

    cmd = ["diff", "--no-color", "--unified=3"]
    if staged:
        cmd.append("--cached")
    if from_ref and to_ref:
        cmd.extend([from_ref, to_ref])
    elif from_ref:
        cmd.append(from_ref)
    if path:
        cmd.extend(["--", path])

    try:
        proc = _run_git(cwd, cmd, timeout=_DIFF_TIMEOUT)
    except _GitError as e:
        return {"error": str(e)}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "unknown revision" in err or "bad revision" in err:
            ref = from_ref or to_ref or "<unknown>"
            return {"error": f"unknown ref: {ref}"}
        return {"error": f"git diff failed: {err[:300]}"}

    files, truncated_hunks = _parse_unified_diff(
        proc.stdout or "", max_hunks_per_file
    )

    truncated_files = False
    if len(files) > max_files:
        files = files[:max_files]
        truncated_files = True

    return {
        "files": files,
        "truncated_files": truncated_files,
        "truncated_hunks": truncated_hunks,
    }


def _parse_unified_diff(text: str, max_hunks_per_file: int) -> tuple[list[dict], bool]:
    lines = text.splitlines()
    files: list[dict] = []
    truncated_hunks = False
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.startswith("diff --git "):
            i += 1
            continue

        m = _DIFF_GIT_RE.match(line)
        a_path = m.group("a") if m else None
        b_path = m.group("b") if m else None
        entry: dict[str, Any] = {
            "path": b_path or a_path,
            "status": "modified",
            "additions": 0,
            "deletions": 0,
            "hunks": [],
        }

        i += 1
        # File header block, until first hunk or next file.
        while i < n and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
            hdr = lines[i]
            if hdr.startswith("new file mode"):
                entry["status"] = "added"
            elif hdr.startswith("deleted file mode"):
                entry["status"] = "deleted"
            elif hdr.startswith("rename from "):
                entry["status"] = "renamed"
                entry["from"] = hdr[len("rename from "):]
            elif hdr.startswith("rename to "):
                entry["to"] = hdr[len("rename to "):]
                entry["path"] = entry["to"]
            elif hdr.startswith("copy from "):
                entry["status"] = "copied"
                entry["from"] = hdr[len("copy from "):]
            elif hdr.startswith("copy to "):
                entry["to"] = hdr[len("copy to "):]
                entry["path"] = entry["to"]
            elif hdr.startswith("old mode "):
                entry["status"] = "type_changed"
            elif hdr.startswith("Binary files "):
                entry["binary"] = True
            i += 1

        hunks_collected = 0
        while i < n and lines[i].startswith("@@"):
            hm = _HUNK_HEADER_RE.match(lines[i])
            if not hm:
                i += 1
                continue
            header = lines[i]
            old_start = int(hm.group("old_start"))
            old_lines = int(hm.group("old_lines") or 1)
            new_start = int(hm.group("new_start"))
            new_lines = int(hm.group("new_lines") or 1)

            i += 1
            content_lines: list[str] = []
            while i < n and not lines[i].startswith("@@") and not lines[i].startswith("diff --git "):
                cl = lines[i]
                content_lines.append(cl)
                if cl.startswith("+") and not cl.startswith("+++"):
                    entry["additions"] += 1
                elif cl.startswith("-") and not cl.startswith("---"):
                    entry["deletions"] += 1
                i += 1

            if hunks_collected < max_hunks_per_file:
                entry["hunks"].append({
                    "old_start": old_start,
                    "old_lines": old_lines,
                    "new_start": new_start,
                    "new_lines": new_lines,
                    "header": header,
                    "content": "\n".join(content_lines) + ("\n" if content_lines else ""),
                })
            else:
                truncated_hunks = True
            hunks_collected += 1

        files.append(entry)

    return files, truncated_hunks


# ---------------------------------------------------------------------------
# Tool 3 -- changed_files
# ---------------------------------------------------------------------------

CHANGED_FILES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "branch": {
            "type": "string",
            "description": "Compare against this branch. Default: detected main/master/develop.",
        },
        "include_uncommitted": {
            "type": "boolean",
            "description": "Include unstaged + staged working-tree changes. Default true.",
            "default": True,
        },
    },
    "required": [],
}

CHANGED_FILES_DESC = (
    "List files changed on the current branch vs a base branch (auto-detected main/master/develop), "
    "with per-file `additions`, `deletions`, and `status`. Optionally folds in uncommitted working-"
    "tree changes. Replaces `git diff --numstat` + status parsing with one call."
)

_CHANGED_FILES_TIMEOUT = 15


def run_changed_files(args: dict, cwd: Path) -> dict:
    branch = args.get("branch") or None
    include_uncommitted = bool(args.get("include_uncommitted", True))

    if not _is_inside_repo(cwd):
        return {"error": "not a git repository"}

    if branch is None:
        branch = _detect_default_branch(cwd)
        if branch is None:
            return {"error": "could not detect default branch (looked for origin/HEAD, main, master, develop)"}
    else:
        if not _ref_exists(cwd, branch):
            return {"error": f"unknown ref: {branch}"}

    # Per-path: {path: {"additions": int, "deletions": int, "status": str}}
    by_path: dict[str, dict[str, Any]] = {}

    try:
        proc_numstat = _run_git(
            cwd,
            ["diff", "--numstat", f"{branch}...HEAD"],
            timeout=_CHANGED_FILES_TIMEOUT,
        )
        proc_namestatus = _run_git(
            cwd,
            ["diff", "--name-status", f"{branch}...HEAD"],
            timeout=_CHANGED_FILES_TIMEOUT,
        )
    except _GitError as e:
        return {"error": str(e)}
    if proc_numstat.returncode != 0:
        return {"error": f"git diff failed: {(proc_numstat.stderr or '').strip()[:300]}"}

    _merge_numstat(proc_numstat.stdout or "", by_path)
    _merge_name_status(proc_namestatus.stdout or "", by_path)

    if include_uncommitted:
        try:
            uns_numstat = _run_git(cwd, ["diff", "--numstat", "HEAD"], timeout=_CHANGED_FILES_TIMEOUT)
            uns_namestatus = _run_git(cwd, ["diff", "--name-status", "HEAD"], timeout=_CHANGED_FILES_TIMEOUT)
            # Untracked files don't show in `git diff HEAD`; include them explicitly.
            untracked = _run_git(
                cwd,
                ["ls-files", "--others", "--exclude-standard"],
                timeout=_CHANGED_FILES_TIMEOUT,
            )
        except _GitError as e:
            return {"error": str(e)}
        _merge_numstat(uns_numstat.stdout or "", by_path)
        _merge_name_status(uns_namestatus.stdout or "", by_path)
        for line in (untracked.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = by_path.setdefault(line, {"additions": 0, "deletions": 0, "status": "added"})
            # Count lines as additions for untracked files so totals are meaningful.
            try:
                size = sum(1 for _ in (cwd / line).open("r", encoding="utf-8", errors="replace"))
                entry["additions"] = max(entry["additions"], size)
            except OSError:
                pass

    files = [
        {"path": path, **info}
        for path, info in sorted(by_path.items())
    ]
    totals = {
        "files": len(files),
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }
    return {
        "branch_compared": branch,
        "files": files,
        "totals": totals,
    }


def _detect_default_branch(cwd: Path) -> str | None:
    try:
        proc = _run_git(cwd, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], timeout=5)
    except _GitError:
        proc = None
    if proc is not None and proc.returncode == 0:
        ref = (proc.stdout or "").strip()
        if ref:
            return ref
    for candidate in ("main", "master", "develop"):
        if _ref_exists(cwd, candidate):
            return candidate
    return None


def _ref_exists(cwd: Path, ref: str) -> bool:
    try:
        proc = _run_git(cwd, ["rev-parse", "--verify", "--quiet", ref], timeout=5)
    except _GitError:
        return False
    return proc.returncode == 0


def _merge_numstat(text: str, by_path: dict[str, dict[str, Any]]) -> None:
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds_raw, dels_raw, path = parts[0], parts[1], parts[2]
        # Renames in numstat appear as "old => new" or with separate path[1] segments;
        # we'll keep the raw path string and let name-status correct the status.
        additions = 0 if adds_raw == "-" else int(adds_raw)
        deletions = 0 if dels_raw == "-" else int(dels_raw)
        entry = by_path.setdefault(path, {"additions": 0, "deletions": 0, "status": "modified"})
        entry["additions"] += additions
        entry["deletions"] += deletions


def _merge_name_status(text: str, by_path: dict[str, dict[str, Any]]) -> None:
    status_map = {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
        "U": "unmerged",
    }
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        code = parts[0][0] if parts[0] else ""
        status = status_map.get(code, "modified")
        if code in ("R", "C") and len(parts) >= 3:
            path = parts[2]
        elif len(parts) >= 2:
            path = parts[1]
        else:
            continue
        entry = by_path.setdefault(path, {"additions": 0, "deletions": 0, "status": status})
        entry["status"] = status
