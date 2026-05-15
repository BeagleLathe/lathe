"""User-facing CLI subcommands: login / logout / whoami / status / savings / upgrade / statusline.

Invoked from `python -m beaglelathe <subcommand>`. Each function returns the
process exit code (0 = success).
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from typing import Optional

from .client import (
    AuthClient,
    AuthError,
    credentials_from_poll_ok,
    default_base_url,
    device_fingerprint,
)
from .credentials import (
    CredentialsError,
    clear_credentials,
    credentials_path,
    load_credentials,
    save_credentials,
)


def login_command(args: argparse.Namespace) -> int:
    base_url = (args.api_url or default_base_url()).rstrip("/")
    open_browser = not args.no_browser
    print(f"Logging in via {base_url} ...", file=sys.stderr)
    try:
        with AuthClient(base_url=base_url, poll_interval=args.poll_interval) as client:
            start = client.start(fingerprint=device_fingerprint())
            login_url = start["login_url"]
            session_id = str(start["login_session_id"])
            poll_secret = start["poll_secret"]
            print(f"Open this URL in your browser to sign in:\n  {login_url}", file=sys.stderr)
            if open_browser:
                try:
                    webbrowser.open(login_url, new=2)
                except Exception:
                    pass
            print("Waiting for confirmation ...", file=sys.stderr, flush=True)
            poll_ok = client.poll_until_complete(
                session_id,
                poll_secret,
                deadline_seconds=args.timeout,
                on_pending=lambda: print(".", end="", file=sys.stderr, flush=True),
            )
            print("", file=sys.stderr)
    except AuthError as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 2

    creds = credentials_from_poll_ok(poll_ok, base_url=base_url)
    path = save_credentials(creds)
    # Login is a successful backend round-trip — reset the offline-grace timer
    # so a freshly authenticated user gets the full window before any block.
    try:
        from ..savings import set_last_server_contact
        set_last_server_contact()
    except Exception:
        pass
    plan_label = creds.plan
    budget_label = "unlimited" if creds.budget_remaining is None else str(creds.budget_remaining)
    print(
        f"Signed in as {creds.email} (plan={plan_label}, budget_remaining={budget_label}).\n"
        f"Saved credentials to {path}",
        file=sys.stderr,
    )
    return 0


def logout_command(args: argparse.Namespace) -> int:
    removed = clear_credentials()
    if removed:
        print(f"Cleared credentials at {credentials_path()}", file=sys.stderr)
    else:
        print("No credentials file to clear.", file=sys.stderr)
    return 0


def whoami_command(args: argparse.Namespace) -> int:
    try:
        creds = load_credentials()
    except CredentialsError as e:
        print(f"could not read credentials: {e}", file=sys.stderr)
        return 2
    if creds is None:
        print("not logged in (run `beaglelathe login`)", file=sys.stderr)
        return 1
    budget_label = "unlimited" if creds.budget_remaining is None else str(creds.budget_remaining)
    print(
        f"email:            {creds.email}\n"
        f"user_id:          {creds.user_id}\n"
        f"plan:             {creds.plan}\n"
        f"budget_remaining: {budget_label}\n"
        f"budget_resets_at: {creds.budget_resets_at}\n"
        f"base_url:         {creds.base_url}\n"
        f"issued_at:        {creds.issued_at}\n"
        f"file:             {credentials_path()}"
    )
    return 0


def status_command(args: argparse.Namespace) -> int:
    try:
        creds = load_credentials()
    except CredentialsError as e:
        print(f"could not read credentials: {e}", file=sys.stderr)
        return 2
    if creds is None:
        print(
            "Not logged in.\n"
            "Run `beaglelathe login` to connect your account.",
            file=sys.stderr,
        )
        return 1

    try:
        from ..usage_client import UsageClientError, get_status
        data = get_status(creds)
    except Exception as e:
        # Network down or token expired — show cached data with a warning.
        budget_label = (
            "unlimited" if creds.budget_remaining is None else str(creds.budget_remaining)
        )
        print(
            f"Warning: could not reach backend ({e}).\n"
            f"Showing cached credentials:\n"
            f"  email:            {creds.email}\n"
            f"  plan:             {creds.plan}\n"
            f"  budget_remaining: {budget_label} (cached)\n"
            f"  budget_resets_at: {creds.budget_resets_at} (cached)",
            file=sys.stderr,
        )
        return 1

    plan = data.get("plan", creds.plan)
    calls_used = data.get("tool_calls_this_month", "?")
    remaining = data.get("budget_remaining")
    resets_at = data.get("budget_resets_at", creds.budget_resets_at)

    remaining_label = "unlimited" if remaining is None else str(remaining)
    plan_display = "Free tier" if plan == "free" else plan.capitalize()

    print(
        f"plan:       {plan_display}\n"
        f"calls used: {calls_used} this month\n"
        f"remaining:  {remaining_label} calls\n"
        f"resets:     {resets_at}\n"
        f"account:    {creds.email}"
    )
    return 0


def savings_command(args: argparse.Namespace) -> int:
    from ..savings import compute_savings, format_summary
    summary = compute_savings()
    if summary.total_calls == 0:
        print(
            "No calls recorded yet.\n"
            "BeagleLathe tracks savings locally as you use the tools.",
            file=sys.stderr,
        )
        return 0
    print(format_summary(summary))
    return 0


def statusline_command(args: argparse.Namespace) -> int:
    """Print a single status line for the Claude Code statusLine integration.

    Reads from local files only (no network). Never raises — on any error,
    prints a neutral fallback and exits 0 so the statusline never goes blank.
    """
    try:
        creds = load_credentials()
    except CredentialsError:
        creds = None

    try:
        from ..savings import compute_savings
        summary = compute_savings()
        calls_saved = summary.calls_saved
    except Exception:
        calls_saved = 0

    if creds is None:
        suffix = f"saved {calls_saved}" if calls_saved else "not signed in"
        print(f"Lathe · {suffix}")
        return 0

    if creds.budget_remaining is None:
        budget = "unlimited"
    else:
        budget = f"{creds.budget_remaining} left"

    plan = "Free" if creds.plan == "free" else creds.plan.capitalize()
    saved_part = f" · saved {calls_saved}" if calls_saved else ""
    print(f"Lathe · {plan} · {budget}{saved_part}")
    return 0


def upgrade_command(args: argparse.Namespace) -> int:
    try:
        creds = load_credentials()
    except CredentialsError as e:
        print(f"could not read credentials: {e}", file=sys.stderr)
        return 2
    if creds is None:
        print(
            "Not logged in. Run `beaglelathe login` first.",
            file=sys.stderr,
        )
        return 1

    try:
        from ..usage_client import UsageClientError, post_checkout
        checkout_url = post_checkout(creds)
    except Exception as e:
        print(
            f"could not start checkout: {e}\n"
            f"If the problem persists, log out and back in: `beaglelathe login`",
            file=sys.stderr,
        )
        return 2

    print(f"Opening Stripe checkout:\n  {checkout_url}", file=sys.stderr)
    if not args.no_browser:
        try:
            webbrowser.open(checkout_url, new=2)
        except Exception:
            pass
    return 0


def install_command(args: argparse.Namespace) -> int:
    """Write .mcp.json into the target project so Claude Code picks up BeagleLathe."""
    import json
    from pathlib import Path

    target_dir = Path(args.project or ".").resolve()
    mcp_path = target_dir / ".mcp.json"

    server_entry: dict = {"command": "python", "args": ["-m", "beaglelathe"]}

    if mcp_path.exists():
        try:
            config = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            print(f"error: {mcp_path} exists but is not valid JSON", file=sys.stderr)
            return 1
        servers = config.setdefault("mcpServers", {})
        if "lathe" in servers:
            print(f"{mcp_path}: lathe already configured — nothing to do.", file=sys.stderr)
            return 0
        servers["lathe"] = server_entry
        action = "updated"
    else:
        config = {"mcpServers": {"lathe": server_entry}}
        action = "created"

    mcp_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"{action} {mcp_path}", file=sys.stderr)
    print("Restart Claude Code (or run /mcp restart) to activate.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beaglelathe",
        description="BeagleLathe MCP server and CLI. Run with no args to start the MCP server.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run the MCP server on stdio (default).")

    login = sub.add_parser("login", help="Sign in via magic link.")
    login.add_argument(
        "--api-url",
        default=None,
        help="Backend base URL (defaults to $BEAGLELATHE_API_URL or http://localhost:8000).",
    )
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the login URL instead of opening it.",
    )
    login.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Seconds to wait for confirmation (default 600).",
    )
    login.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between /auth/poll calls (default 1.0).",
    )
    login.set_defaults(func=login_command)

    logout = sub.add_parser("logout", help="Clear stored credentials.")
    logout.set_defaults(func=logout_command)

    whoami = sub.add_parser("whoami", help="Show the currently signed-in user.")
    whoami.set_defaults(func=whoami_command)

    status = sub.add_parser("status", help="Show plan, calls used, and budget remaining.")
    status.set_defaults(func=status_command)

    savings = sub.add_parser("savings", help="Show local savings counter (calls/tokens/cost).")
    savings.set_defaults(func=savings_command)

    statusline = sub.add_parser(
        "statusline",
        help="Print a one-line status summary for Claude Code's statusLine.",
    )
    statusline.set_defaults(func=statusline_command)

    upgrade = sub.add_parser("upgrade", help="Open Stripe checkout to upgrade to Pro.")
    upgrade.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the checkout URL instead of opening it.",
    )
    upgrade.set_defaults(func=upgrade_command)

    install = sub.add_parser(
        "install",
        help="Write .mcp.json to a project directory so Claude Code finds BeagleLathe.",
    )
    install.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Project directory to configure (defaults to the current directory).",
    )
    install.set_defaults(func=install_command)

    return parser
