"""`run_tests` tool: detect framework, run tests, return structured results.

Collapses the vanilla "Bash(pytest) -> grep -> read -> grep -> read" cycle into
one structured call. Auto-detects pytest / jest / vitest / cargo test / go test
from files in cwd. Output includes summary counts plus per-failure file:line,
message, and a small source excerpt -- no need to grep through raw output.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Override the auto-detected test command. Optional.",
        },
        "path": {
            "type": "string",
            "description": "Restrict tests to this path (file or directory). Optional.",
        },
        "pattern": {
            "type": "string",
            "description": (
                "Test name filter (passed as -k for pytest, --testNamePattern for jest, "
                "-run for go test). Optional."
            ),
        },
        "max_failures": {
            "type": "integer",
            "description": "Cap how many failure entries are included. Default 20.",
            "default": 20,
        },
    },
    "required": [],
}

DESCRIPTION = (
    "Run the project's test suite and return parsed, structured results in one round-trip. "
    "Auto-detects pytest / jest / vitest / cargo test / go test from files in cwd. "
    "Output includes summary counts and per-failure file:line, assertion message, and a small "
    "source excerpt around the failing line -- no need to grep through raw test output. "
    "Use this instead of the built-in Bash tool for any test run."
)

TIMEOUT_SECS = 300  # 5 minutes total wall-clock cap
MAX_FAILURES_DEFAULT = 20
EXCERPT_CONTEXT = 2  # lines before/after the failing line


def run_tests(args: dict, cwd: Path) -> dict:
    explicit_cmd = (args.get("command") or "").strip() or None
    path = args.get("path") or None
    pattern = args.get("pattern") or None
    try:
        max_failures = int(args.get("max_failures") or MAX_FAILURES_DEFAULT)
    except (TypeError, ValueError):
        return {"error": "`max_failures` must be an integer"}
    if max_failures <= 0:
        max_failures = MAX_FAILURES_DEFAULT

    if explicit_cmd:
        return _run_explicit(explicit_cmd, cwd, max_failures)

    framework = _detect_framework(cwd)
    if framework is None:
        return {
            "error": (
                "could not detect test framework; pass `command` explicitly "
                "(looked for: pytest, jest, vitest, cargo, go)"
            )
        }

    runners: dict[str, Callable[..., dict]] = {
        "pytest": lambda: _run_pytest(cwd, path, pattern, max_failures),
        "jest": lambda: _run_jest(cwd, path, pattern, max_failures),
        "vitest": lambda: _run_vitest(cwd, path, pattern, max_failures),
        "cargo": lambda: _run_cargo_test(cwd, pattern, max_failures),
        "go": lambda: _run_go_test(cwd, path, pattern, max_failures),
    }
    return runners[framework]()


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------


def _detect_framework(cwd: Path) -> str | None:
    pyproject = cwd / "pyproject.toml"
    if pyproject.exists() and "[tool.pytest" in _read_text_safe(pyproject):
        return "pytest"
    if (cwd / "pytest.ini").exists():
        return "pytest"
    tests_dir = cwd / "tests"
    if tests_dir.is_dir():
        for p in tests_dir.iterdir():
            if p.is_file() and p.name.startswith("test_") and p.suffix == ".py":
                return "pytest"
    for _ in cwd.glob("test_*.py"):
        return "pytest"

    pkg = cwd / "package.json"
    if pkg.exists():
        try:
            data = json.loads(_read_text_safe(pkg))
        except json.JSONDecodeError:
            data = {}
        test_script = (data.get("scripts") or {}).get("test", "")
        if "vitest" in test_script or any(cwd.glob("vitest.config.*")):
            return "vitest"
        if "jest" in test_script or any(cwd.glob("jest.config.*")):
            return "jest"

    if (cwd / "Cargo.toml").exists():
        return "cargo"
    if (cwd / "go.mod").exists():
        return "go"
    return None


def _read_text_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# pytest runner
# ---------------------------------------------------------------------------

_PYTEST_FAILED_SUMMARY_RE = re.compile(
    r"^FAILED\s+(?P<name>\S+)(?:\s+-\s+(?P<msg>.*))?$",
    re.MULTILINE,
)
_PYTEST_COUNTS_RE = re.compile(
    r"=+\s*(?P<body>[^=]+?)\s+in\s+[\d.]+\s*s\s*=+\s*$",
    re.MULTILINE,
)
_PYTEST_COUNT_FIELD_RE = re.compile(r"(?P<n>\d+)\s+(?P<word>\w+)")
_PYTEST_BLOCK_HEADER_RE = re.compile(r"^_{3,}\s+(?P<short>\S+)\s+_{3,}\s*$")
_PYTEST_SECTION_FAILURES_RE = re.compile(r"^={5,}\s*FAILURES\s*={5,}\s*$", re.MULTILINE)
_PYTEST_SECTION_END_RE = re.compile(r"^={5,}\s+\S", re.MULTILINE)
_PYTEST_TRACE_LOC_RE = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):\s")


def _run_pytest(cwd: Path, path: str | None, pattern: str | None, max_failures: int) -> dict:
    cmd = ["pytest", "--tb=short", "-v", "--no-header", "--color=no", "-p", "no:cacheprovider"]
    if pattern:
        cmd.extend(["-k", pattern])
    if path:
        cmd.append(path)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"tests exceeded {TIMEOUT_SECS}s timeout"}
    except FileNotFoundError:
        return {"error": "pytest not found; pip install pytest"}

    duration_ms = int((time.monotonic() - started) * 1000)
    parsed = _parse_pytest_output(proc.stdout or "", cwd, max_failures)
    parsed["framework"] = "pytest"
    parsed["command"] = " ".join(cmd)
    parsed["duration_ms"] = duration_ms

    if parsed["summary"]["total"] == 0 and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-20:]
        parsed["error_output"] = "\n".join(tail)
    return parsed


def _parse_pytest_output(text: str, cwd: Path, max_failures: int) -> dict:
    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    last_body: str | None = None
    for m in _PYTEST_COUNTS_RE.finditer(text):
        last_body = m.group("body")
    if last_body:
        for fm in _PYTEST_COUNT_FIELD_RE.finditer(last_body):
            n = int(fm.group("n"))
            word = fm.group("word")
            if word == "passed":
                summary["passed"] = n
            elif word == "failed":
                summary["failed"] = n
            elif word == "skipped":
                summary["skipped"] = n
            elif word in ("error", "errors"):
                summary["errors"] = n
    summary["total"] = summary["passed"] + summary["failed"] + summary["skipped"]

    failure_blocks = _split_failure_blocks(text)
    failures: list[dict] = []
    truncated_failures = False

    summary_matches = list(_PYTEST_FAILED_SUMMARY_RE.finditer(text))
    total_failed_summary = len(summary_matches)
    for sm in summary_matches:
        if len(failures) >= max_failures:
            truncated_failures = True
            break
        name = sm.group("name")
        msg = (sm.group("msg") or "").strip() or None

        file = name.split("::", 1)[0] if "::" in name else None
        line: int | None = None
        traceback_tail = None
        short = name.rsplit("::", 1)[-1]
        block = failure_blocks.get(short)
        if block:
            traceback_tail = "\n".join(block.splitlines()[-8:]).strip() or None
            for tl in block.splitlines():
                tm = _PYTEST_TRACE_LOC_RE.match(tl.strip())
                if tm:
                    file = tm.group("file")
                    try:
                        line = int(tm.group("line"))
                    except ValueError:
                        line = None

        excerpt = _read_excerpt(cwd, file, line) if (file and line) else None

        failures.append({
            "name": name,
            "file": file,
            "line": line,
            "message": msg,
            "source_excerpt": excerpt,
            "traceback_tail": traceback_tail,
        })

    if total_failed_summary > max_failures:
        truncated_failures = True

    return {
        "summary": summary,
        "failures": failures,
        "truncated_failures": truncated_failures,
    }


def _split_failure_blocks(text: str) -> dict[str, str]:
    """Map each failed test's short name -> its raw block from the FAILURES section."""
    start = _PYTEST_SECTION_FAILURES_RE.search(text)
    if not start:
        return {}
    rest = text[start.end():]
    end_m = _PYTEST_SECTION_END_RE.search(rest)
    section = rest[: end_m.start()] if end_m else rest

    blocks: dict[str, str] = {}
    current_short: str | None = None
    current_lines: list[str] = []
    for line in section.splitlines():
        hm = _PYTEST_BLOCK_HEADER_RE.match(line)
        if hm:
            if current_short is not None:
                blocks[current_short] = "\n".join(current_lines).strip()
            current_short = hm.group("short")
            current_lines = []
        elif current_short is not None:
            current_lines.append(line)
    if current_short is not None:
        blocks[current_short] = "\n".join(current_lines).strip()
    return blocks


