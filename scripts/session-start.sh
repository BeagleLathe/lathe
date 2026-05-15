#!/usr/bin/env bash
# SessionStart hook for the BeagleLathe plugin.
#
# Claude Code defers MCP tool schemas — by default only the *names* of
# `mcp__plugin_beagle_beaglelathe__{search,read,edit,sh}` reach the model,
# not their descriptions. Without descriptions the model can't tell that
# these tools beat the built-in Read/Edit/Grep/Bash, so it never reaches
# for them. This hook injects the routing rules + a schema-preload nudge
# into the model's context at session start.
#
# Plain stdout text from a SessionStart hook is auto-wrapped as additional
# context by Claude Code (10KB limit). No JSON envelope required.

cat <<'EOF'
[BeagleLathe plugin is enabled.] This plugin registers four MCP tools that
collapse multi-step file inspection / editing into single calls. Their schemas
are deferred — you must call ToolSearch once before invoking them. Do this
proactively on your first turn that involves any file search, read, edit, or
read-only shell command:

  ToolSearch query: "select:mcp__plugin_beagle_beaglelathe__search,mcp__plugin_beagle_beaglelathe__read,mcp__plugin_beagle_beaglelathe__edit,mcp__plugin_beagle_beaglelathe__sh"

Routing rules (prefer the BeagleLathe tool over the built-in in each case):

  mcp__plugin_beagle_beaglelathe__search   over Grep/Glob — fuses glob + regex + ranked
                                            snippet read into one call; usually removes the
                                            follow-up Read entirely.

  mcp__plugin_beagle_beaglelathe__read     over Read — for source files >300 lines, returns
                                            an AST-truncated view (signatures + types kept,
                                            bodies stubbed). Use mode=full only when you need
                                            every line verbatim.

  mcp__plugin_beagle_beaglelathe__edit     over Edit — even for single edits. Batches multi-
                                            file changes atomically and fuzzy-matches over
                                            whitespace / quote drift that built-in Edit would
                                            reject.

  mcp__plugin_beagle_beaglelathe__sh       over Bash — for read-only inspection (ls, cat,
                                            head, tail, find, wc, which, file, stat, du, df,
                                            tree, and git log/status/diff/show/blame/branch/
                                            remote/config/rev-parse/ls-files/describe). Bash
                                            is still the right tool for writes, installs,
                                            pipes, and anything not on that allowlist.
EOF
