# Changelog

## 0.2.5 — 2026-05-16

### Fixed

- **SessionStart hook now loads the real MCP tool schemas.** The bundled `scripts/session-start.sh` told the model to preload `mcp__plugin_beagle_beaglelathe__{search,read,edit,sh}` — a prefix left over from before the plugin/server were renamed to `lathe`. Claude Code actually registers the tools as `mcp__plugin_lathe_lathe__*`, so the first `ToolSearch` returned no matches and every session silently fell back to the built-in Read/Edit/Grep/Bash, also under-counting the local savings meter. Hook updated to the correct prefix; added `tests/test_session_start_hook.py` which parses `plugin.json`, derives the expected prefix, and asserts the hook matches — fails CI on the next rename that forgets to update both files.

## 0.2.4 — 2026-05-17

### Fixed

- **MCP server now starts under `pipx`, `uv tool install`, and unactivated venvs.** Previously the bundled plugin manifest invoked the MCP server as `python -m beaglelathe`, which assumed the package was importable from whichever `python` was on PATH. That assumption holds for system pip installs but fails when the package lives in an isolated venv (the default for `pipx` and `uv tool install`). Two changes:
  - The bundled `.claude-plugin/plugin.json` now calls the `beaglelathe` console script directly (`beaglelathe serve`). Pipx, uv, and activated venvs all put this script on PATH.
  - `beaglelathe install` rewrites the local copy of `plugin.json` at install time with the absolute path to `sys.executable`, so the MCP server starts regardless of PATH state. Same fix in the `beaglelathe install --mcp-json` writer.
- **Plugin install now refreshes Claude's cache on upgrade.** When `beaglelathe install` runs after an upgrade (detected via the version stamp at `~/.beaglelathe/plugin/.version`), it re-invokes `claude plugin install lathe@beaglelathe` to pick up the updated plugin files, even when the plugin is already registered.

## 0.2.3 — 2026-05-15

### Changed

- **New canonical install flow.** `pip install beaglelathe` followed by `beaglelathe login` is now the recommended path — `login` registers the plugin with Claude Code (MCP server, slash commands, agents, hooks) before walking through the magic-link flow. Previously the docs pointed users at `claude plugin marketplace add BeagleLathe/lathe` first, but `claude plugin install` runs no install scripts, so the MCP server failed to start until the pip package was also installed. The marketplace path still works and is documented as a power-user alternative.
- **`beaglelathe install` now installs the plugin** (idempotent: skips if `lathe@beaglelathe` is already registered). The previous behavior — writing a per-project `.mcp.json` — lives behind `beaglelathe install --mcp-json [PROJECT]`.
- **Plugin files ship inside the wheel** (`.claude-plugin/`, `agents/`, `commands/`, `hooks/`, the public `scripts/`). At install time they're copied to `~/.beaglelathe/plugin/` so the marketplace reference stays valid across venv switches and Python upgrades.

## 0.2.2 — 2026-05-15

### Added

- **Offline grace-period timer.** Tools remain available for 24 hours without a backend connection. After the grace window expires, calls return a structured `offline_too_long` error until the connection is restored — either by running `beaglelathe login` or waiting for network access to return. The window resets automatically on the next successful sync. Configurable via `BEAGLELATHE_OFFLINE_GRACE_HOURS`.

## 0.2.1 — 2026-05-10

### Added

- **`extract_todos`** — Scan the codebase for `TODO` / `FIXME` / `XXX` / `HACK` markers (or any custom list) and return them structured: marker, file:line, comment text, surrounding context, and (in git repos) author + commit sha + age in days from blame. Sortable by age (oldest first) or by file.
- **`commit_message`** — Generate a conventional-commit draft (`type(scope): subject`) from staged / working-tree / last-commit changes. Heuristic-only, no LLM call: paths drive type (tests → `test`, `.md` → `docs`, `.github/` → `ci`, lock/manifest files → `build`, pure additions → `feat`, etc.), longest common directory drives scope, basenames drive subject. Accepts `type_override` and `scope_override`.

## 0.2.0 — 2026-05-10

Five new structured tools that collapse the most expensive remaining workflows in a Claude Code session into one round-trip each.

### Added

- **`run_tests`** — Auto-detects pytest / jest / vitest / cargo test / go test from files in cwd. Returns parsed `summary{total, passed, failed, skipped, errors}` plus per-failure `{name, file, line, message, source_excerpt, traceback_tail}`. Accepts `command`, `path`, `pattern`, `max_failures`.
- **`git_status`** — Parses `git status --porcelain=v2 --branch` into `{branch, upstream, ahead, behind, staged, unstaged, untracked, renamed, conflicts}`. Reports detached HEAD with `{detached: true, head_sha}`. Returns `{error: "not a git repository"}` outside a repo.
- **`git_diff`** — Structured unified diff with `{old_start, old_lines, new_start, new_lines, header, content}` hunks per file. Recognizes `added | modified | deleted | renamed | copied | type_changed | binary` statuses. Caps via `max_files` / `max_hunks_per_file`.
- **`changed_files`** — Auto-detects base branch (origin/HEAD → main → master → develop) and returns per-path `{additions, deletions, status}` plus totals. Optionally folds in uncommitted working-tree changes including untracked files.
- **`lint_and_typecheck`** — Dispatches by file extension: `.py` → ruff + mypy; `.ts/.tsx` → tsc + eslint; `.js/.jsx` → eslint; `.go` → go vet + golangci-lint; `.rs` → cargo check + clippy. Files grouped by extension so each toolchain runs once. Missing runners are reported in `skipped_runners` rather than failing the call. Per-runner 60s timeout, 180s total wall-clock cap.

### Notes

- All tools are best-effort: missing toolchains (jest, golangci-lint, cargo, etc.) are reported in `skipped_runners` rather than failing the call.
- No file contents, tool arguments, or output are ever sent to the backend — only a call count.

## 0.0.1

Initial release. Four tools: `search`, `edit`, `read`, `sh`.
