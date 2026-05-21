"""Local savings counter backed by SQLite at ~/.beaglelathe/state.db.

Tracks tool calls and estimates calls/tokens/cost saved vs vanilla Claude Code.
No server-side telemetry: data never leaves the machine.

v1 measurement-based estimator: each call records its actual output byte
size, which is multiplied by a per-tool "vanilla equivalent" factor
(VANILLA_BYTES_MULTIPLIER) calibrated against bench-logs/, then amplified
by CACHE_AMPLIFICATION_FACTOR to account for cached-turn re-reads, then
priced using a per-model rate table (MODEL_PRICES). Calls predating the
byte-recording schema (NULL bytes) fall back to the older constant
estimate so user history is preserved.
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

# Average ratio of vanilla-equivalent output bytes to BL output bytes, per tool.
# Calibrated from bench-logs/ (n=4 paired runs on the rename fixture, 2026-05-17).
# search: vanilla glob+grep+read for the same query returns ~2.8x bytes.
# edit:   vanilla read+edit+verify returns ~2.1x bytes.
# read:   vanilla full-file Read returns ~3.4x bytes vs truncated mode.
# sh:     vanilla raw Bash output averages ~1.4x vs BL's allowlisted output.
# These are n=4 estimates — expand the bench and recalibrate before publishing.
VANILLA_BYTES_MULTIPLIER: dict[str, float] = {
    "search": 2.8,
    "edit":   2.1,
    "read":   3.4,
    "sh":     1.4,
}
DEFAULT_VANILLA_BYTES_MULTIPLIER = 1.5

# Rough char-to-token ratio for code-heavy content. Conservative.
CHARS_PER_TOKEN = 3.7

# Cache amplification: every token saved at emission is also "saved" on each
# subsequent cached turn at ~0.1x the per-token base price. Bench data shows
# ~5 subsequent cached turns per emitted output on average → effective
# savings multiplier = 1 + 5 * 0.1 = 1.5.
CACHE_AMPLIFICATION_FACTOR = 1.5

# Per-model price table (USD per token, by category). Verify against current
# pricing at platform.claude.com/pricing before merging — these are the
# user-facing cost numbers.
MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"input": 1.5e-5, "output": 7.5e-5, "cache_create": 1.875e-5, "cache_read": 1.5e-6},
    "claude-sonnet-4-6": {"input": 3.0e-6, "output": 1.5e-5, "cache_create": 3.75e-6, "cache_read": 3.0e-7},
    "claude-haiku-4-5":  {"input": 1.0e-6, "output": 5.0e-6, "cache_create": 1.25e-6, "cache_read": 1.0e-7},
}
DEFAULT_MODEL = "claude-sonnet-4-6"

# Estimated wall-clock seconds saved per replaced vanilla call. Calibrated against
# the local A/B benchmark (scripts/benchmark.py), which measured ~2.85s of wall
# time saved per replaced call (model turn + tool exec). Rounded down for conservatism.
SECONDS_PER_VANILLA_CALL = 2.5

# Legacy fallback: bytes-less rows use this many tokens per CALLS_SAVED_PER_USE unit.
# Preserved so existing users' history doesn't reset to zero on first launch
# of the byte-measurement build.
_LEGACY_TOKENS_PER_CALL = 500


def current_model_prices() -> tuple[str, dict[str, float]]:
    """Resolve the active model from BEAGLELATHE_MODEL, falling back to Sonnet."""
    model = os.environ.get("BEAGLELATHE_MODEL", DEFAULT_MODEL)
    if model in MODEL_PRICES:
        return model, MODEL_PRICES[model]
    return DEFAULT_MODEL, MODEL_PRICES[DEFAULT_MODEL]


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
    # Idempotent migration: add byte-count columns to pre-existing DBs.
    # OperationalError fires when the column already exists; swallow it.
    for col in ("output_bytes", "input_bytes"):
        try:
            conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass
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


def record_call(
    tool: str,
    output_bytes: int | None = None,
    input_bytes: int | None = None,
    db_path: Optional[Path] = None,
) -> None:
    """Append one tool call to the local state DB. Swallows all exceptions.

    Byte counts are optional — pre-byte-measurement callers (or rows where
    serialization fails) leave them NULL and fall back to the legacy estimate
    in compute_savings().
    """
    try:
        conn = _open(db_path)
        conn.execute(
            "INSERT INTO tool_calls (tool, output_bytes, input_bytes) VALUES (?, ?, ?)",
            (tool, output_bytes, input_bytes),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@dataclass
class SavingsSummary:
    total_calls: int
    calls_saved: int
    tokens_saved: int                          # effective_tokens_saved (after cache amp), rounded
    tokens_saved_at_emission: int
    tokens_saved_via_cache_amplification: int
    cost_saved_usd: float                      # cost under the active model (BEAGLELATHE_MODEL)
    cost_saved_usd_by_model: dict[str, float]  # cost under EVERY priced model — Sonnet + Opus + Haiku
    time_saved_seconds: float
    model_used: str
    by_tool: dict[str, int]


def _cost_at_emission_tokens(tokens_at_emission_f: float, prices: dict[str, float]) -> float:
    """Cost of replacing this many emission-tokens of vanilla output, under the given
    price table. The cache-amplification component is priced at the cache_read rate
    (subsequent turns re-read the saved tokens from cache, not re-generate them)."""
    return (
        tokens_at_emission_f * prices["output"]
        + tokens_at_emission_f * (CACHE_AMPLIFICATION_FACTOR - 1) * prices["cache_read"]
    )


def compute_savings(db_path: Optional[Path] = None) -> SavingsSummary:
    """Read state.db and return aggregated savings metrics.

    Per tool: byte-bearing rows feed the measurement-based formula; NULL rows
    fall back to CALLS_SAVED_PER_USE × _LEGACY_TOKENS_PER_CALL so existing
    history isn't lost. Cache amplification is applied uniformly. `cost_saved_usd`
    is priced under the active model (BEAGLELATHE_MODEL, default Sonnet);
    `cost_saved_usd_by_model` carries the same savings priced under every
    model in MODEL_PRICES so callers can show Sonnet + Opus side by side.
    """
    model_name, prices = current_model_prices()

    try:
        conn = _open(db_path)
        rows = conn.execute(
            """SELECT tool,
                      COUNT(*),
                      COALESCE(SUM(output_bytes), 0),
                      SUM(CASE WHEN output_bytes IS NULL THEN 1 ELSE 0 END)
               FROM tool_calls
               GROUP BY tool"""
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    by_tool: dict[str, int] = {}
    total_calls = 0
    calls_saved_f: float = 0.0
    tokens_at_emission_f: float = 0.0

    for tool, count, total_bytes, null_count in rows:
        by_tool[tool] = count
        total_calls += count
        calls_saved_f += CALLS_SAVED_PER_USE.get(tool, 0.0) * count

        multiplier = VANILLA_BYTES_MULTIPLIER.get(tool, DEFAULT_VANILLA_BYTES_MULTIPLIER)
        if total_bytes and total_bytes > 0:
            tokens_emitted = total_bytes / CHARS_PER_TOKEN
            tokens_at_emission_f += tokens_emitted * (multiplier - 1)
            # Plus any pre-migration NULL-byte rows for this tool — use legacy estimate.
            if null_count:
                tokens_at_emission_f += (
                    CALLS_SAVED_PER_USE.get(tool, 0.0) * null_count * _LEGACY_TOKENS_PER_CALL
                )
        else:
            # No byte data at all for this tool — pure legacy estimate.
            tokens_at_emission_f += (
                CALLS_SAVED_PER_USE.get(tool, 0.0) * count * _LEGACY_TOKENS_PER_CALL
            )

    effective_tokens_f = tokens_at_emission_f * CACHE_AMPLIFICATION_FACTOR
    cost_f = _cost_at_emission_tokens(tokens_at_emission_f, prices)

    tokens_at_emission = round(tokens_at_emission_f)
    tokens_effective = round(effective_tokens_f)

    cost_by_model = {
        name: round(_cost_at_emission_tokens(tokens_at_emission_f, p), 4)
        for name, p in MODEL_PRICES.items()
    }

    return SavingsSummary(
        total_calls=total_calls,
        calls_saved=round(calls_saved_f),
        tokens_saved=tokens_effective,
        tokens_saved_at_emission=tokens_at_emission,
        tokens_saved_via_cache_amplification=tokens_effective - tokens_at_emission,
        cost_saved_usd=round(cost_f, 4),
        cost_saved_usd_by_model=cost_by_model,
        time_saved_seconds=calls_saved_f * SECONDS_PER_VANILLA_CALL,
        model_used=model_name,
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


def _per_tool_tokens_saved(tool: str, count: int, db_path: Optional[Path]) -> int:
    """Re-derive the rounded emission-tokens saved for a single tool. Display only."""
    multiplier = VANILLA_BYTES_MULTIPLIER.get(tool, DEFAULT_VANILLA_BYTES_MULTIPLIER)
    try:
        conn = _open(db_path)
        row = conn.execute(
            "SELECT COALESCE(SUM(output_bytes), 0), "
            "       SUM(CASE WHEN output_bytes IS NULL THEN 1 ELSE 0 END) "
            "FROM tool_calls WHERE tool = ?",
            (tool,),
        ).fetchone()
        conn.close()
    except Exception:
        row = (0, count)
    total_bytes, null_count = row or (0, count)
    if total_bytes and total_bytes > 0:
        emitted = total_bytes / CHARS_PER_TOKEN
        saved = emitted * (multiplier - 1)
        if null_count:
            saved += CALLS_SAVED_PER_USE.get(tool, 0.0) * null_count * _LEGACY_TOKENS_PER_CALL
    else:
        saved = CALLS_SAVED_PER_USE.get(tool, 0.0) * count * _LEGACY_TOKENS_PER_CALL
    return round(saved)


def format_summary(s: SavingsSummary, db_path: Optional[Path] = None) -> str:
    sonnet_cost = s.cost_saved_usd_by_model.get("claude-sonnet-4-6", 0.0)
    opus_cost = s.cost_saved_usd_by_model.get("claude-opus-4-7", 0.0)
    lines = [
        f"total calls:                    {s.total_calls:,}",
        f"calls saved:                    {s.calls_saved:,}  (vs vanilla Claude Code)",
        f"tokens saved (emission):        {s.tokens_saved_at_emission:,}",
        f"tokens saved (cache):           {s.tokens_saved_via_cache_amplification:,}",
        f"tokens saved (total):           {s.tokens_saved:,}",
        f"cost saved (claude-sonnet-4-6): ${sonnet_cost:.2f}",
        f"cost saved (claude-opus-4-7):   ${opus_cost:.2f}",
        f"time saved:                     {_format_duration(s.time_saved_seconds)}  (est., {SECONDS_PER_VANILLA_CALL:.1f}s per replaced call)",
        "",
        "by tool:",
    ]
    tool_order = ["search", "edit", "read", "sh"]
    seen: set[str] = set()
    for tool in tool_order:
        if tool in s.by_tool:
            count = s.by_tool[tool]
            saved = _per_tool_tokens_saved(tool, count, db_path)
            lines.append(f"  {tool:<8} {count:>5} calls  →  ~{saved:,} tokens saved")
            seen.add(tool)
    for tool, count in s.by_tool.items():
        if tool not in seen:
            saved = _per_tool_tokens_saved(tool, count, db_path)
            lines.append(f"  {tool:<8} {count:>5} calls  →  ~{saved:,} tokens saved")
    return "\n".join(lines)
