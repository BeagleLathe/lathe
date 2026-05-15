# Changelog

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
