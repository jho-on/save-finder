"""Pairing with the remote SaveFinder API (apps/api).

The CLI never stores the account password or any key material: it logs in,
fetches the encrypted-key metadata and verifies the DEK by unwrapping it
locally. Only the session cookie (same lifetime as the account session) and
the CSRF token are persisted in ``data/credentials.json`` (mode 0600).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from argon2.low_level import Type, hash_secret_raw
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt

from save_finder.config import Settings
from save_finder.errors import RemoteAuthError, RemoteError

CREDENTIALS_FILE = "credentials.json"
COOKIE_NAME = "save_finder_session"

# Must match the browser client (hash-wasm argon2id): 2 iterations,
# 19 MiB of memory, one lane, 32-byte output.
KDF_OPS_LIMIT = 2
KDF_MEMORY_COST_KIB = 19456
KDF_PARALLELISM = 1
KDF_KEY_LENGTH = 32


@dataclass(frozen=True, slots=True)
class RemoteAccount:
    api_url: str
    email: str
    username: str
    session_cookie: str
    csrf_token: str
    expires_at: str | None


def derive_kek(password: bytes, salt: bytes) -> bytes:
    """Derive the key-encryption key exactly as the browser client does."""
    return hash_secret_raw(
        password,
        salt,
        time_cost=KDF_OPS_LIMIT,
        memory_cost=KDF_MEMORY_COST_KIB,
        parallelism=KDF_PARALLELISM,
        hash_len=KDF_KEY_LENGTH,
        type=Type.ID,
    )


def _b64d(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise RemoteError("remote returned an invalid base64 blob") from exc


def connect(
    api_url: str,
    identifier: str,
    password: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> RemoteAccount:
    """Authenticate against the remote API and verify the crypto metadata."""
    api_url = api_url.rstrip("/")
    try:
        client = (
            httpx.Client(timeout=30.0, transport=transport)
            if transport
            else httpx.Client(timeout=30.0)
        )
    except Exception as exc:  # pragma: no cover - configuration error
        raise RemoteError("could not create HTTP client") from exc

    try:
        with client:
            try:
                resp = client.post(
                    f"{api_url}/auth/login",
                    json={"identifier": identifier, "password": password, "remember_me": True},
                )
            except httpx.HTTPError as exc:
                raise RemoteError(f"could not reach the remote API at {api_url}") from exc
            if resp.status_code == 401:
                raise RemoteAuthError("invalid credentials")
            if resp.status_code == 429:
                raise RemoteError("too many attempts; try again later")
            if resp.status_code != 200:
                raise RemoteError(f"login failed (status {resp.status_code})")
            login = resp.json()
            csrf = login["csrf_token"]
            user = login["user"]
            cookie_value = resp.cookies.get(COOKIE_NAME, "")
            if not cookie_value:
                raise RemoteError("remote API did not set a session cookie")

            sessions = _safe_json(client.get(f"{api_url}/auth/sessions")).get("sessions", [])
            current = next((s for s in sessions if s.get("current")), None)

            crypto = client.get(f"{api_url}/auth/crypto")
            if crypto.status_code == 404:
                raise RemoteError(
                    "this account has no encryption keys yet; "
                    "register and set them up in the remote web app first"
                )
            if crypto.status_code != 200:
                raise RemoteError(
                    f"could not fetch the crypto metadata (status {crypto.status_code})"
                )
            meta = crypto.json()
            kek = derive_kek(password.encode("utf-8"), _b64d(meta["salt"]))
            try:
                crypto_aead_xchacha20poly1305_ietf_decrypt(
                    _b64d(meta["wrapped_dek"]),
                    None,
                    _b64d(meta["dek_nonce"]),
                    kek,
                )
            except Exception as exc:
                raise RemoteAuthError(
                    "the account key could not be unwrapped; it may belong to a different password"
                ) from exc
    except (RemoteError, RemoteAuthError):
        raise
    except Exception as exc:
        raise RemoteError(f"unexpected error while connecting: {exc}") from exc

    return RemoteAccount(
        api_url=api_url,
        email=user["email"],
        username=user["username"],
        session_cookie=f"{COOKIE_NAME}={cookie_value}",
        csrf_token=csrf,
        expires_at=(current or {}).get("absolute_expires_at"),
    )


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


def credentials_path(settings: Settings) -> Path:
    return Path(settings.paths.data) / CREDENTIALS_FILE


def save_credentials(settings: Settings, account: RemoteAccount) -> Path:
    path = credentials_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(account), indent=2) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_credentials(settings: Settings) -> RemoteAccount | None:
    path = credentials_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RemoteAccount(**data)
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        raise RemoteError(f"could not read credentials from {path}") from exc
