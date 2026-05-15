"""Entry point for `python -m beaglelathe`.

No args (or `serve`)  → run the MCP server over stdio.
`login` / `logout`    → magic-link sign-in / clear stored credentials.
`whoami` / `status`   → show signed-in user / plan + budget from backend.
`savings`             → show local savings counter.
`statusline`          → one-line summary for Claude Code's statusLine.
`upgrade`             → open Stripe checkout to upgrade plan.
`install`             → write .mcp.json into a project directory.
"""

from __future__ import annotations

import sys

from .auth.cli import build_parser
from .server import cli as serve_cli


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command or args.command == "serve":
        serve_cli()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


# Kept so an installed `beaglelathe` console script (per pyproject) still works.
def cli() -> None:
    sys.exit(main())