def _read_excerpt(cwd: Path, file: str | None, line: int | None) -> str | None:
    if not file or not line:
        return None
    try:
        p = cwd / file
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    lo = max(0, line - 1 - EXCERPT_CONTEXT)
    hi = min(len(lines), line + EXCERPT_CONTEXT)
    if lo >= hi:
        return None
    return "\n".join(f"  {i + 1}: {lines[i]}" for i in range(lo, hi)) + "\n"


# ---------------------------------------------------------------------------
# jest / vitest / cargo / go runners
# ---------------------------------------------------------------------------


def _run_jest(cwd: Path, path: str | None, pattern: str | None, max_failures: int) -> dict:
    cmd = ["npx", "--no-install", "jest", "--json", "--silent"]
    if pattern:
        cmd.extend(["--testNamePattern", pattern])
    if path:
        cmd.extend(["--testPathPattern", path])
    return _run_json_framework(cmd, "jest", cwd, max_failures, _parse_jest_json)


def _run_vitest(cwd: Path, path: str | None, pattern: str | None, max_failures: int) -> dict:
    cmd = ["npx", "--no-install", "vitest", "run", "--reporter=json"]
    if pattern:
        cmd.extend(["-t", pattern])
    if path:
        cmd.append(path)
    return _run_json_framework(cmd, "vitest", cwd, max_failures, _parse_jest_json)


