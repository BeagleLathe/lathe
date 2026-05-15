"""`sh` tool: read-only shell command runner with strict whitelist.

No shell metacharacters allowed (`<`, `>`, `|`, `&`, `;`, backticks, `$(`, `${`,
`$VAR`). First token must be in ALLOWED_COMMANDS. For `git` the subcommand must
also be in GIT_ALLOWED. Output is hard-capped to keep tool results small.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "A single read-only shell command. No pipes/redirects/subshells."}
    },
    "required": ["command"],
}

DESCRIPTION = (
    "Run a single read-only shell command (ls, cat, head, tail, wc, find, pwd, which, file, stat, du, df, "
    "echo, tree, or git log/status/diff/show/blame/branch/remote/config/rev-parse/ls-files/describe). "
    "Pipes, redirects, subshells, and env interpolation are rejected. Output is truncated to the last "
    "500 lines / 50KB. Use this instead of the built-in Bash tool for read-only inspection."
)

ALLOWED_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "wc", "pwd", "which", "file", "stat",
    "du", "df", "find", "tree", "echo", "git",
}

GIT_ALLOWED: set[str] = {
    "log", "status", "diff", "show", "blame", "branch", "remote",
    "config", "rev-parse", "ls-files", "describe",
}

_DANGEROUS_RE = re.compile(r"[<>|&;`]|\$\(|\$\{|\$[A-Za-z_]")

MAX_STDOUT_LINES = 500
MAX_STDOUT_BYTES = 50_000
MAX_STDERR_BYTES = 5_000
TIMEOUT_SECS = 30


def run_sh(args: dict, cwd: Path) -> dict:
    cmd_str = (args.get("command") or "").strip()
    if not cmd_str:
        return {"error": "`command` is required"}

    if _DANGEROUS_RE.search(cmd_str):
        return {
            "error": (
                "shell metacharacters not allowed (<, >, |, &, ;, `, $(...), ${...}, $VAR). "
                "Use a single command; for chains, run them separately."
            )
        }

    try:
        tokens = shlex.split(cmd_str)
    except ValueError as e:
        return {"error": f"could not parse command: {e}"}
    if not tokens:
        return {"error": "empty command"}

    first = tokens[0]
    if first not in ALLOWED_COMMANDS:
        return {
            "error": (
                f"command '{first}' not in read-only allowlist. "
                f"Allowed: {sorted(ALLOWED_COMMANDS)}."
            )
        }
    if first == "git":
        if len(tokens) < 2 or tokens[1] not in GIT_ALLOWED:
            return {
                "error": (
                    f"git subcommand not allowed. Allowed: {sorted(GIT_ALLOWED)}."
                )
            }

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
        return {"error": f"command timed out after {TIMEOUT_SECS}s"}
    except FileNotFoundError:
        return {"error": f"command not found: {first}"}

    stdout, truncated = _truncate_stdout(proc.stdout)
    stderr = proc.stderr
    if len(stderr) > MAX_STDERR_BYTES:
        stderr = stderr[-MAX_STDERR_BYTES:]

    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
    }


def _truncate_stdout(stdout: str) -> tuple[str, bool]:
    if not stdout:
        return "", False
    truncated = False
    if stdout.count("\n") > MAX_STDOUT_LINES:
        lines = stdout.splitlines(keepends=True)
        stdout = "".join(lines[-MAX_STDOUT_LINES:])
        truncated = True
    if len(stdout) > MAX_STDOUT_BYTES:
        stdout = stdout[-MAX_STDOUT_BYTES:]
        truncated = True
    return stdout, truncated
