# BeagleLathe

Token-efficient MCP server for Claude Code. Eleven tools that collapse the sequences Claude Code runs most often — search, read, edit, test runs, git inspection, lint/typecheck, TODO scans, and commit-message drafts — from 3–5 round-trips down to 1.

Free tier: 200 tool calls/month. Pro: unlimited.

---

## What it does

| Tool | Replaces | Savings |
|------|----------|---------|
| `search` | glob → grep → read-to-confirm | ~3 calls → 1 |
| `edit` | read → edit → verify-read | ~3 calls → 1 |
| `read` | cat + head/tail on large files | ~2 calls → 1 |
| `sh` | Bash for read-only inspection | structured output, fewer re-reads |
| `run_tests` | Bash(pytest) → grep → read → grep → read | ~5 calls → 1 |
| `git_status` | git status + manual parsing | structured branch/staged/untracked |
| `git_diff` | git diff + hunk parsing | structured hunks per file |
| `changed_files` | git diff --numstat + status parsing | one call vs branch |
| `lint_and_typecheck` | run each linter via Bash, grep output | one call across languages |
| `extract_todos` | grep + per-line `git blame` | one call, sorted by age |
| `commit_message` | hand-write conventional commit | heuristic draft from staged diff |

Every session, BeagleLathe counts what it saved. Run `/lathe-savings` to see the numbers.

---

## Install

**Prerequisites:** Python 3.10+.

First install the Python package from PyPI:

```bash
pip install beaglelathe
```

Then add the plugin from your shell:

```bash
claude plugin marketplace add BeagleLathe/lathe
claude plugin install lathe@beaglelathe
```

Or, if you're already inside a Claude Code session, use the slash-command equivalents:

```
/plugin marketplace add BeagleLathe/lathe
/plugin install lathe@beaglelathe
```

---

## Verify

Open Claude Code in your project and run:

```
/mcp
```

You should see `beaglelathe` listed with eleven tools: `search`, `edit`, `read`, `sh`, `run_tests`, `git_status`, `git_diff`, `changed_files`, `lint_and_typecheck`, `extract_todos`, `commit_message`. If you have the plugin installed, a `lathe:code` badge appears in the sidebar.

---

## Sign in (optional, free)

BeagleLathe tools work without an account. Sign in to track usage against your free tier:

```
beaglelathe login
```

This opens a magic-link URL in your browser. Click it, confirm, and credentials are saved to `~/.beaglelathe/credentials.json`.

After signing in:

```
beaglelathe status
```

```
plan:       Free tier
calls used: 0 this month
remaining:  200 calls
resets:     2026-06-01T00:00:00+00:00
account:    you@example.com
```

---

## CLI reference

```
beaglelathe login        Sign in via magic link
beaglelathe logout       Clear stored credentials
beaglelathe whoami       Print signed-in user
beaglelathe status       Show plan and remaining calls
beaglelathe savings      Show local savings summary
beaglelathe upgrade      Open Stripe checkout (Free → Pro)
beaglelathe serve        Start the MCP server (what Claude Code runs automatically)
```

All commands accept `--help`.

---

## Slash commands (inside Claude Code)

```
/lathe             List all commands
/lathe-login       Run the login flow
/lathe-logout      Clear credentials
/lathe-status      View plan and calls remaining
/lathe-savings     View local savings counter
/lathe-upgrade     Upgrade to Pro
/lathe-update      Update to latest version
/lathe-help        Troubleshooting and quick reference
```

---

## Statusline integration

BeagleLathe ships a Claude Code statusLine component that shows your current plan, calls remaining, and locally-tracked savings count on every prompt. To enable, add this to `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "$CLAUDE_PLUGIN_ROOT/scripts/statusline.sh"
}
```

Or call the CLI directly:

```json
"statusLine": {
  "type": "command",
  "command": "beaglelathe statusline"
}
```

Output looks like `Lathe · Free · 158 left · saved 42`. Reads only from `~/.beaglelathe/credentials.json` and `~/.beaglelathe/state.db`; never makes a network call.

---

## Agents

Three agent definitions ship in `agents/`:

- **lathe-code** (default, Sonnet) — full tool access, instructs the model to use BeagleLathe tools first.
- **lathe-explore** (Haiku) — read-only search and inspection, returns compressed summaries.
- **lathe-plan** (Haiku) — reads the codebase, returns a concrete step-by-step implementation plan.

---

## Tool reference

### `search`

```
pattern        Regex (ripgrep syntax). Required.
path_glob      Glob to limit scope. Default: **/*
max_results    Cap on total matches. Default: 50
context_lines  Lines before/after each match. Default: 2
case_sensitive Default: false
```

Returns matches grouped by file with surrounding context. Always prefer this over separate file-discovery + content searches.

### `edit`

```
edits    Array of {path, old_string, new_string, replace_all?}. Required.
validate Run syntax validation after writing. Default: true
```

Matching cascade: exact → normalized (whitespace/smart-quote fold) → fuzzy (rapidfuzz ≥ 90). All-or-nothing: if any edit fails the dry run, nothing is written.

### `read`

```
path        File path. Required.
line_range  [start, end], 1-indexed inclusive. Optional.
full        Skip AST truncation. Default: false
```

AST-aware truncation keeps top-level signatures and class skeletons, collapses function bodies ≥ 3 lines. `line_range` expands any body intersecting that range. Hard cap: 50,000 chars.

### `sh`

```
command    A single read-only shell command.
```

