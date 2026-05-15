"""`commit_message` tool: heuristic conventional-commit draft from a git diff.

Looks at staged / last-commit / working-tree changes and produces a
deterministic conventional-commit draft (`type(scope): subject`). No LLM call:
file paths drive the scope, change patterns drive the type, basenames drive
the subject. Intended as a draft the agent can polish.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "enum": ["staged", "working_tree", "last_commit"],
            "description": "Where to read the diff from. Default: staged.",
            "default": "staged",
        },
        "type_override": {
            "type": "string",
            "description": "Force a specific conventional-commit type (feat, fix, refactor, etc.). Optional.",
        },
        "scope_override": {
            "type": "string",
            "description": "Force a specific scope. Optional.",
        },
        "max_subject_length": {
            "type": "integer",
            "description": "Cap the subject line. Default: 60.",
            "default": 60,
        },
    },
    "required": [],
}

DESCRIPTION = (
    "Generate a conventional-commit-style message draft from staged changes (or last commit, "
    "or working tree). Heuristic-only -- no LLM call. File paths drive the scope, change patterns "
    "drive the type, basenames drive the subject. Returns `{type, scope, subject, message}` plus "
    "the file list it considered. Intended as a starting point for the agent to refine."
)

DIFF_TIMEOUT = 15
MAX_SUBJECT_DEFAULT = 60

# Known conventional-commit types, in priority order when multiple apply.
_VALID_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"
)


def run_commit_message(args: dict, cwd: Path) -> dict:
    source = args.get("source") or "staged"
    if source not in ("staged", "working_tree", "last_commit"):
        return {"error": "`source` must be 'staged', 'working_tree', or 'last_commit'"}
    type_override = (args.get("type_override") or "").strip() or None
    scope_override = (args.get("scope_override") or "").strip() or None
    try:
        max_subject = int(args.get("max_subject_length") or MAX_SUBJECT_DEFAULT)
    except (TypeError, ValueError):
        return {"error": "`max_subject_length` must be an integer"}
    if max_subject < 20:
        max_subject = MAX_SUBJECT_DEFAULT

    if not _is_inside_repo(cwd):
        return {"error": "not a git repository"}

    files = _list_changes(cwd, source)
    if not files:
        return {"error": f"no changes found in {source}"}

    commit_type = type_override or _infer_type(files)
    if commit_type not in _VALID_TYPES:
        # Unknown override: keep it, but warn via a field.
        pass
    scope = scope_override or _infer_scope(files) or ""
    subject = _build_subject(files, commit_type, max_subject - _header_overhead(commit_type, scope))

    message = _format_message(commit_type, scope, subject)

    return {
        "type": commit_type,
        "scope": scope or None,
        "subject": subject,
        "message": message,
        "source": source,
        "files": [{"path": f["path"], "status": f["status"]} for f in files],
    }


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _is_inside_repo(cwd: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def _list_changes(cwd: Path, source: str) -> list[dict]:
    if source == "staged":
        cmd = ["git", "diff", "--staged", "--name-status"]
    elif source == "working_tree":
        cmd = ["git", "diff", "--name-status", "HEAD"]
    else:  # last_commit
        cmd = ["git", "show", "--name-status", "--format=", "HEAD"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=DIFF_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    return _parse_name_status(proc.stdout or "")


_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "type_changed",
    "U": "unmerged",
}


def _parse_name_status(text: str) -> list[dict]:
    files: list[dict] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        code = parts[0][0] if parts[0] else ""
        status = _STATUS_MAP.get(code, "modified")
        if code in ("R", "C") and len(parts) >= 3:
            path = parts[2]
        elif len(parts) >= 2:
            path = parts[1]
        else:
            continue
        files.append({"path": path, "status": status})
    return files


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def _infer_type(files: list[dict]) -> str:
    paths = [f["path"] for f in files]
    statuses = [f["status"] for f in files]

    def all_match(predicate) -> bool:
        return all(predicate(p) for p in paths)

    if all_match(_is_test_path):
        return "test"
    if all_match(_is_docs_path):
        return "docs"
    if all_match(_is_ci_path):
        return "ci"
    if all_match(_is_build_path):
        return "build"

    # Pure additions in source -> feat. Pure deletions -> refactor. Otherwise default.
    src_files = [p for p in paths if not _is_meta_path(p)]
    if src_files:
        src_statuses = [
            f["status"] for f in files if not _is_meta_path(f["path"])
        ]
        unique = set(src_statuses)
        if unique == {"added"}:
            return "feat"
        if unique == {"deleted"}:
            return "refactor"
        if "added" in unique and "deleted" not in unique:
            # Mostly adding code -> feat.
            return "feat"
    return "fix"


def _is_test_path(p: str) -> bool:
    parts = p.split("/")
    if any(part in ("tests", "test", "__tests__", "spec", "specs") for part in parts):
        return True
    base = os.path.basename(p)
    return (
        base.startswith("test_")
        or base.endswith("_test.go")
        or base.endswith(".test.ts")
        or base.endswith(".test.tsx")
        or base.endswith(".test.js")
        or base.endswith(".spec.ts")
        or base.endswith(".spec.js")
    )


def _is_docs_path(p: str) -> bool:
    base = os.path.basename(p).lower()
    if base in ("readme.md", "changelog.md", "license", "license.md", "contributing.md"):
        return True
    parts = p.split("/")
    if any(part in ("docs", "doc") for part in parts):
        return True
    return p.lower().endswith(".md") or p.lower().endswith(".rst")


def _is_ci_path(p: str) -> bool:
    parts = p.split("/")
    if parts and parts[0] == ".github":
        return True
    if parts and parts[0] == ".gitlab-ci.yml":
        return True
    if any(part in ("ci", "workflows") for part in parts):
        return True
    return os.path.basename(p) in (".travis.yml", "azure-pipelines.yml", "Jenkinsfile")


def _is_build_path(p: str) -> bool:
    base = os.path.basename(p)
    return base in (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "Makefile",
        "Dockerfile",
    )


def _is_meta_path(p: str) -> bool:
    return _is_docs_path(p) or _is_ci_path(p) or _is_build_path(p) or _is_test_path(p)


def _infer_scope(files: list[dict]) -> str | None:
    paths = [f["path"] for f in files]
    if not paths:
        return None
    # For each path, the directory components (drop the basename).
    dirs_per_path: list[list[str]] = []
    for p in paths:
        parts = p.split("/")
        dirs_per_path.append(parts[:-1] if len(parts) > 1 else [])
    if not any(dirs_per_path):
        return None
    common: list[str] = []
    for cols in zip(*dirs_per_path):
        if all(c == cols[0] for c in cols):
            common.append(cols[0])
        else:
            break
    # Strip leading common roots that aren't useful as scope words.
    while common and common[0] in ("src", "lib", "app", "packages", "internal", "cmd"):
        common.pop(0)
    if not common:
        return None
    candidate = common[-1]
    if not candidate or candidate in (".",):
        return None
    return candidate.lower()


def _build_subject(files: list[dict], commit_type: str, budget: int) -> str:
    verb = _verb_for_type(commit_type, files)
    bases = [os.path.basename(f["path"]) for f in files if f.get("path")]
    counter = Counter(bases)
    most_common = [b for b, _ in counter.most_common(3)]
    if not most_common:
        return verb
    joined = ", ".join(most_common)
    subject = f"{verb} {joined}"
    if budget > 0 and len(subject) > budget:
        # Trim file list to fit budget. Always keep the verb.
        prefix = f"{verb} "
        room = max(8, budget - len(prefix))
        trimmed = joined[: room - 1].rstrip(", ")
        subject = f"{prefix}{trimmed}..."
    return subject


def _verb_for_type(commit_type: str, files: list[dict]) -> str:
    statuses = {f.get("status") for f in files}
    if commit_type == "feat":
        return "add"
    if commit_type == "fix":
        return "fix"
    if commit_type == "refactor":
        return "refactor"
    if commit_type == "test":
        if statuses == {"added"}:
            return "add tests for"
        return "update tests for"
    if commit_type == "docs":
        return "update docs for"
    if commit_type == "ci":
        return "update"
    if commit_type == "build":
        return "update"
    if commit_type == "chore":
        return "chore:"
    return "update"


def _header_overhead(commit_type: str, scope: str) -> int:
    if scope:
        return len(commit_type) + 1 + len(scope) + 1 + 2  # "<type>(<scope>): "
    return len(commit_type) + 2  # "<type>: "


def _format_message(commit_type: str, scope: str, subject: str) -> str:
    if scope:
        return f"{commit_type}({scope}): {subject}"
    return f"{commit_type}: {subject}"
