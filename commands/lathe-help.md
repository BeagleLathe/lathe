---
description: Usage tips, troubleshooting, and quick reference for BeagleLathe.
---

Print the following help text to the user exactly as written:

---

BeagleLathe — quick reference

TOOLS (active in every Claude Code session after install)
  search              Fused glob + regex + ranked snippet read.
                      Replaces glob + grep + read-to-confirm in one call.
  edit                Batched, atomic, fuzzy-matching file edits across one or more files.
                      Replaces sequential Read + Edit cycles.
  read                AST-aware reader. Keeps signatures, stubs function bodies.
                      Pass symbol='ClassName.method' to keep one body in full.
  sh                  Read-only shell runner. Allowed: ls, cat, head, tail, wc, find,
                      pwd, which, file, stat, du, df, tree, echo, git (log/status/diff/
                      show/blame/branch/remote/config/rev-parse/ls-files/describe).
  run_tests           Auto-detects pytest / jest / vitest / cargo test / go test.
                      Returns parsed summary + per-failure file:line + excerpt.
  git_status          Structured working-tree status (branch, ahead/behind, staged, etc.).
  git_diff            Structured unified diff with per-file hunks.
  changed_files       Files changed vs auto-detected base branch (main/master/develop).
  lint_and_typecheck  Dispatches by extension: .py→ruff+mypy, .ts/.tsx→tsc+eslint,
                      .js/.jsx→eslint, .go→go vet+golangci-lint, .rs→cargo check+clippy.
  extract_todos       TODO/FIXME/XXX/HACK scan with optional git blame age + author.
  commit_message      Conventional-commit draft from staged / working / last-commit changes.

SLASH COMMANDS
  /lathe          List all commands.
  /lathe-login    Sign in via magic link.
  /lathe-logout   Sign out.
  /lathe-status   View plan and remaining calls.
  /lathe-savings  View local savings summary.
  /lathe-upgrade  Upgrade to Pro (unlimited calls).
  /lathe-update   Update to latest version.
  /lathe-help     This help.

TROUBLESHOOTING
  "ripgrep not found"      Vendored binaries ship for all supported platforms.
                           On an unusual platform, install manually:
                           brew install ripgrep (macOS) or apt install ripgrep (Linux/WSL).
  "not logged in"          Run /lathe-login.
  "quota exceeded"         Run /lathe-upgrade.
  "session expired"        Run beaglelathe login to get a fresh token.
  MCP tools not listed     Run /mcp — if lathe is absent, check that `beaglelathe`
                           is on PATH (`which beaglelathe`) and the plugin is
                           registered (`claude plugin list`). Re-run `beaglelathe install`
                           if needed.
  First tool call hangs    The server starts on first use. Wait 5s, then retry.

FREE TIER
  200 tool calls per month. Resets on the 1st. Run /lathe-status to check.

---

Offer to answer any follow-up questions the user has.