Allowed commands: `ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `which`, `file`, `stat`, `du`, `df`, `find`, `tree`, `echo`, `git`. For git: only `log`, `status`, `diff`, `show`, `blame`, `branch`, `remote`, `config`, `rev-parse`, `ls-files`, `describe`. No pipes, redirects, or subshells.

### `run_tests`

```
command       Override auto-detected test command. Optional.
path          Restrict tests to this path. Optional.
pattern       Test name filter (-k for pytest). Optional.
max_failures  Cap on failure entries returned. Default: 20.
```

Auto-detects pytest / jest / vitest / cargo test / go test from files in cwd. Output: `framework`, `command`, `duration_ms`, `summary{total, passed, failed, skipped, errors}`, `failures[{name, file, line, message, source_excerpt, traceback_tail}]`, `truncated_failures`. Collection / import errors land in `error_output`.

### `git_status`

```
include_untracked  Default: true.
```

Returns `{branch, upstream, ahead, behind, staged, unstaged, untracked, renamed, conflicts}`. Detached HEAD adds `{detached: true, head_sha}`. Returns `{error: "not a git repository"}` outside a repo.

### `git_diff`

```
path                Limit diff to this path. Optional.
from_ref, to_ref    Compare specific refs. Optional.
staged              Diff staged changes only. Default: false.
max_files           Default: 50.
max_hunks_per_file  Default: 20.
```

Returns `{files: [{path, status, additions, deletions, hunks: [{old_start, old_lines, new_start, new_lines, header, content}]}], truncated_files, truncated_hunks}`. Statuses: `added | modified | deleted | renamed | copied | type_changed`. Binary files get `binary: true` with no hunks. Unknown refs return `{error: "unknown ref: ..."}`.

### `changed_files`

```
branch               Compare against this branch. Default: auto-detected main/master/develop.
include_uncommitted  Fold in working-tree changes. Default: true.
```

Returns `{branch_compared, files: [{path, additions, deletions, status}], totals{files, additions, deletions}}`. Untracked files appear with `status: "added"`.

### `lint_and_typecheck`

```
paths       Files or directories to check. Default: ["."].
level       "syntax" or "full". Default: "full".
max_issues  Cap on issues returned. Default: 100.
```

Dispatches by extension: `.py` → ruff + mypy (or py_compile in syntax mode); `.ts/.tsx` → tsc + eslint; `.js/.jsx` → eslint; `.go` → go vet + golangci-lint; `.rs` → cargo check + clippy. Runners that aren't installed are listed in `skipped_runners`. Output: `{level, summary{errors, warnings, by_file}, issues[{file, line, column, severity, code, source, message}], ran_runners, skipped_runners, truncated}`.

### `extract_todos`

```
path_glob       Glob to limit scope. Default: **/*
markers         List of markers. Default: ["TODO", "FIXME", "XXX", "HACK"]
include_blame   Attach git blame author + age in days. Default: true
context_lines   Lines around each match. Default: 1
sort_by         "age" or "file". Default: age (oldest first)
max_results     Default: 100
```

Returns `{todos: [{marker, file, line, text, context, author?, commit?, age_days?}], total, by_marker, truncated}`. Without git (or for untracked files), blame fields are omitted.

### `commit_message`

```
source              "staged" | "working_tree" | "last_commit". Default: staged
type_override       Force a conventional-commit type. Optional.
scope_override      Force a scope. Optional.
max_subject_length  Cap on subject line. Default: 60.
```

Returns `{type, scope, subject, message, source, files}`. Heuristics: test paths → `test`, docs/`*.md` → `docs`, `.github/` → `ci`, `pyproject.toml`/`package.json`/etc. → `build`, all-additions in source → `feat`, all-deletions → `refactor`, otherwise `fix`. Scope is the longest common directory after stripping `src/`/`lib/`/`app/`/etc. Intended as a starting draft the agent can polish.

---

## Troubleshooting

**`ripgrep not found`**
BeagleLathe ships vendored ripgrep binaries for all supported platforms. If you hit this on an unusual platform, install ripgrep manually (`brew install ripgrep` on macOS, `apt install ripgrep` on Linux) and restart Claude Code.

**Tools not showing in `/mcp`**
Check that `python -m beaglelathe` starts without errors in your terminal. Common causes: wrong Python in PATH, missing pip install, bad `.mcp.json` path.

**Auth failures / "session expired"**
Run `beaglelathe login` to get a fresh token. Tokens expire after 30 days of inactivity.

**Quota exceeded**
Run `/lathe-upgrade` or `beaglelathe upgrade` to open Stripe checkout. Pro is unlimited.

**`edit` reports "old_string not found"**
The string you provided doesn't match what's in the file. Read the current file content first with `read`, then retry with the exact text.

**`edit` reports "old_string not unique"**
Add more surrounding lines to make the match unique, or set `replace_all: true` if you want all occurrences replaced.

**First tool call is slow**
The MCP server starts on first use. ~3 seconds is normal. Subsequent calls in the same session are fast.

---

## FAQ

**Do you store my code?**
No. `search` and `edit` run entirely on your machine. The auth backend only receives a call count (an integer) on each sync — never file contents, never diffs, never code.

**Does it work offline?**
Yes. All four tools work without a network connection. The usage sync is best-effort; if the backend is unreachable the tool still succeeds.

**What's the free tier exactly?**
200 tool calls per month, per account. A "call" is one invocation of any BeagleLathe tool (search, edit, read, or sh). Resets on the 1st of each month.

**Can I use it without signing in?**
Yes. The tools work anonymously. Without an account, savings are tracked locally but not synced, and there is no usage quota enforced.

**Python version?**
3.10 or higher.
