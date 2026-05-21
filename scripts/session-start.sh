#!/usr/bin/env bash
# SessionStart hook for the BeagleLathe plugin.
#
# Claude Code defers MCP tool schemas — by default only the *names* of the
# BeagleLathe tools reach the model, not their descriptions. Without
# descriptions the model can't tell that these tools beat the built-in
# Read/Edit/Grep/Bash, so it never reaches for them. This hook injects the
# routing rules + a schema-preload nudge into the model's context at session
# start.
#
# L4 — meta-tool. BeagleLathe ships ONE MCP tool (`lathe`) with an `action`
# discriminator that dispatches to search / edit / edit_glob / read. The
# per-session schema budget therefore carries one tool's overhead, not four.
# The preload line and the routing prose reflect that single-tool surface.
#
# Install-path detection (A1): the prefix Claude Code registers the MCP
# tools under depends on how the server was loaded.
#
#   Plugin marketplace install: mcp__plugin_lathe_tool__*
#   Project-local .mcp.json:    mcp__tool__*   (no plugin wrapper)
#
# The MCP server key is `tool` (was `lathe` pre-rename). The probe below
# also accepts the legacy `lathe`/`beaglelathe` server keys so a user
# .mcp.json carried over from before the rename keeps working.
#
# At hook fire we probe `$CLAUDE_PROJECT_DIR/.mcp.json` (else `$PWD/.mcp.json`)
# for a known BL server-key. If we find one, the ToolSearch select line below
# emits ONLY that prefix — dead-prefix lookups are pure prompt-cache padding.
# On any detection failure we fall back to emitting both prefixes (the
# pre-A1 behavior), so a missing/unreadable .mcp.json never causes BL to
# silently fall back to vanilla tools.
#
# The drift-guard test (tests/test_session_start_hook.py) runs this script
# under each scenario (lathe .mcp.json present, plugin-only fallback, etc.)
# and asserts the right prefix shows up. Any future rename or install-path
# change that breaks the detection fails CI.
#
# Plain stdout text from a SessionStart hook is auto-wrapped as additional
# context by Claude Code (10KB limit). No JSON envelope required.

# ── A1: detect the live install path ──────────────────────────────────────
# Probe $CLAUDE_PROJECT_DIR/.mcp.json (else $PWD/.mcp.json) for a known BL
# server-key. On a hit, we emit only that install path's prefix in the
# preload line; on miss or parse-error, we fall through to listing both.
project_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
mcp_json="${project_dir}/.mcp.json"
detected_prefix=""
if [ -f "${mcp_json}" ] && command -v python3 >/dev/null 2>&1; then
  detected_prefix=$(python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    for s in (data.get("mcpServers") or {}):
        if s in ("tool", "lathe", "beaglelathe"):
            print("mcp__" + s + "__")
            sys.exit(0)
except Exception:
    pass
' "${mcp_json}" 2>/dev/null)
fi

# ── pick the preload + routing prefix ─────────────────────────────────────
# L4: one tool (`lathe`) per prefix. The select line carries every prefix
# the live install could have registered the tool under — the model only
# needs to resolve one of them to be off to the races.
if [ -n "${detected_prefix}" ]; then
  # Detected a live BL server in .mcp.json — emit only its prefix.
  select_line="${detected_prefix}lathe"
  routing_prefix="${detected_prefix}"
else
  # No .mcp.json detected — assume plugin-marketplace install path, but
  # also include the .mcp.json form as a safety net (this matches the
  # pre-A1 behavior so installs we can't probe never silently fall back).
  select_line="mcp__plugin_lathe_tool__lathe,mcp__tool__lathe"
  routing_prefix="mcp__plugin_lathe_tool__"
fi

# ── A3 experiment toggle ──────────────────────────────────────────────────
# BEAGLELATHE_HOOK_PRELOAD controls whether the hook emits an explicit
# `ToolSearch select:...` line that nudges the model to promote BL schemas
# on its first turn. Default: "on" (production behavior). Setting it to
# "off" emits ONLY the routing prose — useful for A/B-testing whether
# Claude Code auto-loads BL tool schemas without an explicit promotion.
preload_mode="${BEAGLELATHE_HOOK_PRELOAD:-on}"

if [ "${preload_mode}" = "off" ]; then
  cat <<EOF
[BeagleLathe MCP is active. preload=off (A3 experiment)] The \`${routing_prefix}lathe\` tool beats the built-ins for this session. On EVERY tool call, prefer it. Shape:

  ${routing_prefix}lathe({"action": <action>, "args": {...}})

Actions and their args (one tool, four actions):

  action="search"     args={pattern, path_glob?, max_results?, context_lines?}    ←  Grep/Glob   (glob+regex+ranked snippet in one call)
  action="read"       args={path, mode?}                                          ←  Read        (AST-truncates files >300 lines; mode="full" for verbatim)
  action="edit"       args={edits:[{path, old_string, new_string}], include_diffs?}  ←  Edit     (atomic multi-file; fuzzy whitespace/quote-drift)
  action="edit_glob"  args={glob, pattern, replacement}                           ←  sed -i      (atomic regex substitution across a glob)

When making bulk edits across files (renames, find-and-replace), do NOT use \`Bash\` with sed/perl/awk/tr — call \`${routing_prefix}lathe\` with action="edit_glob" instead: glob + regex + replacement, committed across all files atomically (all-or-nothing on failure). For per-file edits that don't share a regex, batch them via action="edit".
EOF
else
  cat <<EOF
[BeagleLathe MCP is active.] The \`${routing_prefix}lathe\` tool beats the built-ins for this session. Preload it once on your first turn (this is required — without it the model defaults to Read/Edit/Grep habits and BL's wins are lost):

  ToolSearch select:${select_line}

Then on EVERY subsequent tool call, prefer it. Shape:

  ${routing_prefix}lathe({"action": <action>, "args": {...}})

Actions and their args (one tool, four actions):

  action="search"     args={pattern, path_glob?, max_results?, context_lines?}    ←  Grep/Glob   (glob+regex+ranked snippet in one call)
  action="read"       args={path, mode?}                                          ←  Read        (AST-truncates files >300 lines; mode="full" for verbatim)
  action="edit"       args={edits:[{path, old_string, new_string}], include_diffs?}  ←  Edit     (atomic multi-file; fuzzy whitespace/quote-drift)
  action="edit_glob"  args={glob, pattern, replacement}                           ←  sed -i      (atomic regex substitution across a glob)

When making bulk edits across files (renames, find-and-replace), do NOT use \`Bash\` with sed/perl/awk/tr — call \`${routing_prefix}lathe\` with action="edit_glob" instead: glob + regex + replacement, committed across all files atomically (all-or-nothing on failure). For per-file edits that don't share a regex, batch them via action="edit".
EOF
fi
