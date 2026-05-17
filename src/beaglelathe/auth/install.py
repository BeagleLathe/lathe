"""Plugin install routine: register the bundled plugin tree with Claude Code.

The pip wheel ships the plugin files (`.claude-plugin/`, `agents/`, `commands/`,
`hooks/`, `scripts/`) at `beaglelathe/_plugin/`. This module:

  1. Copies that tree into a stable user-local location (`~/.beaglelathe/plugin/`)
     so the marketplace reference survives venv switches and Python upgrades.
  2. Shells out to `claude plugin marketplace add <local-path>` to register it.
  3. Shells out to `claude plugin install lathe@beaglelathe` to install it.

Idempotent: if the marketplace is already registered and the plugin is already
installed, both shell-outs are skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Optional

MARKETPLACE_NAME = "beaglelathe"
PLUGIN_NAME = "lathe"
PLUGIN_REF = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"

CLAUDE_INSTALL_URL = "https://claude.com/code"


class InstallError(Exception):
    """Raised when the plugin install can't proceed."""


@dataclass
class InstallResult:
    ok: bool
    already_installed: bool
    plugin_path: Optional[Path]
    message: str


def bundled_plugin_path() -> Path:
    """Path to the plugin tree.

    Prefers `<package>/_plugin/` (wheel layout, populated by the
    `force-include` entries in pyproject.toml). Falls back to the repo source
    layout for editable installs, where the plugin files live alongside
    `src/` at the repo root.
    """
    import os
    override = os.environ.get("BEAGLELATHE_PLUGIN_SRC")
    if override:
        path = Path(override)
        if (path / ".claude-plugin" / "marketplace.json").is_file():
            return path
        raise InstallError(
            f"BEAGLELATHE_PLUGIN_SRC={override} does not contain .claude-plugin/marketplace.json."
        )

    pkg_root = Path(str(resources.files("beaglelathe")))
    wheel_layout = pkg_root / "_plugin"
    if (wheel_layout / ".claude-plugin" / "marketplace.json").is_file():
        return wheel_layout

    # Editable install: src/beaglelathe -> repo root
    repo_root = pkg_root.parent.parent
    if (repo_root / ".claude-plugin" / "marketplace.json").is_file():
        return repo_root

    raise InstallError(
        f"bundled plugin tree not found near {pkg_root}. "
        "For wheel installs, this usually means the wheel was built without the plugin "
        "force-includes — reinstall with `pip install --force-reinstall beaglelathe`."
    )


def local_plugin_root() -> Path:
    """User-local copy of the plugin tree. Stable across venvs and upgrades."""
    import os
    home = os.environ.get("BEAGLELATHE_HOME")
    base = Path(home) if home else Path.home() / ".beaglelathe"
    return base / "plugin"


def claude_plugins_dir() -> Path:
    return Path.home() / ".claude" / "plugins"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def is_marketplace_registered() -> bool:
    data = _read_json(claude_plugins_dir() / "known_marketplaces.json")
    return MARKETPLACE_NAME in data


def is_plugin_installed() -> bool:
    data = _read_json(claude_plugins_dir() / "installed_plugins.json")
    plugins = data.get("plugins", {})
    entries = plugins.get(PLUGIN_REF) or []
    return bool(entries)


def sync_plugin_tree(*, force: bool = False) -> tuple[Path, bool]:
    """Copy the bundled plugin tree to `local_plugin_root()`.

    Returns `(path, changed)`. `changed` is True when the tree was (re)written
    on this call — either because it was missing, force-synced, or the version
    stamp differed from the installed package version.
    """
    src = bundled_plugin_path()
    dst = local_plugin_root()
    stamp = dst / ".version"

    target_version = _package_version()
    current_version = stamp.read_text().strip() if stamp.exists() else None

    if not force and dst.exists() and current_version == target_version:
        return dst, False

    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    _pin_plugin_json_to_current_python(dst)
    stamp.write_text(target_version)
    return dst, True


def _pin_plugin_json_to_current_python(plugin_dir: Path) -> None:
    """Rewrite the local plugin.json so the MCP server command uses an absolute
    path to the Python interpreter that has `beaglelathe` installed.

    Why: the bundled plugin.json calls the `beaglelathe` console script, which
    works when that script is on PATH (typical for system pip, pipx, and
    activated venvs). For unactivated venvs and other unusual layouts, pinning
    to `sys.executable` makes the MCP server start regardless of PATH.
    """
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        return
    try:
        data = json.loads(plugin_json.read_text())
    except json.JSONDecodeError:
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "lathe" not in servers:
        return
    servers["lathe"] = {
        "command": sys.executable,
        "args": ["-m", "beaglelathe"],
    }
    plugin_json.write_text(json.dumps(data, indent=2) + "\n")


def _package_version() -> str:
    try:
        from importlib.metadata import version
        return version("beaglelathe")
    except Exception:
        return "0.0.0"


def _claude_executable() -> str:
    exe = shutil.which("claude")
    if exe is None:
        raise InstallError(
            "Claude Code CLI ('claude') not found in PATH. "
            f"Install it from {CLAUDE_INSTALL_URL}, then re-run `beaglelathe install`."
        )
    return exe


def _run_claude(*args: str) -> subprocess.CompletedProcess:
    exe = _claude_executable()
    try:
        return subprocess.run(
            [exe, *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise InstallError(f"failed to invoke claude: {e}") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        detail = stderr or stdout or f"exit code {e.returncode}"
        raise InstallError(
            f"`claude {' '.join(args)}` failed: {detail}"
        ) from e


def install_plugin(*, force: bool = False, log=None) -> InstallResult:
    """Idempotently register and install the BeagleLathe plugin.

    Returns an InstallResult. Raises InstallError if `claude` is missing or a
    shell-out fails.
    """
    def _log(msg: str) -> None:
        if log is not None:
            log(msg)

    plugin_path, tree_changed = sync_plugin_tree(force=force)

    if (
        not force
        and not tree_changed
        and is_marketplace_registered()
        and is_plugin_installed()
    ):
        return InstallResult(
            ok=True,
            already_installed=True,
            plugin_path=plugin_path,
            message=f"Plugin {PLUGIN_REF} already installed.",
        )

    if force or not is_marketplace_registered():
        _log(f"Registering marketplace from {plugin_path} ...")
        _run_claude("plugin", "marketplace", "add", str(plugin_path))

    if force or tree_changed or not is_plugin_installed():
        _log(f"Installing {PLUGIN_REF} ...")
        _run_claude("plugin", "install", PLUGIN_REF)

    return InstallResult(
        ok=True,
        already_installed=False,
        plugin_path=plugin_path,
        message=(
            f"Installed {PLUGIN_REF}. Restart Claude Code (or run /mcp restart) to activate."
        ),
    )
