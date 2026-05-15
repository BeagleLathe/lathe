"""`edit` tool: dry-run all edits, then atomically write each file via temp+rename.

Matching cascade per edit: exact → normalized (smart-quote/dash/whitespace fold) →
fuzzy (rapidfuzz, threshold 90). If any edit fails the dry run, nothing is written.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .. import validators
from ..fuzzy import fuzzy_find, normalize

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        "validate": {
            "type": "boolean",
            "default": True,
            "description": "After writing, parse each touched file with the appropriate validator and report `validation: ok | skipped | syntax_error` per result. Warn-only — syntax errors do not revert the write.",
        },
    },
    "required": ["edits"],
}

DESCRIPTION = (
    "Apply an array of edits across one or more files in a single atomic call. Prefer this "
    "over the built-in Edit tool for any code modification, including single-edit cases — a "
    "one-item `edits` array has no overhead, gets the same atomic guarantee, and benefits "
    "from fuzzy matching that tolerates whitespace, indentation, and quote/dash drift the "
    "built-in Edit would reject. For multi-file changes the win is larger: replaces a "
    "sequence of separate Edit invocations with one batched operation that lands together or "
    "not at all. Use for single-file fixes, refactors, multi-file renames, or any set of "
    "related changes (e.g., a function rename plus its call sites) that must succeed as a "
    "unit. Each edit specifies `path`, `old_string`, `new_string`, and optional `replace_all`; "
    "matching is fuzzy when needed, tolerating whitespace drift, indentation differences, and "
    "curly-vs-straight quote/dash/ellipsis variations between the supplied `old_string` and "
    "the file. Atomic guarantee: every edit is dry-run first against current file state, and "
    "if any one fails to match or is ambiguous, no file is written — the workspace is never "
    "left half-modified."
)


def _is_binary(b: bytes) -> bool:
    return b"\x00" in b[:8192]


def _plan_one(
    content: str,
    old: str,
    new: str,
    replace_all: bool,
) -> tuple[str | None, str | None, int | None, str | None]:
    """Return (new_content, match_type, fuzzy_score, error). Exactly one of new_content/error is set."""
    if old == new:
        return None, None, None, "no-op edit"

    if old in content:
        count = content.count(old)
        if not replace_all and count > 1:
            return None, None, None, "old_string not unique"
        if replace_all:
            return content.replace(old, new), "exact", None, None
        return content.replace(old, new, 1), "exact", None, None

    norm_content, offsets = normalize(content)
    norm_old, _ = normalize(old)
    if norm_old and norm_old in norm_content:
        count = norm_content.count(norm_old)
        if not replace_all and count > 1:
            return None, None, None, "old_string not unique"

        positions: list[int] = []
        start = 0
        while True:
            pos = norm_content.find(norm_old, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + len(norm_old)
            if not replace_all:
                break

        new_content = content
        for pos in reversed(positions):
            orig_start = offsets[pos]
            orig_end_norm = pos + len(norm_old)
            orig_end = offsets[orig_end_norm] if orig_end_norm < len(offsets) else len(content)
            new_content = new_content[:orig_start] + new + new_content[orig_end:]
        return new_content, "normalized", None, None

    result = fuzzy_find(content, old, min_score=90)
    if result is None:
        return None, None, None, "old_string not found"
    if result == "ambiguous":
        return None, None, None, "ambiguous fuzzy match"
    fstart, fend, fscore = result  # type: ignore[misc]
    return content[:fstart] + new + content[fend:], "fuzzy", fscore, None


def apply_edits(edits: list[dict], cwd: Path, validate: bool = True) -> dict:
    cwd = cwd.resolve()
    if not edits:
        return {"applied": 0, "failed": 0, "results": [], "error": "no edits provided"}

    plans: list[dict] = []
    failures: list[dict] = []
    file_state: dict[str, str] = {}
    file_orig_sha: dict[str, str] = {}

    for edit in edits:
        path_str = edit.get("path", "")
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        replace_all = bool(edit.get("replace_all", False))

        try:
            p = Path(path_str)
            if not p.is_absolute():
                p = cwd / p
            try:
                p_resolved = p.resolve(strict=False)
            except (OSError, RuntimeError):
                failures.append({"path": path_str, "status": "failed", "reason": "invalid path"})
                continue

            try:
                p_resolved.relative_to(cwd)
            except ValueError:
                failures.append(
                    {"path": path_str, "status": "failed", "reason": "path outside workspace"}
                )
                continue

            if not p_resolved.exists():
                failures.append({"path": path_str, "status": "failed", "reason": "file not found"})
                continue
            if not p_resolved.is_file():
                failures.append({"path": path_str, "status": "failed", "reason": "not a regular file"})
                continue

            key = str(p_resolved)
            if key in file_state:
                content = file_state[key]
            else:
                raw = p_resolved.read_bytes()
                if _is_binary(raw):
                    failures.append({"path": path_str, "status": "failed", "reason": "binary file"})
                    continue
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    failures.append({"path": path_str, "status": "failed", "reason": "not utf-8"})
                    continue
                file_orig_sha[key] = hashlib.sha256(raw).hexdigest()

            new_content, match_type, fscore, err = _plan_one(content, old, new, replace_all)
            if err is not None:
                failures.append({"path": path_str, "status": "failed", "reason": err})
                continue
            assert new_content is not None
            file_state[key] = new_content

            rel = str(p_resolved.relative_to(cwd))
            diff = "".join(
                difflib.unified_diff(
                    content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=rel,
                    tofile=rel,
                    n=2,
                )
            )
            plans.append(
                {
                    "key": key,
                    "rel": rel,
                    "match_type": match_type,
                    "fuzzy_score": fscore,
                    "diff": diff,
                }
            )
        except Exception as e:  # pragma: no cover - defensive
            failures.append(
                {"path": path_str, "status": "failed", "reason": f"unexpected error: {e}"}
            )

    if failures:
        results: list[dict] = list(failures)
        for plan in plans:
            results.append(
                {
                    "path": plan["rel"],
                    "status": "not_applied",
                    "reason": "skipped due to other failures in batch",
                }
            )
        return {
            "applied": 0,
            "failed": len([r for r in results if r["status"] == "failed"]),
            "results": results,
        }

    # Phase 2: re-verify hashes, then write atomically.
    for key, expected_sha in file_orig_sha.items():
        current_sha = hashlib.sha256(Path(key).read_bytes()).hexdigest()
        if current_sha != expected_sha:
            return {
                "applied": 0,
                "failed": len(plans),
                "results": [
                    {"path": plan["rel"], "status": "failed", "reason": "file modified during edit"}
                    for plan in plans
                ],
            }

    written: list[Path] = []
    for key, final_content in file_state.items():
        target = Path(key)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".beaglelathe.",
            suffix=".tmp",
            delete=False,
        )
        try:
            tmp.write(final_content)
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp.name, str(target))
        written.append(target)

    out_results = []
    for plan in plans:
        r: dict[str, Any] = {
            "path": plan["rel"],
            "status": "applied",
            "match_type": plan["match_type"],
            "diff": plan["diff"],
        }
        if plan["fuzzy_score"] is not None:
            r["fuzzy_score"] = plan["fuzzy_score"]
        if validate:
            target = Path(plan["key"])
            status, detail = validators.validate(target, file_state[plan["key"]])
            r["validation"] = status
            if detail is not None:
                r["validation_detail"] = detail
        out_results.append(r)

    return {"applied": len(plans), "failed": 0, "results": out_results}
