#!/usr/bin/env bash
# Claude Code statusLine integration for BeagleLathe.
#
# Claude Code invokes the command configured under `statusLine` in
# ~/.claude/settings.json on each prompt refresh, passing session JSON on
# stdin. We ignore stdin and shell out to the local `beaglelathe` CLI which
# reads credentials + the local savings DB and prints one line of state.
#
# Wire-up (drop into ~/.claude/settings.json):
#
#   "statusLine": {
#     "type": "command",
#     "command": "$CLAUDE_PLUGIN_ROOT/scripts/statusline.sh"
#   }
#
# Or, if running outside the plugin cache, point directly at this file by
# absolute path. The script never fails — if `beaglelathe` isn't installed
# it falls back to a stub so the statusline doesn't go blank.

set -u

# Discard stdin (Claude Code pipes session JSON we don't need).
cat >/dev/null 2>&1 || true

if command -v beaglelathe >/dev/null 2>&1; then
  beaglelathe statusline 2>/dev/null || echo "Lathe"
else
  echo "Lathe · CLI not installed"
fi
