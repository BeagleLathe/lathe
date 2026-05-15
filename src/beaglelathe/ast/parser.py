"""Tree-sitter parser cache.

Per-grammar Python wheels are imported lazily so unused languages don't pay
their import cost. `get_language(name)` and `get_parser(name)` are cached.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from tree_sitter import Language, Parser


def _py_lang() -> Language:
    import tree_sitter_python
    return Language(tree_sitter_python.language())


def _ts_lang() -> Language:
    import tree_sitter_typescript
    return Language(tree_sitter_typescript.language_typescript())


def _tsx_lang() -> Language:
    import tree_sitter_typescript
    return Language(tree_sitter_typescript.language_tsx())


def _js_lang() -> Language:
    import tree_sitter_javascript
    return Language(tree_sitter_javascript.language())


def _rust_lang() -> Language:
    import tree_sitter_rust
    return Language(tree_sitter_rust.language())


def _go_lang() -> Language:
    import tree_sitter_go
    return Language(tree_sitter_go.language())


def _java_lang() -> Language:
    import tree_sitter_java
    return Language(tree_sitter_java.language())


def _ruby_lang() -> Language:
    import tree_sitter_ruby
    return Language(tree_sitter_ruby.language())


def _php_lang() -> Language:
    import tree_sitter_php
    return Language(tree_sitter_php.language_php())


def _csharp_lang() -> Language:
    import tree_sitter_c_sharp
    return Language(tree_sitter_c_sharp.language())


def _c_lang() -> Language:
    import tree_sitter_c
    return Language(tree_sitter_c.language())


def _cpp_lang() -> Language:
    import tree_sitter_cpp
    return Language(tree_sitter_cpp.language())


_LOADERS: dict[str, Callable[[], Language]] = {
    "python": _py_lang,
    "typescript": _ts_lang,
    "tsx": _tsx_lang,
    "javascript": _js_lang,
    "rust": _rust_lang,
    "go": _go_lang,
    "java": _java_lang,
    "ruby": _ruby_lang,
    "php": _php_lang,
    "csharp": _csharp_lang,
    "c": _c_lang,
    "cpp": _cpp_lang,
}


@lru_cache(maxsize=32)
def get_language(name: str) -> Language:
    if name not in _LOADERS:
        raise ValueError(f"unsupported language: {name!r}")
    return _LOADERS[name]()


@lru_cache(maxsize=32)
def get_parser(name: str) -> Parser:
    return Parser(get_language(name))


def parse(source: bytes, language: str):
    """Parse source bytes with the named grammar. Returns a tree_sitter.Tree."""
    if not isinstance(source, (bytes, bytearray)):
        raise TypeError("source must be bytes")
    return get_parser(language).parse(bytes(source))
