"""`lathe` meta-tool (L4): one MCP tool with an `action` discriminator that
dispatches to the search / edit / edit_glob / read handlers.

Hypothesis: per-session cache_creation is dominated by the number of MCP tools
advertised, not by the size of any single schema. Collapsing four tools into
one cuts the per-tool overhead by 4× while paying for one slightly-larger
schema. Combined with `alwaysLoad: true`, the per-session schema-load tax
should drop materially vs L3.

Risk: action-routing meta-tools can degrade tool-call correctness. The model
may pick wrong actions or supply args in the wrong nested shape. The bench's
BL pass rate and BL_used count are the kill signal.

Schema choice: flat `{action, args}` rather than a `oneOf` discriminated
schema. Two reasons:
  1. Smaller cache_creation — the whole point of A4 is to shrink the
     per-session schema budget; a `oneOf` would bring back most of the size
     savings by re-embedding all four per-action schemas inline.
  2. MCP clients and JSON-schema validators in the wild handle the simpler
     shape more uniformly than `oneOf` with const discriminators.
The per-action arg requirements are documented in DESCRIPTION (so the model
sees them) and re-validated by the underlying handlers (so a malformed call
returns a clean user_error rather than crashing).

Reverting A4: drop `lathe` from server.list_tools(), restore the four
per-tool Tool() entries. The per-tool modules and dispatch arms are
untouched, so revert is a single-file diff in server.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .edit import apply_edits
from .edit_glob import run_edit_glob
from .read import run_read
from .search import run_search

_ACTIONS = ("search", "edit", "edit_glob", "read")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
        },
        "args": {
            "type": "object",
            "description": (
                "Per-action arguments. "
                "search: {pattern, path_glob?, max_results?, context_lines?, max_total_lines?, case_sensitive?, force?}. "
                "edit: {edits:[{path, old_string, new_string, replace_all?}], validate?, include_diffs?}. "
                "edit_glob: {glob, pattern, replacement, flags?, max_files?}. "
                "read: {path, mode?, symbol?, force?}."
            ),
        },
    },
    "required": ["action", "args"],
}

DESCRIPTION = (
    "Unified BeagleLathe tool. Pass action=search|edit|edit_glob|read with "
    "per-action args. search=glob+regex+ranked snippet in one call. "
    "edit=atomic multi-file edits with fuzzy whitespace/quote-drift. "
    "edit_glob=atomic regex substitution across a glob (use instead of "
    "Bash sed for renames). read=AST-truncates source files >300 lines "
    "(mode=full for verbatim)."
)


def _user_error(msg: str) -> dict[str, Any]:
    return {"error": msg, "_type": "user_error"}


def run_lathe(args: dict, cwd: Path) -> dict:
    """Dispatch a meta-tool call to the underlying per-action handler.

    Returns the handler's payload verbatim on success. On dispatch-layer
    failure (unknown action, malformed envelope) returns a clean user_error
    so the model can self-correct without a crash.
    """
    action = args.get("action") if isinstance(args, dict) else None
    if action is None:
        return _user_error(
            "`action` is required. Must be one of: " + ", ".join(_ACTIONS)
        )
    if action not in _ACTIONS:
        return _user_error(
            f"unknown action: {action!r}. Must be one of: " + ", ".join(_ACTIONS)
        )

    sub_args = args.get("args", {})
    if sub_args is None:
        sub_args = {}
    if not isinstance(sub_args, dict):
        return _user_error(
            f"`args` must be an object; got {type(sub_args).__name__}"
        )

    if action == "search":
        return run_search(sub_args, cwd)
    if action == "edit":
        return apply_edits(
            list(sub_args.get("edits", [])),
            cwd,
            validate=bool(sub_args.get("validate", True)),
            include_diffs=bool(sub_args.get("include_diffs", False)),
        )
    if action == "edit_glob":
        return run_edit_glob(sub_args, cwd)
    if action == "read":
        return run_read(sub_args, cwd)

    # Unreachable — _ACTIONS membership was already checked above.
    return _user_error(f"unknown action: {action!r}")
