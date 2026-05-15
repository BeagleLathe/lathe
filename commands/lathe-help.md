---
description: Usage tips, troubleshooting, and quick reference for BeagleLathe.
---

Print the following help text to the user exactly as written:

---

BeagleLathe — quick reference

TOOLS (active in every Claude Code session after install)
  search   Regex search across files. Replaces glob + grep + read in one call.
           Use instead of: "find all files containing X, then read them."
  edit     Batch file edits with fuzzy matching. Atomic — all or nothing.
           Use instead of: multiple sequential Read + Edit calls.
  read     AST-aware file reader. Collapses long files to signatures + skeletons.
           Use instead of: cat on large source files.
  sh       Read-only shell runner. Allowed: ls, cat, find, git log/status/diff, etc.
           Use instead of: Bash for inspection-only commands.

SLASH COMMANDS
  /lathe          List all commands.
  /lathe-login    Sign in via magic link.
  /lathe-status   View plan and remaining calls.
  /lathe-savings  View local savings summary.
  /lathe-upgrade  Upgrade to Pro (unlimited calls).
  /lathe-logout   Sign out.
  /lathe-update   Update to latest version.

TROUBLESHOOTING
  "ripgrep not found"      Vendored binaries ship for all supported platforms.
                           On an unusual platform, install manually:
                           brew install ripgrep (macOS) or apt install ripgrep (Linux/WSL).
  "not logged in"          Run /lathe-login.
  "quota exceeded"         Run /lathe-upgrade.
  "session expired"        Run beaglelathe login to get a fresh token.
  MCP tools not listed     Run /mcp — if lathe is absent, check .mcp.json
                           and that `python -m beaglelathe` starts without errors.
  First tool call hangs    The server starts on first use. Wait 5s, then retry.

FREE TIER
  200 tool calls per month. Resets on the 1st. Run /lathe-status to check.

---

Offer to answer any follow-up questions the user has.
