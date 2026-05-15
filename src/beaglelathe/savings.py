"""Local savings counter backed by SQLite at ~/.beaglelathe/state.db.

Tracks tool calls and estimates calls/tokens/cost saved vs vanilla Claude Code.
No server-side telemetry: data never leaves the machine.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Estimated vanilla round-trips collapsed by each BeagleLathe tool call.
# search: replaces glob + grep + read (~3 calls → 1), saves 2
# edit:   replaces read + edit + verify-read (~3 calls → 1), saves 2
# read:   replaces cat + head/tail pattern (~2 calls → 1), saves 1
# sh:     structured vs raw Bash, partial savings (~0.5)
CALLS_SAVED_PER_USE: dict[str, float] = {
    "search": 2.0,
    "edit": 2.0,
    "read": 1.0,
    "sh": 0.5,
}

# Rough average tokens per vanilla Claude Code tool round-trip (input + output combined).
TOKENS_PER_VANILLA_CALL = 500

# Approximate Claude Sonnet cost per token (USD) — used only for the local estimate.
COST_PER_TOKEN_USD = 0.000003

# Estimated wall-clock seconds saved per replaced vanilla call. Calibrated against
# the local A/B benchmark (scripts/benchmark.py), which measured ~2.85s of wall
# time saved per replaced call (model turn + tool exec). Rounded down for conservatism.
SECONDS_PER_VANILLA_CALL = 2.5


def state_db_path() -> Path:
    home = Path(os.environ.get("BEAGLELATHE_HOME", str(Path.home() / ".beaglelathe")))
    return home / "state.db"


def _open(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or state_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tool_calls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tool      TEXT    NOT NULL,
            called_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    # Seed the offline-grace clock on first DB creation so a freshly installed
    # user gets the full grace window before the timer can fire. INSERT OR
    # IGNORE means subsequent opens leave the value alone.
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('last_server_contact_at', datetime('now'))"
    )
    conn.commit()
    return conn


# Offline grace period — tool calls are blocked once this many hours pass without a
# successful backend response. Tuned to ride out hotel wifi / a day off, short
# enough that a hostile client can't run forever on a tampered local clock without
# the server ever metering it.
DEFAULT_OFFLINE_GRACE_HOURS = 24.0


def offline_grace_hours() -> float:
    """Effective grace window, honoring BEAGLELATHE_OFFLINE_GRACE_HOURS for tests/tuning."""
    raw = os.environ.get("BEAGLELATHE_OFFLINE_GRACE_HOURS")
    if not raw:
        return DEFAULT_OFFLINE_GRACE_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_OFFLINE_GRACE_HOURS
    return value if value > 0 else DEFAULT_OFFLINE_GRACE_HOURS


def set_last_server_contact(
    ts: Optional[datetime] = None, db_path: Optional[Path] = None
) -> None:
    """Stamp the time of the most recent successful backend response. Swallows errors."""
    try:
        conn = _open(db_path)
        if ts is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_server_contact_at', datetime('now')) "
                "ON CONFLICT (key) DO UPDATE SET value = datetime('now')"
            )
        else:
            iso = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_server_contact_at', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (iso,),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_last_server_contact(db_path: Optional[Path] = None) -> Optional[datetime]:
    """Return the timestamp of the last successful backend response (UTC), or None."""
    try:
        conn = _open(db_path)
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_server_contact_at'"
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    raw = row[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def offline_too_long(
    db_path: Optional[Path] = None, now: Optional[datetime] = None
) -> bool:
    """True when no successful backend response has landed inside the grace window."""
    contact = get_last_server_contact(db_path)
    if contact is None:
        # No record at all — `_open()` should have seeded it, but if it didn't,
        # err on the side of blocking so we never silently slip past the gate.
        return True
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=offline_grace_hours())
    return contact < cutoff


def record_call(tool: str, db_path: Optional[Path] = None) -> None:
    """Append one tool call to the local state DB. Swallows all exceptions."""
    try:
        conn = _open(db_path)
        conn.execute("INSERT INTO tool_calls (tool) VALUES (?)", (tool,))
        conn.commit()
        conn.close()
    except Exception:
        pass


@dataclass
class SavingsSummary:
    total_calls: int
    calls_saved: int
    tokens_saved: int
    cost_saved_usd: float
    time_saved_seconds: float
    by_tool: dict[str, int]


def compute_savings(db_path: Optional[Path] = None) -> SavingsSummary:
    """Read state.db and return aggregated savings metrics."""
    try:
        conn = _open(db_path)
        rows = conn.execute(
            "SELECT tool, COUNT(*) FROM tool_calls GROUP BY tool"
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    by_tool: dict[str, int] = {}
    total_calls = 0
    calls_saved_f: float = 0.0

    for tool, count in rows:
        by_tool[tool] = count
        total_calls += count
        calls_saved_f += CALLS_SAVED_PER_USE.get(tool, 0.0) * count

    calls_saved = round(calls_saved_f)
    tokens_saved = round(calls_saved_f * TOKENS_PER_VANILLA_CALL)
    cost_saved = round(tokens_saved * COST_PER_TOKEN_USD, 4)
    time_saved_seconds = calls_saved_f * SECONDS_PER_VANILLA_CALL

    return SavingsSummary(
        total_calls=total_calls,
        calls_saved=calls_saved,
        tokens_saved=tokens_saved,
        cost_saved_usd=cost_saved,
        time_saved_seconds=time_saved_seconds,
        by_tool=by_tool,
    )


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(round(seconds)), 60)
        return f"{m}m {s}s"
    h, rem = divmod(int(round(seconds)), 3600)
    m = rem // 60
    return f"{h}h {m}m"


def format_summary(s: SavingsSummary) -> str:
    lines = [
        f"total calls:   {s.total_calls:,}",
        f"calls saved:   {s.calls_saved:,}  (vs vanilla Claude Code)",
        f"tokens saved:  {s.tokens_saved:,}",
        f"cost saved:    ${s.cost_saved_usd:.2f}  (est., Claude Sonnet rate)",
        f"time saved:    {_format_duration(s.time_saved_seconds)}  (est., {SECONDS_PER_VANILLA_CALL:.1f}s per replaced call)",
        "",
        "by tool:",
    ]
    tool_order = ["search", "edit", "read", "sh"]
    seen = set()
    for tool in tool_order:
        if tool in s.by_tool:
            count = s.by_tool[tool]
            saved = round(CALLS_SAVED_PER_USE.get(tool, 0.0) * count)
            lines.append(f"  {tool:<8} {count:>5} calls  →  {saved} saved")
            seen.add(tool)
    for tool, count in s.by_tool.items():
        if tool not in seen:
            saved = round(CALLS_SAVED_PER_USE.get(tool, 0.0) * count)
            lines.append(f"  {tool:<8} {count:>5} calls  →  {saved} saved")
    return "\n".join(lines)
