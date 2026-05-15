"""Local credentials store at ~/.beaglelathe/credentials.json.

The file is written with mode 0600 (rw owner only). The directory is created
on demand. Loaders return None when the file is absent so callers can
distinguish "not logged in" from "corrupt".
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


class CredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    jwt: str
    user_id: str
    email: str
    plan: str
    budget_remaining: Optional[int]
    budget_resets_at: str
    base_url: str
    issued_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def credentials_path() -> Path:
    return Path(os.environ.get("BEAGLELATHE_HOME", str(Path.home() / ".beaglelathe"))) / "credentials.json"


def load_credentials(path: Optional[Path] = None) -> Optional[Credentials]:
    p = path or credentials_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise CredentialsError(f"could not read credentials at {p}: {e}") from e
    try:
        return Credentials(
            jwt=data["jwt"],
            user_id=data["user_id"],
            email=data["email"],
            plan=data["plan"],
            budget_remaining=data.get("budget_remaining"),
            budget_resets_at=data["budget_resets_at"],
            base_url=data["base_url"],
            issued_at=data["issued_at"],
        )
    except KeyError as e:
        raise CredentialsError(f"credentials missing field: {e}") from e


def save_credentials(creds: Credentials, path: Optional[Path] = None) -> Path:
    p = path or credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(creds.to_dict(), indent=2), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, p)
    return p


def clear_credentials(path: Optional[Path] = None) -> bool:
    """Remove credentials file. Returns True if a file was removed."""
    p = path or credentials_path()
    if not p.exists():
        return False
    p.unlink()
    return True
