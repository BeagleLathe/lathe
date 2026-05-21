"""Per-session output dedup for `search` and `read`.

When the same content would be emitted twice within `window` turns, the
second emission is replaced with a small stub `{unchanged_since_turn: K, ...}`
that points back at the earlier turn. The agent can pass `force: true` to
bypass dedup if it has a reason to re-fetch (e.g., expecting the file changed).

State is in-memory and per-process; the MCP server is per-session, so the
cache resets every time the server starts. No persistence.

Default window N=10 turns. Chosen by inspection of typical agent trajectories
on the rename benchmark: between an initial `search` and a follow-up rename,
the agent typically takes 2-5 turns; N=10 gives 2× headroom without trapping
content that's genuinely stale (file changed N turns ago, agent re-reads to
check). Smaller N risks missing dedup opportunities; larger N risks stale
dedup against a payload the agent has already mentally aged out of context.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


class DedupCache:
    """Sliding-window dedup cache, keyed on serialized payload hash.

    `begin_call()` is invoked at the top of each tool dispatch to advance the
    turn counter. `check_or_record()` consults the window: if the payload
    hash matches a prior entry within `window` turns, returns a stub dict;
    otherwise records the new emission and returns None.
    """

    def __init__(self, window: int = 10) -> None:
        self._window = window
        self._recent: list[tuple[str, int]] = []
        self._turn = 0

    def begin_call(self) -> int:
        self._turn += 1
        return self._turn

    def check_or_record(self, payload: dict, tool: str) -> Optional[dict[str, Any]]:
        """Return a stub if `payload` matches a recent emission, else None."""
        self._gc()
        h = self._hash(payload)
        for prev_h, prev_turn in reversed(self._recent):
            if prev_h == h:
                return {
                    "unchanged_since_turn": prev_turn,
                    "current_turn": self._turn,
                    "tool": tool,
                    "hint": (
                        "Output identical to a prior call this session "
                        f"(turn {prev_turn}). Pass force=true to re-emit."
                    ),
                }
        self._recent.append((h, self._turn))
        return None

    def force_record(self, payload: dict, tool: str) -> None:  # noqa: ARG002 — tool kept for parity
        """Record the payload as emitted at the current turn, without checking.

        Used when the caller passes `force=true` — we still want the cache to
        remember the latest emission so future calls dedupe against the
        refreshed turn, not the stale one."""
        self._gc()
        h = self._hash(payload)
        # Drop any prior identical-hash entry to keep the recorded turn fresh.
        self._recent = [(ph, pt) for ph, pt in self._recent if ph != h]
        self._recent.append((h, self._turn))

    def _gc(self) -> None:
        cutoff = self._turn - self._window
        while self._recent and self._recent[0][1] <= cutoff:
            self._recent.pop(0)

    @staticmethod
    def _hash(payload: dict) -> str:
        try:
            s = json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError):
            s = repr(payload)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()
