# BeagleLathe Features

BeagleLathe is a Claude Code plugin that exposes an MCP server (`beaglelathe`) with eleven structured tools. Each tool collapses a 2–5 step vanilla Claude Code cycle (`Bash` → parse → `Read` → `Grep`) into a single round-trip that returns parsed JSON.

Server entry: [src/beaglelathe/server.py](src/beaglelathe/server.py). Tool registration is in [server.py:75-90](src/beaglelathe/server.py#L75-L90). Tool implementations live under [src/beaglelathe/tools/](src/beaglelathe/tools/).

---

## Tool catalog

| Tool | Replaces | Module |
|---|---|---|
| [`search`](#search) | `Glob` + `Grep` + `Read` cycle | [tools/search.py](src/beaglelathe/tools/search.py) |
| [`read`](#read) | Verbatim `Read` of source files | [tools/read.py](src/beaglelathe/tools/read.py) |
| [`edit`](#edit) | One-by-one `Edit` calls | [tools/edit.py](src/beaglelathe/tools/edit.py) |
| [`sh`](#sh) | `Bash` for read-only inspection | [tools/sh.py](src/beaglelathe/tools/sh.py) |
| [`run_tests`](#run_tests) | `Bash(pytest …)` + raw-output grep | [tools/run_tests.py](src/beaglelathe/tools/run_tests.py) |
| [`git_status`](#git_status) | `git status` + manual parse | [tools/git_ops.py](src/beaglelathe/tools/git_ops.py) |
| [`git_diff`](#git_diff) | `git diff` + hunk parse | [tools/git_ops.py](src/beaglelathe/tools/git_ops.py) |
| [`changed_files`](#changed_files) | `git diff --numstat` + status parse | [tools/git_ops.py](src/beaglelathe/tools/git_ops.py) |
| [`lint_and_typecheck`](#lint_and_typecheck) | Per-language linter invocations | [tools/lint_and_typecheck.py](src/beaglelathe/tools/lint_and_typecheck.py) |
| [`extract_todos`](#extract_todos) | `grep TODO` + per-line `git blame` | [tools/extract_todos.py](src/beaglelathe/tools/extract_todos.py) |
| [`commit_message`](#commit_message) | Manual diff-reading commit drafts | [tools/commit_message.py](src/beaglelathe/tools/commit_message.py) |

---

## `search`

Fuses glob filtering, regex pattern matching, and ranked snippet reading into a single ripgrep-backed call. Replaces the `Glob` + `Grep` + `Read` sequence with one round-trip.

**Inputs:** `pattern` (required, ripgrep regex syntax), `path_glob` (default `**/*`), `max_results` (default 50), `context_lines` (default 2), `case_sensitive` (default false).

**Output:** Results grouped per file, each with line number, matched line, configurable surrounding context, and a `count` field that deduplicates identical hits within the same file. Top-level `files_searched`, `files_matched`, `total_matches`, `truncated`.

**When to prefer:** Whenever the next step would be reading one or more matched files — `search` returns enough context that the follow-up Read is usually unnecessary. Use over `Grep` for any search across more than a couple of files.

---

## `read`

Reads a source file with substantial token savings by stubbing function/method bodies via tree-sitter while keeping every signature, type, import, and module-level declaration intact.

**Modes:**
- `truncated` (default) — AST-aware via tree-sitter. Bodies stubbed, structural info kept.
- `full` — file verbatim. Use when you genuinely need every line.
- `skeleton` — regex-picked declaration lines only. Cheapest fallback for unsupported languages or files >10k lines.

**Inputs:** `path` (required), `mode` (default `truncated`), `symbol` (optional). When `symbol='ClassName.method'` is set with `mode=truncated`, that one symbol keeps its body in full while the rest are stubbed — ideal for zeroing in on a specific function in a large file.

**Output:** `path`, `language`, `mode`, `lines`, `truncated_to_lines`, `content`, `size_capped`, and a `symbols` array (kind, byte/line spans, parent) extracted via tree-sitter.

**Hard caps:** Output capped at 200,000 characters (head + tail with elision marker).

**Graceful degradation:** If `truncated` mode hits a syntax error or tree-sitter fails, falls back to `full` with a `note` field.

**Workspace sandbox:** Refuses paths outside the working directory.

---

## `edit`

Applies an array of edits across one or more files in a single atomic call. Even one-element arrays benefit from the same atomic guarantee and fuzzy matching.

**Inputs:** `edits` (array of `{path, old_string, new_string, replace_all?}`, min 1), `validate` (default true).

**Match cascade** ([tools/edit.py:67-117](src/beaglelathe/tools/edit.py#L67-L117)):
1. **Exact** — direct substring match.
2. **Normalized** — whitespace, smart-quote (`""`), em-dash, ellipsis variants folded.
3. **Fuzzy** — rapidfuzz with score threshold 90.

If any edit is ambiguous or unmatched, the entire batch is rejected before any write.

**Atomic guarantee:** Two-phase commit.
1. Plan all edits against in-memory file state.
2. Re-verify SHA-256 of every touched file (detects concurrent modification).
3. Write each file via `tempfile + os.replace` to its parent dir.

The workspace is never left half-modified.

**Validation:** When `validate: true` (default), each touched file is parsed by the appropriate validator after write. Result includes `validation: ok | skipped | syntax_error`. Warn-only — syntax errors do not revert the write.

**Output:** `applied`, `failed`, and `results[]` with per-edit `status`, `match_type` (`exact` | `normalized` | `fuzzy`), `fuzzy_score`, unified `diff`.

---

## `sh`

Runs a single read-only shell command. Strict allowlist; no pipes, redirects, subshells, or variable interpolation.

**Allowed commands** ([tools/sh.py:31-39](src/beaglelathe/tools/sh.py#L31-L39)): `ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `which`, `file`, `stat`, `du`, `df`, `find`, `tree`, `echo`, `git`.

**Allowed `git` subcommands:** `log`, `status`, `diff`, `show`, `blame`, `branch`, `remote`, `config`, `rev-parse`, `ls-files`, `describe`.

**Rejected metacharacters:** `<`, `>`, `|`, `&`, `;`, backticks, `$(`, `${`, `$VAR`.

**Caps:** 500 lines / 50KB stdout, 5KB stderr, 30s timeout.

**When to prefer:** Read-only inspection. For writes, installs, pipes, or anything off the allowlist, use the built-in `Bash`.

---

## `run_tests`

Detects the test framework, runs the suite, and parses the output into a uniform shape.

**Auto-detects** ([tools/run_tests.py:97-127](src/beaglelathe/tools/run_tests.py#L97-L127)):
- **pytest** — `pyproject.toml` with `[tool.pytest`, `pytest.ini`, `tests/test_*.py`, or `test_*.py`.
- **vitest / jest** — `package.json` `scripts.test` references, `vitest.config.*`, `jest.config.*`.
- **cargo** — `Cargo.toml`.
- **go** — `go.mod`.

**Inputs:** `command` (override auto-detection), `path` (restrict to file/directory), `pattern` (passed as `-k` / `--testNamePattern` / `-run`), `max_failures` (default 20).

**Output:** Uniform across frameworks:
```
{
  framework, command, duration_ms,
  summary: {total, passed, failed, skipped, errors},
  failures: [{name, file, line, message, source_excerpt, traceback_tail}],
  truncated_failures
}
```

**Per-failure source excerpts:** When `file` and `line` are extracted from the output (pytest tracebacks via `_PYTEST_TRACE_LOC_RE`), the tool reads ±2 lines around the failing line and includes them inline — no follow-up `Read` needed.

**Explicit command path:** When `command` is supplied, output is sniffed for known framework signatures (pytest section headers, jest `testResults` JSON, cargo JSON events, go test JSON) and parsed accordingly. Falls back to raw stdout/stderr with `framework: "unknown"` if no parser matches.

**Caps:** 300s total wall clock, 8KB stdout / 4KB stderr fallback truncation.

---

## `git_status`

Returns the structured state of the working tree.

**Inputs:** `include_untracked` (default true).

**Output:**
```
{
  branch, upstream, ahead, behind,
  staged: [paths], unstaged: [paths], untracked: [paths],
  renamed: [{from, to}], conflicts: [paths],
  detached?: true, head_sha?: "..."
}
```

Built on `git status --porcelain=v2 --branch`. Detached HEAD is reported with `detached: true` and `head_sha`.

**Refuses** if not inside a git repository.

---

## `git_diff`

Returns a structured unified diff: per-file additions/deletions and a hunk list with `old_start`, `old_lines`, `new_start`, `new_lines`, header, and raw content.

**Inputs:** `path` (limit to a path), `from_ref`, `to_ref`, `staged` (default false), `max_hunks_per_file` (default 20), `max_files` (default 50).

**Handles statuses:** `added`, `modified`, `deleted`, `renamed`, `copied`, `type_changed`, plus `binary: true` for binary files.

**Output:**
```
{
  files: [{path, status, additions, deletions, hunks: [{old_start, ...}], from?, to?, binary?}],
  truncated_files, truncated_hunks
}
```

**Error mapping:** Unknown refs are surfaced as `error: "unknown ref: <ref>"` rather than raw git stderr.

---

## `changed_files`

Lists files changed on the current branch vs a base branch, with per-file additions, deletions, and status. Optionally folds in uncommitted working-tree changes.

**Inputs:** `branch` (default: auto-detect via `origin/HEAD`, then `main`, `master`, `develop`), `include_uncommitted` (default true).

**Output:**
```
{
  branch_compared,
  files: [{path, additions, deletions, status}],
  totals: {files, additions, deletions}
}
```

**Untracked file handling:** When `include_uncommitted: true`, untracked files (which don't appear in `git diff HEAD`) are surfaced via `git ls-files --others --exclude-standard` and their line count is included as additions.

---

## `lint_and_typecheck`

Runs linters and typecheckers across the given paths and returns issues in a uniform structured shape.

**Inputs:** `paths` (default `["."]`), `level` (`syntax` or `full`, default `full`), `max_issues` (default 100).

**Dispatch by extension:**
- **Python (`.py`)** → `ruff` + `mypy` (or `py_compile` in `syntax` mode).
- **TypeScript (`.ts`, `.tsx`)** → `tsc --noEmit` + `eslint`.
- **JavaScript (`.js`, `.jsx`)** → `eslint`.
- **Go (`.go`)** → `go vet` + `golangci-lint`.
- **Rust (`.rs`)** → `cargo check` + `cargo clippy`.

**Runner robustness:**
- Missing toolchains are reported in `skipped_runners` rather than failing.
- Per-runner 60s timeout, 180s total wall-clock budget. Runners hitting their timeout land in `errored_runners` with a stderr tail.
- Each language path is independent — a missing `mypy` never blocks `tsc` or `cargo clippy`.

**Output:** Sorted by `(file, line, column)`. Each issue carries `severity` (`error` | `warning`), `code`, `source` (the runner name), `message`.

**Walk skips:** `.git`, `.venv`, `node_modules`, `target`, `__pycache__`, `dist`, `build`, and any hidden directory.

---

## `extract_todos`

Scans the codebase for TODO/FIXME/XXX/HACK comments and returns them structured with `git blame` metadata.

**Inputs:** `path_glob` (default `**/*`), `markers` (default `["TODO", "FIXME", "XXX", "HACK"]`), `include_blame` (default true), `context_lines` (default 1), `sort_by` (`age` or `file`, default `age`), `max_results` (default 100).

**Output:**
```
{
  todos: [{marker, file, line, text, context?, author?, commit?, age_days?}],
  total, by_marker: {TODO: n, FIXME: n, ...}, truncated
}
```

**Blame integration:** Uses `git blame --line-porcelain` once per file, parsed for `author`, `author-mail`, `author-time`. Skipped if more than 200 files have matches (avoids thrashing).

**Default sort:** Oldest age first; entries without blame data sort last.

---

## `commit_message`

Generates a conventional-commit-style draft from a git diff. **Heuristic-only, no LLM call** — meant as a starting point for the agent to polish.

**Inputs:** `source` (`staged` | `working_tree` | `last_commit`, default `staged`), `type_override`, `scope_override`, `max_subject_length` (default 60).

**Heuristics:**
- **Type inference** — path-pattern based. All-tests → `test`, all-docs → `docs`, all-`.github`/workflows → `ci`, all-`pyproject.toml`/`package.json`/`Makefile`/`Dockerfile`/etc. → `build`. Pure additions in source → `feat`. Pure deletions → `refactor`. Mixed → `fix`.
- **Scope inference** — longest common directory prefix, after stripping noise roots (`src`, `lib`, `app`, `packages`, `internal`, `cmd`).
- **Subject** — verb chosen from type (`add`, `fix`, `refactor`, `update tests for`, …) + the three most common file basenames, trimmed to fit the budget.

**Output:**
```
{
  type, scope?, subject,
  message: "<type>(<scope>): <subject>",
  source, files: [{path, status}]
}
```

---

## Cross-cutting features

### Local savings tracking
Every tool call increments a local SQLite counter via `record_call()` in [src/beaglelathe/savings.py](src/beaglelathe/savings.py). Surfaced by the `/lathe-savings` slash command — calls saved, tokens saved, and estimated cost saved vs vanilla Claude Code.

### Background usage sync
After each tool call, `_sync_usage_bg` ([src/beaglelathe/server.py](src/beaglelathe/server.py)) fires a daemon thread that posts to the BeagleLathe backend with the credentialed JWT (if present), persists any refreshed JWT, and updates an in-process quota flag. Never blocks the tool response.

### First-call welcome
On the first tool call in a session without stored credentials, the result is prefixed with a `_notice` field directing the user to `/lathe-login` for the free-tier activation.

### Hard quota gate
When a backend sync confirms `budget_remaining == 0`, subsequent calls short-circuit to `{error: "quota_exceeded", upgrade_url, action}` without executing the tool body.

### SessionStart routing nudge
[hooks/hooks.json](hooks/hooks.json) registers a `SessionStart` hook ([scripts/session-start.sh](scripts/session-start.sh)) that injects routing rules + a `ToolSearch select:…` preload instruction into context at session start. Compensates for Claude Code's MCP tool deferral, which would otherwise hide tool descriptions from the model.

### Slash commands
Under [commands/](commands/): `/lathe`, `/lathe-login`, `/lathe-logout`, `/lathe-status`, `/lathe-savings`, `/lathe-upgrade`, `/lathe-update`, `/lathe-help`.

### Subagents
Under [agents/](agents/): `lathe-code`, `lathe-explore`, `lathe-plan` — pre-configured agents whose tool budgets steer toward BeagleLathe tools.

---

## V2 / Future

- **Multi-file `read`** — accept `paths: string[]` in addition to the current single `path: string`, execute reads in parallel, and return results as an array. Eliminates the N sequential `read` calls Claude issues when surveying several files at once (e.g. reading all changed files after a `changed_files` call).
