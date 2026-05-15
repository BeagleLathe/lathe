"""LanguageHandler base class.

Subclasses override `truncate` (and optionally `symbols`/`imports`) when a
language needs imperative logic. The default implementation delegates back to
the generic engine in truncate.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..language_config import LanguageConfig


class LanguageHandler:
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
