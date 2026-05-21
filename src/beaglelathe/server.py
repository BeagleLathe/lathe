"""MCP server: registers `search`, `edit`, `read`, `sh`, `run_tests`, structured git tools, and `lint_and_typecheck` over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth.credentials import CredentialsError, load_credentials, save_credentials
from .dedup import DedupCache
from .formatting import format_result
from .savings import offline_grace_hours, offline_too_long, record_call
from .tools.edit import apply_edits
from .tools.edit_glob import run_edit_glob
from .tools.lathe_meta import (
    DESCRIPTION as LATHE_DESC,
    INPUT_SCHEMA as LATHE_SCHEMA,
    run_lathe,
)
from .tools.read import run_read
from .tools.search import run_search
# l3: only search/edit/edit_glob/read are advertised to the model. The
# dispatch handlers for the other tools remain importable so call_tool can
# still route to them if a future revert re-exposes them (or if the user
# calls them by name directly via a custom MCP client).
from .tools.sh import run_sh
from .tools.run_tests import run_tests
from .tools.git_ops import (
    run_git_status,
    run_git_diff,
    run_changed_files,
)
from .tools.lint_and_typecheck import run_lint_and_typecheck
from .tools.extract_todos import run_extract_todos
from .tools.commit_message import run_commit_message

app: Server = Server("beaglelathe")

# Per-process state — reset on each MCP server start.
_welcomed: bool = False
_quota_exceeded: bool = False
_upgrade_url: str | None = None
_dedup_cache: DedupCache = DedupCache(window=10)
_DEDUP_TOOLS: frozenset[str] = frozenset({"search", "read"})

# l4 experiment: BL advertises ONE MCP tool (`lathe`) with an `action`
# discriminator that dispatches to search/edit/edit_glob/read. The other 8
# tools (sh, run_tests, git_status, git_diff, changed_files,
# lint_and_typecheck, extract_todos, commit_message) still have dispatch
# arms in call_tool() so they remain callable by name — they're just not
# advertised. Hypothesis: per-session cache_creation is dominated by the
# count of advertised tools, not by any single schema's size; collapsing
# four tools into one should cut that overhead meaningfully. Reverting L4
# is a one-file edit — restore the four per-tool Tool() entries below.


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="lathe", description=LATHE_DESC, inputSchema=LATHE_SCHEMA),
    ]


# Claude Code renders MCP `structuredContent` in preference to the text block,
# so emitting the raw result dict alongside our formatted text causes the JSON
# envelope to shadow the human-readable rendering in the console. We keep
# `call_tool` returning the dict so programmatic consumers (smoke scripts,
# tests) can opt in via the env var; the MCP handler below strips it by
# default so the user-facing surface only sees the formatted text.
_EXPOSE_STRUCTURED_ENV: str = "BEAGLELATHE_EXPOSE_STRUCTURED"


def _expose_structured_content() -> bool:
    return os.environ.get(_EXPOSE_STRUCTURED_ENV) == "1"


def _payload_bytes(payload: object) -> int | None:
    """Serialized JSON length in bytes for savings measurement. None on failure."""
    try:
        return len(json.dumps(payload, default=str).encode("utf-8"))
    except Exception:
        return None


async def call_tool(name: str, arguments: dict) -> tuple[list[TextContent], dict]:
    global _welcomed, _quota_exceeded, _upgrade_url
    cwd = Path.cwd()
    args = arguments or {}
    input_bytes = _payload_bytes(args)

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
        record_call(name, output_bytes=_payload_bytes(payload), input_bytes=input_bytes)
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
        record_call(name, output_bytes=_payload_bytes(payload), input_bytes=input_bytes)
        return [TextContent(type="text", text=text)], payload

    # Dispatch to the appropriate tool.
    if name == "lathe":
        # L4 meta-tool. Apply the per-action error-improvement that the
        # underlying tools would have received via their direct dispatch
        # arms below, so error_type tagging stays consistent regardless of
        # how the call entered.
        result = run_lathe(args, cwd)
        sub_action = args.get("action") if isinstance(args, dict) else None
        if sub_action == "search":
            result = _improve_search_errors(result)
        elif sub_action == "read":
            result = _improve_read_errors(result)
    elif name == "search":
        result = run_search(args, cwd)
        result = _improve_search_errors(result)
    elif name == "edit":
        result = apply_edits(
            list(args.get("edits", [])),
            cwd,
            validate=bool(args.get("validate", True)),
            include_diffs=bool(args.get("include_diffs", False)),
        )
    elif name == "edit_glob":
        result = run_edit_glob(args, cwd)
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

    # Per-session output dedup (search/read only). Identical content emitted
    # in the last N turns is replaced with a small stub; pass force=true to
    # bypass. The L4 meta-tool routes search/read through `name == "lathe"`
    # — peek at the action and the nested `force` so dedup behaves the same
    # whether the call entered via the per-tool arm or the meta-tool arm.
    dedup_name: str | None = None
    dedup_force = False
    if name in _DEDUP_TOOLS:
        dedup_name = name
        dedup_force = bool(args.get("force", False))
    elif name == "lathe":
        sub_action = args.get("action") if isinstance(args, dict) else None
        if sub_action in _DEDUP_TOOLS:
            dedup_name = sub_action
            sub_args = args.get("args") or {}
            dedup_force = bool(sub_args.get("force", False)) if isinstance(sub_args, dict) else False

    if dedup_name is not None and "error" not in result:
        _dedup_cache.begin_call()
        if dedup_force:
            _dedup_cache.force_record(result, dedup_name)
        else:
            stub = _dedup_cache.check_or_record(result, dedup_name)
            if stub is not None:
                result = stub

    if notice:
        result = {"_notice": notice, **result}

    # Record this call in the local savings DB (best-effort, never blocks).
    # Done post-dispatch so output_bytes reflects the actual returned payload.
    record_call(name, output_bytes=_payload_bytes(result), input_bytes=input_bytes)

    # Background sync to the auth backend (best-effort, never blocks the response).
    threading.Thread(target=_sync_usage_bg, daemon=True).start()

    text = format_result(name, args, result)
    return [TextContent(type="text", text=text)], result


@app.call_tool()
async def _mcp_call_tool(name: str, arguments: dict):
    content, result = await call_tool(name, arguments)
    if _expose_structured_content():
        return content, result
    return content


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
        from .usage_client import post_sync
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
