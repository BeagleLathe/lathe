"""`lint_and_typecheck` tool: structured lint + typecheck dispatch by extension.

Replaces "run each linter via Bash, grep raw output" with one call that returns
issues in a uniform shape, sorted by file/line, with per-runner status. Runners
that aren't installed are listed in `skipped_runners` rather than failing the
whole call. Each language path is independent: missing toolchains in one
language never block reporting for another.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Files or directories to check. Default: current directory.",
        },
        "level": {
            "type": "string",
            "enum": ["syntax", "full"],
            "description": "syntax = parser-only; full = lint + types. Default: full.",
            "default": "full",
        },
        "max_issues": {
            "type": "integer",
            "description": "Cap the issues list. Default 100.",
            "default": 100,
        },
    },
    "required": [],
}

DESCRIPTION = (
    "Run linters and typecheckers on the given paths and return issues in a uniform structured "
    "shape. Dispatches by file extension: Python -> ruff + mypy (or py_compile for syntax); "
    "TS/TSX -> tsc + eslint; JS/JSX -> eslint; Go -> go vet + golangci-lint; Rust -> cargo check + "
    "clippy. Runners that aren't installed are listed in `skipped_runners` rather than failing. "
    "Replaces multiple Bash invocations + raw-output grepping with one call."
)

PER_RUNNER_TIMEOUT = 60
TOTAL_WALLCLOCK_TIMEOUT = 180
MAX_ISSUES_DEFAULT = 100

PY_EXTS = {".py"}
TS_EXTS = {".ts", ".tsx"}
JS_EXTS = {".js", ".jsx"}
GO_EXTS = {".go"}
RS_EXTS = {".rs"}
ALL_EXTS = PY_EXTS | TS_EXTS | JS_EXTS | GO_EXTS | RS_EXTS

_SKIP_DIRS = {".git", ".venv", "node_modules", "target", "__pycache__", "dist", "build"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_lint_and_typecheck(args: dict, cwd: Path) -> dict:
    raw_paths = args.get("paths") or ["."]
    level = args.get("level") or "full"
    if level not in ("syntax", "full"):
        return {"error": "`level` must be 'syntax' or 'full'"}
    try:
        max_issues = int(args.get("max_issues") or MAX_ISSUES_DEFAULT)
    except (TypeError, ValueError):
        return {"error": "`max_issues` must be an integer"}
    if max_issues <= 0:
        max_issues = MAX_ISSUES_DEFAULT

    groups = _collect_paths(raw_paths, cwd)
    if not groups:
        return {
            "level": level,
            "summary": {"errors": 0, "warnings": 0, "by_file": {}},
            "issues": [],
            "ran_runners": [],
            "skipped_runners": [],
            "truncated": False,
            "note": "no recognized source files in paths",
        }

    deadline = time.monotonic() + TOTAL_WALLCLOCK_TIMEOUT
    issues: list[dict] = []
    ran: list[str] = []
    skipped: list[str] = []
    errored: list[dict] = []

    dispatch: list[tuple[set[str], Callable[..., None]]] = [
        (PY_EXTS, _run_python),
        (TS_EXTS, _run_typescript),
        (JS_EXTS, _run_javascript),
        (GO_EXTS, _run_go),
        (RS_EXTS, _run_rust),
    ]
    for exts, runner in dispatch:
        files = [f for ext in exts for f in groups.get(ext, [])]
        if not files:
            continue
        if time.monotonic() >= deadline:
            errored.append({"runner": "wall_clock", "stderr_tail": "180s budget exhausted"})
            break
        runner(files, level, cwd, issues, ran, skipped, errored, deadline)

    return _finalize(issues, ran, skipped, errored, level, max_issues)


def _collect_paths(paths: list[str], cwd: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for raw in paths:
        p = (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if p.is_file():
            ext = p.suffix
            if ext in ALL_EXTS:
                groups.setdefault(ext, []).append(p)
        elif p.is_dir():
            for f in _walk_files(p):
                ext = f.suffix
                if ext in ALL_EXTS:
                    groups.setdefault(ext, []).append(f)
    return groups


def _walk_files(root: Path):
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        parts = f.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
            continue
        yield f


def _finalize(
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    level: str,
    max_issues: int,
) -> dict:
    issues.sort(key=lambda i: (i.get("file") or "", i.get("line") or 0, i.get("column") or 0))
    truncated = len(issues) > max_issues
    visible = issues[:max_issues]

    errors = 0
    warnings = 0
    by_file: dict[str, dict[str, int]] = {}
    for it in visible:
        sev = it.get("severity") or "warning"
        f = it.get("file") or ""
        entry = by_file.setdefault(f, {"errors": 0, "warnings": 0})
        if sev == "error":
            errors += 1
            entry["errors"] += 1
        else:
            warnings += 1
            entry["warnings"] += 1

    result: dict[str, Any] = {
        "level": level,
        "summary": {"errors": errors, "warnings": warnings, "by_file": by_file},
        "issues": visible,
        "ran_runners": ran,
        "skipped_runners": skipped,
        "truncated": truncated,
    }
    if errored:
        result["errored_runners"] = errored
    return result


def _relativize(file_str: str, cwd: Path) -> str:
    try:
        return str(Path(file_str).resolve().relative_to(cwd.resolve()))
    except (ValueError, OSError):
        return file_str


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _run_python(
    files: list[Path],
    level: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    deadline: float,
) -> None:
    if level == "syntax":
        _run_py_compile(files, cwd, issues, ran, skipped, errored)
        return
    _run_ruff(files, cwd, issues, ran, skipped, errored)
    if time.monotonic() < deadline:
        _run_mypy(files, cwd, issues, ran, skipped, errored)


# Inline helper run via `python -c` so all files are compiled in one subprocess
# and results come back as parseable JSON. Stdlib `py_compile` short-circuits at
# the first error in batch mode, which would hide later errors.
_PY_COMPILE_HELPER = (
    "import sys, json\n"
    "errs = []\n"
    "for f in sys.argv[1:]:\n"
    "    try:\n"
    "        with open(f, 'rb') as fh:\n"
    "            compile(fh.read(), f, 'exec')\n"
    "    except (SyntaxError, IndentationError, TabError) as e:\n"
    "        errs.append({'file': f, 'line': e.lineno or 1, 'column': e.offset,\n"
    "                     'kind': type(e).__name__, 'message': e.msg or str(e)})\n"
    "    except OSError as e:\n"
    "        errs.append({'file': f, 'line': 1, 'column': None,\n"
    "                     'kind': 'OSError', 'message': str(e)})\n"
    "sys.stdout.write(json.dumps(errs))\n"
)


def _run_py_compile(
    files: list[Path],
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    cmd = [sys.executable, "-c", _PY_COMPILE_HELPER, *[str(f) for f in files]]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        errored.append({"runner": "py_compile", "stderr_tail": "timed out"})
        return
    except FileNotFoundError:
        skipped.append("py_compile")
        return
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        errored.append({"runner": "py_compile", "stderr_tail": (proc.stderr or "")[-500:]})
        return
    ran.append("py_compile")
    for e in data:
        issues.append({
            "file": _relativize(e.get("file", ""), cwd),
            "line": e.get("line"),
            "column": e.get("column"),
            "severity": "error",
            "code": e.get("kind") or "SyntaxError",
            "source": "py_compile",
            "message": e.get("message") or "",
        })


def _run_ruff(
    files: list[Path],
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("ruff") is None:
        skipped.append("ruff")
        return
    cmd = ["ruff", "check", "--output-format=json", "--no-cache", *[str(f) for f in files]]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        errored.append({"runner": "ruff", "stderr_tail": "timed out"})
        return
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        errored.append({"runner": "ruff", "stderr_tail": (proc.stderr or proc.stdout or "")[-500:]})
        return
    ran.append("ruff")
    for item in data:
        code = item.get("code") or ""
        loc = item.get("location") or {}
        issues.append({
            "file": _relativize(item.get("filename") or "", cwd),
            "line": int(loc.get("row", 0)) or None,
            "column": int(loc.get("column", 0)) or None,
            "severity": _ruff_severity(code),
            "code": code,
            "source": "ruff",
            "message": item.get("message") or "",
        })


def _ruff_severity(code: str) -> str:
    if not code:
        return "warning"
    if code.startswith("F"):  # pyflakes correctness rules
        return "error"
    if code.startswith("E9"):  # pycodestyle parser errors
        return "error"
    return "warning"


_MYPY_LINE_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<sev>error|warning|note):\s*"
    r"(?P<msg>.+?)(?:\s+\[(?P<code>[a-z][a-z0-9\-]*)\])?$"
)


def _run_mypy(
    files: list[Path],
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("mypy") is None:
        skipped.append("mypy")
        return
    cmd = [
        "mypy",
        "--no-error-summary",
        "--no-color-output",
        "--show-column-numbers",
        "--no-pretty",
        *[str(f) for f in files],
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        errored.append({"runner": "mypy", "stderr_tail": "timed out"})
        return
    ran.append("mypy")
    for line in (proc.stdout or "").splitlines():
        m = _MYPY_LINE_RE.match(line.strip())
        if not m or m.group("sev") == "note":
            continue
        issues.append({
            "file": _relativize(m.group("file"), cwd),
            "line": int(m.group("line")),
            "column": int(m.group("col")) if m.group("col") else None,
            "severity": m.group("sev"),
            "code": m.group("code") or "",
            "source": "mypy",
            "message": m.group("msg").strip(),
        })


# ---------------------------------------------------------------------------
# TypeScript / JavaScript
# ---------------------------------------------------------------------------


def _run_typescript(
    files: list[Path],
    level: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    deadline: float,
) -> None:
    _run_tsc(cwd, issues, ran, skipped, errored)
    if level == "full" and time.monotonic() < deadline:
        _run_eslint(files, cwd, issues, ran, skipped, errored)


def _run_javascript(
    files: list[Path],
    level: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    deadline: float,
) -> None:
    if level == "full":
        _run_eslint(files, cwd, issues, ran, skipped, errored)


_TSC_LINE_RE = re.compile(
    r"^(?P<file>[^()]+)\((?P<line>\d+),(?P<col>\d+)\):\s+(?P<sev>error|warning)\s+"
    r"(?P<code>TS\d+):\s+(?P<msg>.+)$"
)


def _run_tsc(
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("tsc") is None and shutil.which("npx") is None:
        skipped.append("tsc")
        return
    if shutil.which("tsc"):
        cmd = ["tsc", "--noEmit", "--pretty", "false"]
    else:
        cmd = ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        errored.append({"runner": "tsc", "stderr_tail": "timed out or not found"})
        return
    if "Cannot find" in (proc.stderr or "") and "tsconfig" in (proc.stderr or ""):
        skipped.append("tsc")
        return
    ran.append("tsc")
    for line in (proc.stdout or "").splitlines():
        m = _TSC_LINE_RE.match(line.strip())
        if not m:
            continue
        issues.append({
            "file": _relativize(m.group("file"), cwd),
            "line": int(m.group("line")),
            "column": int(m.group("col")),
            "severity": m.group("sev"),
            "code": m.group("code"),
            "source": "tsc",
            "message": m.group("msg"),
        })


def _run_eslint(
    files: list[Path],
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    binary = "eslint" if shutil.which("eslint") else ("npx" if shutil.which("npx") else None)
    if binary is None:
        skipped.append("eslint")
        return
    cmd = (
        ["eslint", "--format=json", *[str(f) for f in files]]
        if binary == "eslint"
        else ["npx", "--no-install", "eslint", "--format=json", *[str(f) for f in files]]
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        errored.append({"runner": "eslint", "stderr_tail": "timed out or not found"})
        return
    if not (proc.stdout or "").strip():
        skipped.append("eslint")
        return
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        errored.append({"runner": "eslint", "stderr_tail": (proc.stderr or "")[-500:]})
        return
    ran.append("eslint")
    for f in data:
        file_rel = _relativize(f.get("filePath", ""), cwd)
        for msg in f.get("messages", []):
            issues.append({
                "file": file_rel,
                "line": msg.get("line"),
                "column": msg.get("column"),
                "severity": "error" if msg.get("severity") == 2 else "warning",
                "code": msg.get("ruleId") or "",
                "source": "eslint",
                "message": msg.get("message") or "",
            })


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _run_go(
    files: list[Path],
    level: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    deadline: float,
) -> None:
    _run_govet(cwd, issues, ran, skipped, errored)
    if level == "full" and time.monotonic() < deadline:
        _run_golangci(cwd, issues, ran, skipped, errored)


_GOVET_LINE_RE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.+)$")


def _run_govet(
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("go") is None:
        skipped.append("go vet")
        return
    try:
        proc = subprocess.run(
            ["go", "vet", "./..."],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        errored.append({"runner": "go vet", "stderr_tail": "timed out or not found"})
        return
    ran.append("go vet")
    for line in (proc.stderr or "").splitlines():
        m = _GOVET_LINE_RE.match(line.strip())
        if not m:
            continue
        issues.append({
            "file": _relativize(m.group("file"), cwd),
            "line": int(m.group("line")),
            "column": int(m.group("col")),
            "severity": "error",
            "code": "",
            "source": "go vet",
            "message": m.group("msg"),
        })


def _run_golangci(
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("golangci-lint") is None:
        skipped.append("golangci-lint")
        return
    try:
        proc = subprocess.run(
            ["golangci-lint", "run", "--out-format=json"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        errored.append({"runner": "golangci-lint", "stderr_tail": "timed out or not found"})
        return
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        errored.append({"runner": "golangci-lint", "stderr_tail": (proc.stderr or "")[-500:]})
        return
    ran.append("golangci-lint")
    for item in data.get("Issues") or []:
        pos = item.get("Pos") or {}
        sev = item.get("Severity") or "warning"
        issues.append({
            "file": _relativize(pos.get("Filename", ""), cwd),
            "line": int(pos.get("Line", 0)) or None,
            "column": int(pos.get("Column", 0)) or None,
            "severity": "error" if sev.lower() == "error" else "warning",
            "code": item.get("FromLinter") or "",
            "source": "golangci-lint",
            "message": item.get("Text") or "",
        })


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


def _run_rust(
    files: list[Path],
    level: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
    deadline: float,
) -> None:
    _run_cargo("check", "cargo check", cwd, issues, ran, skipped, errored)
    if level == "full" and time.monotonic() < deadline:
        _run_cargo("clippy", "cargo clippy", cwd, issues, ran, skipped, errored)


def _run_cargo(
    subcmd: str,
    label: str,
    cwd: Path,
    issues: list[dict],
    ran: list[str],
    skipped: list[str],
    errored: list[dict],
) -> None:
    if shutil.which("cargo") is None:
        skipped.append(label)
        return
    try:
        proc = subprocess.run(
            ["cargo", subcmd, "--message-format=json", "--quiet"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PER_RUNNER_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        errored.append({"runner": label, "stderr_tail": "timed out or not found"})
        return
    ran.append(label)
    for line in (proc.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        level_str = msg.get("level") or "warning"
        spans = msg.get("spans") or []
        primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else None)
        if not primary:
            continue
        code = msg.get("code") or {}
        code_str = (code.get("code") if isinstance(code, dict) else "") or ""
        issues.append({
            "file": _relativize(primary.get("file_name", ""), cwd),
            "line": int(primary.get("line_start") or 0) or None,
            "column": int(primary.get("column_start") or 0) or None,
            "severity": "error" if level_str == "error" else "warning",
            "code": code_str,
            "source": label,
            "message": msg.get("message") or "",
        })