def _run_cargo_test(cwd: Path, pattern: str | None, max_failures: int) -> dict:
    cmd = ["cargo", "test", "--no-fail-fast", "--quiet", "--", "-Z", "unstable-options",
           "--format", "json", "--report-time"]
    # Older cargo: fall back to non-JSON output. We try the modern flag set first;
    # if it fails to produce JSON we still return raw exit_code via _run_json_framework.
    if pattern:
        cmd.insert(3, pattern)
    return _run_json_framework(cmd, "cargo test", cwd, max_failures, _parse_cargo_json)


def _run_go_test(cwd: Path, path: str | None, pattern: str | None, max_failures: int) -> dict:
    target = path or "./..."
    cmd = ["go", "test", "-json"]
    if pattern:
        cmd.extend(["-run", pattern])
    cmd.append(target)
    return _run_json_framework(cmd, "go test", cwd, max_failures, _parse_go_json)


def _run_json_framework(
    cmd: list[str],
    framework: str,
    cwd: Path,
    max_failures: int,
    parser: Callable[[str, Path, int], dict],
) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"tests exceeded {TIMEOUT_SECS}s timeout"}
    except FileNotFoundError:
        return {"error": f"{cmd[0]} not found"}

    duration_ms = int((time.monotonic() - started) * 1000)
    parsed = parser(proc.stdout or "", cwd, max_failures)
    parsed["framework"] = framework
    parsed["command"] = " ".join(cmd)
    parsed["duration_ms"] = duration_ms
    if parsed["summary"]["total"] == 0 and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-20:]
        parsed["error_output"] = "\n".join(tail)
    return parsed


def _parse_jest_json(stdout: str, cwd: Path, max_failures: int) -> dict:
    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {"summary": summary, "failures": [], "truncated_failures": False}

    summary["passed"] = int(data.get("numPassedTests", 0))
    summary["failed"] = int(data.get("numFailedTests", 0))
    summary["skipped"] = (
        int(data.get("numPendingTests", 0)) + int(data.get("numTodoTests", 0))
    )
    summary["total"] = int(data.get("numTotalTests", 0)) or (
        summary["passed"] + summary["failed"] + summary["skipped"]
    )

    failures: list[dict] = []
    truncated = False
    cwd_resolved = cwd.resolve()
    for suite in data.get("testResults", []):
        suite_path = suite.get("name") or ""
        try:
            suite_rel = str(Path(suite_path).resolve().relative_to(cwd_resolved))
        except (ValueError, OSError):
            suite_rel = suite_path
        for t in suite.get("testResults", []):
            if t.get("status") != "failed":
                continue
            if len(failures) >= max_failures:
                truncated = True
                break
            msgs = t.get("failureMessages") or []
            first_msg = msgs[0] if msgs else ""
            first_line = first_msg.splitlines()[0].strip() if first_msg else None
            tail = (
                "\n".join(first_msg.splitlines()[-8:]).strip() if first_msg else None
            )
            name_parts = list(t.get("ancestorTitles", [])) + [t.get("title") or ""]
            failures.append({
                "name": " > ".join(p for p in name_parts if p),
                "file": suite_rel,
                "line": None,
                "message": first_line or None,
                "source_excerpt": None,
                "traceback_tail": tail,
            })
        if truncated:
            break
    return {"summary": summary, "failures": failures, "truncated_failures": truncated}


