"""HTTP client for the BeagleLathe backend's usage and billing endpoints."""

from __future__ import annotations

from .auth.credentials import Credentials

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HTTPX_AVAILABLE = False

DEFAULT_TIMEOUT = 10.0


class UsageClientError(RuntimeError):
    pass


def _headers(creds: Credentials) -> dict[str, str]:
    return {"Authorization": f"Bearer {creds.jwt}"}


def _base(creds: Credentials) -> str:
    return creds.base_url.rstrip("/")


def get_status(creds: Credentials) -> dict:
    """Call GET /usage/status. Returns the raw JSON dict."""
    if not _HTTPX_AVAILABLE:
        raise UsageClientError("httpx not installed; cannot reach backend")
    import httpx as _httpx
    url = f"{_base(creds)}/usage/status"
    try:
        resp = _httpx.get(url, headers=_headers(creds), timeout=DEFAULT_TIMEOUT)
    except _httpx.HTTPError as e:
        raise UsageClientError(f"network error reaching {url}: {e}") from e
    if resp.status_code == 401:
        raise UsageClientError("session expired — run `beaglelathe login` to refresh")
    if resp.status_code >= 400:
        raise UsageClientError(f"GET /usage/status returned HTTP {resp.status_code}")
    _stamp_contact()
    return resp.json()


def post_sync(creds: Credentials, tool_calls: int) -> dict:
    """Call POST /usage/sync. Returns the raw JSON dict (includes new JWT and upgrade_url)."""
    if not _HTTPX_AVAILABLE:
        raise UsageClientError("httpx not installed; cannot reach backend")
    import httpx as _httpx
    url = f"{_base(creds)}/usage/sync"
    try:
        resp = _httpx.post(
            url,
            json={"tool_calls": tool_calls},
            headers=_headers(creds),
            timeout=DEFAULT_TIMEOUT,
        )
    except _httpx.HTTPError as e:
        raise UsageClientError(f"network error reaching {url}: {e}") from e
    if resp.status_code == 401:
        raise UsageClientError("session expired — run `beaglelathe login` to refresh")
    if resp.status_code >= 400:
        raise UsageClientError(f"POST /usage/sync returned HTTP {resp.status_code}")
    _stamp_contact()
    return resp.json()


def post_checkout(creds: Credentials) -> str:
    """Call POST /billing/checkout. Returns the Stripe Checkout URL."""
    if not _HTTPX_AVAILABLE:
        raise UsageClientError("httpx not installed; cannot reach backend")
    import httpx as _httpx
    url = f"{_base(creds)}/billing/checkout"
    try:
        resp = _httpx.post(url, headers=_headers(creds), timeout=DEFAULT_TIMEOUT)
    except _httpx.HTTPError as e:
        raise UsageClientError(f"network error reaching {url}: {e}") from e
    if resp.status_code == 401:
        raise UsageClientError("session expired — run `beaglelathe login` to refresh")
    if resp.status_code >= 400:
        raise UsageClientError(f"POST /billing/checkout returned HTTP {resp.status_code}")
    _stamp_contact()
    data = resp.json()
    url_out = data.get("checkout_url")
    if not url_out:
        raise UsageClientError("backend returned no checkout_url")
    return url_out


def _stamp_contact() -> None:
    """Record a successful backend response for the offline-grace timer. Best-effort."""
    try:
        from .savings import set_last_server_contact
        set_last_server_contact()
    except Exception:
        pass
