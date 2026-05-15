"""TSX handler: dispatch only.

The .tsx files must be parsed with the `tsx` grammar (not `typescript`).
The registry maps .tsx -> the `tsx` LanguageConfig already, so this handler
is a thin pass-through that exists to make the dispatch site explicit and to
give us a place to add JSX-specific logic later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ._base import LanguageHandler

if TYPE_CHECKING:
    from ..language_config import LanguageConfig


class TSXHandler(LanguageHandler):
    @classmethod
    def truncate(
        cls,
        source: bytes,
        config: "LanguageConfig",
        *,
        keep_symbol: Optional[str] = None,
    ) -> bytes:
        from ..truncate import _generic_truncate

        return _generic_truncate(source, config, keep_symbol)
