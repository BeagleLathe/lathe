"""Auth subsystem for the BeagleLathe MCP client.

Public surface: load_credentials, save_credentials, clear_credentials,
device_fingerprint, login_flow, default_base_url.
"""

from .credentials import (
    Credentials,
    CredentialsError,
    clear_credentials,
    credentials_path,
    load_credentials,
    save_credentials,
)
from .client import AuthClient, AuthError, default_base_url, device_fingerprint

__all__ = [
    "AuthClient",
    "AuthError",
    "Credentials",
    "CredentialsError",
    "clear_credentials",
    "credentials_path",
    "default_base_url",
    "device_fingerprint",
    "load_credentials",
    "save_credentials",
]
