"""MCP server: registers `search`, `edit`, `read`, `sh`, `run_tests`, structured git tools, and `lint_and_typecheck` over stdio."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth.credentials import CredentialsError, load_credentials, save_credentials
from .formatting import format_result
from .savings import offline_grace_hours, offline_too_long, record_call
from .tools.edit import (
    DESCRIPTION as EDIT_DESC,
    INPUT_SCHEMA as EDIT_SCHEMA,
    apply_edits,
)
from .tools.read import (
    DESCRIPTION as READ_DESC,
    INPUT_SCHEMA as READ_SCHEMA,
    run_read,
)
from .tools.search import (
    DESCRIPTION as SEARCH_DESC,
    INPUT_SCHEMA as SEARCH_SCHEMA,
    run_search,
)
from .tools.sh import (
    DESCRIPTION as SH_DESC,
    INPUT_SCHEMA as SH_SCHEMA,
    run_sh,
)
from .tools.run_tests import (
    DESCRIPTION as RUN_TESTS_DESC,
    INPUT_SCHEMA as RUN_TESTS_SCHEMA,
    run_tests,
)
from .tools.git_ops import (
    GIT_STATUS_DESC,
    GIT_STATUS_SCHEMA,
    GIT_DIFF_DESC,
    GIT_DIFF_SCHEMA,
    CHANGED_FILES_DESC,
    CHANGED_FILES_SCHEMA,
    run_git_status,
    run_git_diff,
    run_changed_files,
)
from .tools.lint_and_typecheck import (
    DESCRIPTION as LINT_DESC,
    INPUT_SCHEMA as LINT_SCHEMA,
    run_lint_and_typecheck,
)
from .tools.extract_todos import (
    DESCRIPTION as TODOS_DESC,
    INPUT_SCHEMA as TODOS_SCHEMA,
    run_extract_todos,
)
from .tools.commit_message import (
    DESCRIPTION as COMMIT_MSG_DESC,
    INPUT_SCHEMA as COMMIT_MSG_SCHEMA,
    run_commit_message,
)

app: Server = Server("beaglelathe")

# Per-process state — reset on each MCP server start.
_welcomed: bool = False
_quota_exceeded: bool = False
_upgrade_url: str | None = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="search", description=SEARCH_DESC, inputSchema=SEARCH_SCHEMA),
        Tool(name="edit", description=EDIT_DESC, inputSchema=EDIT_SCHEMA),
        Tool(name="read", description=READ_DESC, inputSchema=READ_SCHEMA),
        Tool(name="sh", description=SH_DESC, inputSchema=SH_SCHEMA),
        Tool(name="run_tests", description=RUN_TESTS_DESC, inputSchema=RUN_TESTS_SCHEMA),
        Tool(name="git_status", description=GIT_STATUS_DESC, inputSchema=GIT_STATUS_SCHEMA),
        Tool(name="git_diff", description=GIT_DIFF_DESC, inputSchema=GIT_DIFF_SCHEMA),
        Tool(name="changed_files", description=CHANGED_FILES_DESC, inputSchema=CHANGED_FILES_SCHEMA),
        Tool(name="lint_and_typecheck", description=LINT_DESC, inputSchema=LINT_SCHEMA),
        Tool(name="extract_todos", description=TODOS_DESC, inputSchema=TODOS_SCHEMA),
        Tool(name="commit_message", description=COMMIT_MSG_DESC, inputSchema=COMMIT_MSG_SCHEMA),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> tuple[list[TextContent], dict]:
    global _welcomed, _quota_exceeded, _upgrade_url
    cwd = Path.cwd()
    args = arguments or {}

    # Record this call in the local savings DB (best-effort, never blocks).
    record_call(name)

    # First-call welcome when credentials are absent. Tool still executes.
    notice: str | None = None
    if not _welcomed:
        _welcomed = True
        try:
            creds = load_credentials()
        except CredentialsError:
            creds = None
        if creds is None:
            notice = (
                "Welcome to BeagleLathe! Your tools are active and savings are being tracked "
                "locally. Run /lathe-login to connect your account and activate your "
                "free tier (200 tool calls/month included)."
            )

    # Offline-grace gate — blocks tool calls once we've gone too long without a
    # successful backend response. Applies to logged-in and anonymous users
    # alike so usage metering can't be defeated by simply never connecting.
    if offline_too_long():
        hours = offline_grace_hours()
        payload: dict = {
            "error": "offline_too_long",
            "message": (
                f"BeagleLathe has been offline for more than {hours:.0f} hours. "
                "Run `beaglelathe login` (or restore your network) to reconnect."
            ),
            "action": "Run /lathe-login to sign in, or check your network and retry.",
        }
        text = (
            f"BeagleLathe: offline too long\nNo successful backend contact in over "
            f"{hours:.0f} hours.\nRun `beaglelathe login` or restore your network."
        )
        return [TextContent(type="text", text=text)], payload

    # Hard quota gate — only engaged after a sync confirms budget_remaining == 0.
    if _quota_exceeded:
        payload: dict = {
            "error": "quota_exceeded",
            "message": "Free-tier limit reached. Upgrade to continue.",
        }
        if _upgrade_url:
            payload["upgrade_url"] = _upgrade_url
        payload["action"] = (
            "Run /lathe-upgrade (or visit the URL above) to unlock unlimited calls."
        )
        text = "BeagleLathe: quota exceeded\nFree-tier limit reached. Upgrade to continue."
        if _upgrade_url:
            text += f"\nUpgrade: {_upgrade_url}"
        text += "\nRun /lathe-upgrade to unlock unlimited calls."
        return [TextContent(type="text", text=text)], payload

    # Dispatch to the appropriate tool.
    if name == "search":
        result = run_search(args, cwd)
        result = _improve_search_errors(result)
    elif name == "edit":
        result = apply_edits(
            list(args.get("edits", [])),
            cwd,
            validate=bool(args.get("validate", True)),
        )
    elif name == "read":
        result = run_read(args, cwd)
        result = _improve_read_errors(result)
    elif name == "sh":
        result = run_sh(args, cwd)
        result = _improve_sh_errors(result)
    elif name == "run_tests":
        result = run_tests(args, cwd)
    elif name == "git_status":
        result = run_git_status(args, cwd)
    elif name == "git_diff":
        result = run_git_diff(args, cwd)
    elif name == "changed_files":
        result = run_changed_files(args, cwd)
    elif name == "lint_and_typecheck":
        result = run_lint_and_typecheck(args, cwd)
    elif name == "extract_todos":
        result = run_extract_todos(args, cwd)
    elif name == "commit_message":
        result = run_commit_message(args, cwd)
    else:
        result = {"error": f"unknown tool: {name}"}

    if notice:
        result = {"_notice": notice, **result}

    # Background sync to the auth backend (best-effort, never blocks the response).
    threading.Thread(target=_sync_usage_bg, daemon=True).start()

    text = format_result(name, args, result)
    return [TextContent(type="text", text=text)], result


# ---------------------------------------------------------------------------
# Error message improvements
# ---------------------------------------------------------------------------

def _improve_search_errors(result: dict) -> dict:
    err = result.get("error", "")
    if "ripgrep" in err.lower() and "not found" in err.lower():
        result["error"] = (
            "system error: ripgrep is not installed. "
            "Fix: brew install ripgrep (macOS) or apt install ripgrep (Linux)."
        )
        result["_type"] = "system_error"
    elif "error" in result and result.get("_type") is None:
        result["_type"] = "user_error"
    return result


def _improve_read_errors(result: dict) -> dict:
    err = result.get("error", "")
    if "not found" in err.lower() or "no such file" in err.lower():
        result["_type"] = "user_error"
        result["hint"] = "Check the path with the `sh` tool: ls <directory>"
    elif "error" in result and result.get("_type") is None:
        result["_type"] = "system_error"
    return result


def _improve_sh_errors(result: dict) -> dict:
    err = result.get("error", "")
    if "not in read-only allowlist" in err:
        result["_type"] = "user_error"
        result["hint"] = (
            "The `sh` tool only runs read-only commands. "
            "For writes/installs use Claude Code's built-in Bash tool."
        )
    elif "shell metacharacters" in err:
        result["_type"] = "user_error"
        result["hint"] = "Run each command separately; pipes and redirects are not supported."
    elif "not found" in err:
        result["_type"] = "system_error"
        result["hint"] = "The binary is not in PATH. Install it or check with: which <command>"
    return result


# ---------------------------------------------------------------------------
# Background usage sync
# ---------------------------------------------------------------------------

# Runs on a daemon thread spawned after the tool result is built; never blocks the tool response.
def _sync_usage_bg() -> None:
    """Fire-and-forget: sync one call to the backend; update quota state if exhausted."""
    global _quota_exceeded, _upgrade_url
    try:
        creds = load_credentials()
        if creds is None:
            return
        from .usage_client import UsageClientError, post_sync
        data = post_sync(creds, tool_calls=1)

        # If the backend issued a refreshed JWT, persist it so subsequent syncs use it.
        new_jwt = data.get("jwt")
        if new_jwt and new_jwt != creds.jwt:
            from dataclasses import replace
            updated = replace(creds, jwt=new_jwt)  # type: ignore[call-arg]
            try:
                save_credentials(updated)
            except Exception:
                pass

        # Check quota.
        if data.get("upgrade_url"):
            _quota_exceeded = True
            _upgrade_url = data["upgrade_url"]
    except Exception:
        pass  # never fail the tool because of a sync error


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

async def _amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def cli() -> None:
    asyncio.run(_amain())
