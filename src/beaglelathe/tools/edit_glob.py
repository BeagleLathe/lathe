"""`edit_glob` tool: atomic regex substitution across a glob.

Resolves a file glob, applies a Python regex `re.subn` substitution across
every matching file, and commits all writes atomically (all-or-nothing).
Designed as the drop-in replacement for `Bash sed -i` on multi-file renames —
the differentiator is the atomicity guarantee: if any file fails to read,
decode, or hash-check, NO target files are written.

Atomicity is a two-phase commit: Phase 2 stages every change to a sibling
.tmp file. Phase 3 re-verifies every original file's hash against what was
read in Phase 1 — if anything was modified by another writer mid-run, the
staged tmps are deleted and the batch aborts with NO target files touched.
Phase 4 renames each .tmp into place via os.replace.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .edit import _is_binary, _snippet_for_edit

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "glob": {"type": "string", "description": "File glob (e.g. src/**/*.ts)."},
        "pattern": {"type": "string", "description": "Python regex."},
        "replacement": {"type": "string"},
        "flags": {
            "type": "array",
            "items": {"type": "string", "enum": ["MULTILINE", "DOTALL", "IGNORECASE"]},
            "default": [],
        },
        "max_files": {"type": "integer", "default": 200},
    },
    "required": ["glob", "pattern", "replacement"],
}

DESCRIPTION = (
    "Atomic regex substitution across files matching a glob. Use this INSTEAD "
    "of Bash sed for bulk renames: per-file diffs, atomic rollback, no "
    "half-applied state on failure."
)


_FLAG_MAP = {
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "IGNORECASE": re.IGNORECASE,
}


def _compile_flags(names: list[str]) -> int:
    out = 0
    for n in names:
        flag = _FLAG_MAP.get(n)
        if flag is None:
            raise ValueError(f"unknown flag: {n}")
        out |= flag
    return out


def _write_atomic(target: Path, content: str) -> Path:
    """Stage `content` to a sibling .tmp file of `target`. Returns the .tmp path.

    Does NOT rename. The two-phase commit in `run_edit_glob` calls this once
    per changed file to write the staging tmp, then re-verifies all original
    hashes, then renames each .tmp into its target. Exposed for monkeypatching
    so tests can inject a race between staging and the hash recheck.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=".beaglelathe.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    return Path(tmp.name)


def _resolve_glob(cwd: Path, pattern: str, max_files: int) -> tuple[list[Path], bool]:
    """Return (paths, capped). Filters to files within cwd; sorts for determinism."""
    matched: list[Path] = []
    for p in cwd.glob(pattern):
        try:
            resolved = p.resolve(strict=False)
            resolved.relative_to(cwd)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            matched.append(resolved)
    matched.sort()
    capped = len(matched) > max_files
    if capped:
        matched = matched[:max_files]
    return matched, capped


def run_edit_glob(args: dict, cwd: Path) -> dict:
    cwd = cwd.resolve()
    glob_pattern = args.get("glob")
    pattern = args.get("pattern")
    replacement = args.get("replacement")
    if not glob_pattern or pattern is None or replacement is None:
        return {"error": "glob, pattern, replacement are required"}

    flag_names = args.get("flags") or []
    try:
        flag_bits = _compile_flags(list(flag_names))
    except ValueError as e:
        return {"error": str(e)}
    try:
        regex = re.compile(pattern, flag_bits)
    except re.error as e:
        return {"error": f"invalid regex: {e}"}

    max_files = int(args.get("max_files", 200))
    paths, capped = _resolve_glob(cwd, glob_pattern, max_files)

    # Phase 1: read + plan. Any binary or non-utf8 file in the glob aborts.
    plans: list[dict[str, Any]] = []
    for p in paths:
        raw = p.read_bytes()
        if _is_binary(raw):
            return _error_response(
                f"binary file in glob: {p.relative_to(cwd)}", len(paths)
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _error_response(
                f"non-utf8 file in glob: {p.relative_to(cwd)}", len(paths)
            )
        new_content, subs = regex.subn(replacement, content)
        plans.append(
            {
                "path": p,
                "rel": str(p.relative_to(cwd)),
                "content": content,
                "new_content": new_content,
                "subs": subs,
                "orig_sha": hashlib.sha256(raw).hexdigest(),
            }
        )

    # Two-phase commit. `staged` holds (tmp_path → target_path) for files
    # awaiting rename. On any failure, every tmp gets unlinked and no targets
    # are touched.
    staged: list[tuple[Path, Path]] = []
    try:
        # Phase 2: stage every changed file to a sibling tmp.
        for plan in plans:
            if plan["subs"] == 0:
                continue
            tmp_path = _write_atomic(plan["path"], plan["new_content"])
            staged.append((tmp_path, plan["path"]))

        # Phase 3: re-verify every original file's hash.
        for plan in plans:
            if plan["subs"] == 0:
                continue
            current = hashlib.sha256(plan["path"].read_bytes()).hexdigest()
            if current != plan["orig_sha"]:
                return _abort(staged, plan["rel"], "file modified during edit", plans)

        # Phase 4: commit. Rename each staged tmp into its target.
        committed: list[tuple[Path, Path]] = []
        for tmp_path, target in staged:
            os.replace(str(tmp_path), str(target))
            committed.append((tmp_path, target))
        staged = []  # nothing left for cleanup
    finally:
        for tmp_path, _ in staged:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Build results.
    total_subs = 0
    results: list[dict[str, Any]] = []
    for plan in plans:
        if plan["subs"] > 0:
            entry: dict[str, Any] = {"path": plan["rel"], "subs": plan["subs"]}
            snippet = _snippet_for_edit(plan["content"], plan["new_content"])
            if snippet is not None:
                entry["snippet"] = snippet
            results.append(entry)
            total_subs += plan["subs"]
        else:
            results.append({"path": plan["rel"], "subs": 0})

    out: dict[str, Any] = {
        "files_matched": len(plans),
        "files_changed": sum(1 for p in plans if p["subs"] > 0),
        "total_subs": total_subs,
        "results": results,
    }
    if capped:
        out["capped"] = True
    return out


def _error_response(error: str, files_matched: int) -> dict[str, Any]:
    return {
        "error": error,
        "files_matched": files_matched,
        "files_changed": 0,
        "total_subs": 0,
        "results": [],
    }


def _abort(
    staged: list[tuple[Path, Path]],
    rel: str,
    reason: str,
    plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Roll back: delete every staged tmp, return an error result."""
    for tmp_path, _ in staged:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    staged.clear()
    return {
        "error": f"{reason}: {rel}",
        "files_matched": len(plans),
        "files_changed": 0,
        "total_subs": 0,
        "results": [],
    }
