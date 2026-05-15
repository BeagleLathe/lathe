"""Per-language handlers for grammars whose quirks can't be expressed in .scm files.

Currently shipped:
- TSXHandler: routes .tsx files through the tsx grammar (vs ts grammar for .ts).

Deferred until the corresponding language is wired into the registry:
- RustHandler: skip nodes inside macro_definition / macro_invocation.
- PHPHandler: ignore HTML interleave regions.
- CDispatchHandler: choose c vs cpp grammar for .h headers.
"""
