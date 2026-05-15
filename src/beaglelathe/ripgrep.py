"""Thin subprocess wrapper around ripgrep with --json output."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class RipgrepNotFound(RuntimeError):
    pass


class RipgrepError(RuntimeError):
    pass


_FALLBACK_RG_PATHS = (
    "/opt/homebrew/bin/rg",
    "/usr/local/bin/rg",
    "/usr/bin/rg",
)


def _platform_key() -> str | None:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        if machine in ("x86_64", "amd64"):
            return "darwin-x86_64"
    elif sys.platform.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
    elif sys.platform.startswith("win"):
        if machine in ("amd64", "x86_64"):
            return "windows-x86_64"
    return None


def _vendored_rg_paths() -> list[Path]:
    key = _platform_key()
    if key is None:
        return []
    exe = "rg.exe" if key.startswith("windows") else "rg"
    candidates: list[Path] = []
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        candidates.append(Path(plugin_root) / "bin" / key / exe)
    here = Path(__file__).resolve()
    candidates.append(here.parent / "_bin" / key / exe)
    candidates.append(here.parents[2] / "bin" / key / exe)
    return candidates


def _resolve_rg() -> str:
    for candidate in _vendored_rg_paths():
        if candidate.is_file():
            if not os.access(candidate, os.X_OK):
                try:
                    candidate.chmod(0o755)
                except OSError:
                    pass
            return str(candidate)
    rg = shutil.which("rg")
    if rg:
        return rg
    for candidate_str in _FALLBACK_RG_PATHS:
        if Path(candidate_str).is_file():
            return candidate_str
    raise RipgrepNotFound(
        "ripgrep ('rg') not found. The plugin ships vendored binaries for "
        "darwin-arm64/x86_64, linux-arm64/x86_64, and windows-x86_64; "
        "your platform may not be supported. Install ripgrep manually: "
        "`brew install ripgrep` or `apt install ripgrep`."
    )


def run(
    pattern: str,
    cwd: Path,
    path_glob: str = "**/*",
    max_results: int = 50,
    context_lines: int = 2,
    case_sensitive: bool = False,
    timeout: float = 30.0,
) -> Iterable[dict]:
    rg = _resolve_rg()
    cmd: list[str] = [
        rg,
        "--json",
        "--max-count",
        str(max_results),
        "-C",
        str(context_lines),
    ]
    if not case_sensitive:
        cmd.append("-i")
    if path_glob and path_glob != "**/*":
        cmd.extend(["-g", path_glob])
    cmd.append(pattern)
    cmd.append(".")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise RipgrepError(f"ripgrep timed out after {timeout}s") from e

    # rg exits 0 on matches, 1 on no matches, 2 on error.
    if proc.returncode not in (0, 1):
        raise RipgrepError(proc.stderr.strip() or f"ripgrep exited {proc.returncode}")

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