def _parse_cargo_json(stdout: str, cwd: Path, max_failures: int) -> dict:
    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    failures: list[dict] = []
    truncated = False
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "test":
            continue
        ev = event.get("event")
        if ev == "ok":
            summary["passed"] += 1
        elif ev == "ignored":
            summary["skipped"] += 1
        elif ev == "failed":
            summary["failed"] += 1
            if len(failures) >= max_failures:
                truncated = True
                continue
            out = (event.get("stdout") or "").strip()
            first = out.splitlines()[0] if out else None
            tail = "\n".join(out.splitlines()[-8:]).strip() if out else None
            failures.append({
                "name": event.get("name") or "<unknown>",
                "file": None,
                "line": None,
                "message": first,
                "source_excerpt": None,
                "traceback_tail": tail,
            })
    summary["total"] = summary["passed"] + summary["failed"] + summary["skipped"]
    return {"summary": summary, "failures": failures, "truncated_failures": truncated}


def _parse_go_json(stdout: str, cwd: Path, max_failures: int) -> dict:
    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    output_by_test: dict[tuple[str, str], list[str]] = {}
    final_actions: dict[tuple[str, str], str] = {}
    failure_order: list[tuple[str, str]] = []

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        test = event.get("Test")
        if not test:
            continue
        pkg = event.get("Package", "")
        key = (pkg, test)
        action = event.get("Action")
        if action == "output":
            output_by_test.setdefault(key, []).append(event.get("Output") or "")
        elif action in ("pass", "fail", "skip"):
            final_actions[key] = action
            if action == "fail" and key not in failure_order:
                failure_order.append(key)

    for action in final_actions.values():
        if action == "pass":
            summary["passed"] += 1
        elif action == "fail":
            summary["failed"] += 1
        elif action == "skip":
            summary["skipped"] += 1
    summary["total"] = summary["passed"] + summary["failed"] + summary["skipped"]

    failures: list[dict] = []
    truncated = False
    for key in failure_order:
        if len(failures) >= max_failures:
            truncated = True
            break
        pkg, test = key
        joined = "".join(output_by_test.get(key, [])).strip()
        first = joined.splitlines()[0] if joined else None
        tail = "\n".join(joined.splitlines()[-8:]).strip() if joined else None
        failures.append({
            "name": f"{pkg}::{test}" if pkg else test,
            "file": None,
            "line": None,
            "message": first,
            "source_excerpt": None,
            "traceback_tail": tail,
        })
    return {"summary": summary, "failures": failures, "truncated_failures": truncated}


# ---------------------------------------------------------------------------
# Explicit command override
# ---------------------------------------------------------------------------


def _run_explicit(cmd_str: str, cwd: Path, max_failures: int) -> dict:
    try:
        tokens = shlex.split(cmd_str)
    except ValueError as e:
        return {"error": f"could not parse command: {e}"}
    if not tokens:
        return {"error": "empty command"}

    started = time.monotonic()
    try:
        proc = subprocess.run(
            tokens,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"tests exceeded {TIMEOUT_SECS}s timeout"}
    except FileNotFoundError:
        return {"error": f"command not found: {tokens[0]}"}

    duration_ms = int((time.monotonic() - started) * 1000)
    framework, parsed = _sniff_and_parse(proc.stdout or "", cwd, max_failures)
    if parsed is not None:
        parsed["framework"] = framework
        parsed["command"] = cmd_str
        parsed["duration_ms"] = duration_ms
        return parsed

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if len(stdout) > 8000:
        stdout = stdout[-8000:]
    if len(stderr) > 4000:
        stderr = stderr[-4000:]
    return {
        "framework": "unknown",
        "command": cmd_str,
        "duration_ms": duration_ms,
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0},
        "failures": [],
        "truncated_failures": False,
        "note": "output not parseable as a known test framework; raw stdout/stderr returned",
    }


def _sniff_and_parse(stdout: str, cwd: Path, max_failures: int) -> tuple[str, dict | None]:
    text = stdout or ""
    if "test session starts" in text or _PYTEST_COUNTS_RE.search(text):
        return "pytest", _parse_pytest_output(text, cwd, max_failures)
    stripped = text.strip()
    if stripped.startswith("{") and '"testResults"' in stripped:
        return "jest", _parse_jest_json(stripped, cwd, max_failures)
    first_line = stripped.splitlines()[0] if stripped else ""
    if first_line.startswith("{") and first_line.endswith("}"):
        try:
            ev = json.loads(first_line)
        except json.JSONDecodeError:
            ev = {}
        if "type" in ev and "event" in ev:
            return "cargo test", _parse_cargo_json(stripped, cwd, max_failures)
        if "Action" in ev or "Package" in ev:
            return "go test", _parse_go_json(stripped, cwd, max_failures)
    return "unknown", None
