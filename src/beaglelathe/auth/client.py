"""HTTP client for the BeagleLathe auth backend (magic-link login flow)."""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .credentials import Credentials


PROD_API_URL = "https://beaglelathe-api.fly.dev"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


class AuthError(RuntimeError):
    pass


def default_base_url() -> str:
    """Backend URL: BEAGLELATHE_API_URL env var, or the production Fly.io URL.

    Read at call time, not at import, so callers (and tests) can override the
    env var after the module is already loaded.
    """
    return os.environ.get("BEAGLELATHE_API_URL", PROD_API_URL).rstrip("/")


def device_fingerprint() -> str:
    """Stable per-device identifier for the auth backend.

    Backed by hostname + username + machine arch — not a security boundary, just
    a hint the backend stores so a user can see which machines have active
    sessions later. 32 hex chars (§8 char limit on backend is min 8).
    """
    parts = [platform.node(), platform.machine(), getpass.getuser()]
    raw = "::".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


class AuthClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)

    def __enter__(self) -> "AuthClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def start(self, fingerprint: Optional[str] = None) -> dict:
        url = f"{self.base_url}/auth/start"
        body = {"device_fingerprint": fingerprint or device_fingerprint()}
        try:
            resp = self._client.post(url, json=body)
        except httpx.HTTPError as e:
            raise AuthError(f"could not reach {url}: {e}") from e
        if resp.status_code >= 400:
            raise AuthError(f"POST /auth/start failed: HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def poll_once(self, session_id: str, poll_secret: str) -> dict:
        url = f"{self.base_url}/auth/poll"
        try:
            resp = self._client.get(
                url, params={"session": session_id, "secret": poll_secret}
            )
        except httpx.HTTPError as e:
            raise AuthError(f"could not reach {url}: {e}") from e
        if resp.status_code >= 400:
            raise AuthError(f"GET /auth/poll failed: HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def poll_until_complete(
        self,
        session_id: str,
        poll_secret: str,
        *,
        deadline_seconds: float = 600.0,
        on_pending: Optional[callable] = None,
    ) -> dict:
        """Poll /auth/poll until status == 'ok' or deadline passes.

        The backend long-polls (up to ~LOGIN_POLL_MAX_SECONDS) so each call may
        sit for a while. We loop until we see status='ok'. Returns the full
        PollOk payload (jwt, user, plan, budget_remaining, budget_resets_at).
        """
        start = time.monotonic()
        while True:
            payload = self.poll_once(session_id, poll_secret)
            if payload.get("status") == "ok":
                return payload
            if on_pending:
                on_pending()
            if time.monotonic() - start > deadline_seconds:
                raise AuthError(
                    f"login timed out after {deadline_seconds:.0f}s without confirmation"
                )
            time.sleep(self.poll_interval)


def credentials_from_poll_ok(payload: dict, base_url: str) -> Credentials:
    """Build a Credentials record from a PollOk JSON body."""
    user = payload.get("user") or {}
    return Credentials(
        jwt=payload["jwt"],
        user_id=str(user.get("id", "")),
        email=str(user.get("email", "")),
        plan=str(payload.get("plan", "free")),
        budget_remaining=payload.get("budget_remaining"),
        budget_resets_at=str(payload.get("budget_resets_at", "")),
        base_url=base_url,
        issued_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
