"""Tests for the remote-API pairing (`save-finder connect`) flow."""

from __future__ import annotations

import base64
import json
import os
import stat

import httpx
import pytest
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

from save_finder.errors import RemoteAuthError, RemoteError
from save_finder.remote import (
    RemoteAccount,
    connect,
    derive_kek,
    load_credentials,
    save_credentials,
)

PASSWORD = "senha-super-secreta-cli"
EMAIL = "cli@example.com"
USERNAME = "cliuser"
SALT = bytes(range(16))
DEK = b"\xab" * 32
NONCE = b"\xcd" * 24


def _crypto_payload() -> dict:
    kek = derive_kek(PASSWORD.encode(), SALT)
    wrapped = crypto_aead_xchacha20poly1305_ietf_encrypt(DEK, None, NONCE, kek)
    return {
        "salt": base64.b64encode(SALT).decode(),
        "wrapped_dek": base64.b64encode(wrapped).decode(),
        "dek_nonce": base64.b64encode(NONCE).decode(),
        "kdf_ops_limit": 2,
        "kdf_mem_limit": 19456 * 1024,
    }


def _handler_factory(*, crypto_wrapped_with: bytes | None = None):
    payload = _crypto_payload()
    if crypto_wrapped_with is not None:
        payload = {
            **payload,
            "wrapped_dek": base64.b64encode(crypto_wrapped_with).decode(),
        }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/auth/login":
            body = json.loads(request.content.decode())
            if body.get("password") != PASSWORD:
                return httpx.Response(
                    401,
                    json={
                        "error": {"code": "invalid_credentials", "message": "invalid credentials"}
                    },
                )
            return httpx.Response(
                200,
                json={
                    "user": {
                        "id": "1",
                        "email": EMAIL,
                        "username": USERNAME,
                        "created_at": "2026-11-14T10:00:00Z",
                    },
                    "csrf_token": "csrf-token-123",
                },
                headers={
                    "Set-Cookie": "save_finder_session=session-cookie-value; Path=/; HttpOnly"
                },
            )
        if request.method == "GET" and path == "/auth/sessions":
            return httpx.Response(
                200,
                json={
                    "sessions": [
                        {"id": "s1", "current": True, "absolute_expires_at": "2026-12-14T00:00:00Z"}
                    ],
                },
            )
        if request.method == "GET" and path == "/auth/crypto":
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "not found"}})

    return handler


def test_connect_success() -> None:
    transport = httpx.MockTransport(_handler_factory())
    account = connect("http://api.test", EMAIL, PASSWORD, transport=transport)
    assert account.email == EMAIL
    assert account.username == USERNAME
    assert account.api_url == "http://api.test"
    assert account.session_cookie == "save_finder_session=session-cookie-value"
    assert account.csrf_token == "csrf-token-123"
    assert account.expires_at == "2026-12-14T00:00:00Z"


def test_connect_wrong_password() -> None:
    transport = httpx.MockTransport(_handler_factory())
    with pytest.raises(RemoteAuthError):
        connect("http://api.test", EMAIL, "senha-errada", transport=transport)


def test_connect_unwrap_failure() -> None:
    # Server metadata wrapped with a different password: unwrap fails.
    other_kek = derive_kek(b"outra-senha", SALT)
    wrong_wrapped = crypto_aead_xchacha20poly1305_ietf_encrypt(DEK, None, NONCE, other_kek)
    transport = httpx.MockTransport(_handler_factory(crypto_wrapped_with=wrong_wrapped))
    with pytest.raises(RemoteAuthError):
        connect("http://api.test", EMAIL, PASSWORD, transport=transport)


def test_connect_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    with pytest.raises(RemoteError):
        connect("http://api.test", EMAIL, PASSWORD, transport=transport)


def test_save_and_load_credentials(tmp_path, monkeypatch) -> None:
    account = RemoteAccount(
        api_url="http://api.test",
        email=EMAIL,
        username=USERNAME,
        session_cookie="save_finder_session=value",
        csrf_token="csrf-token-123",
        expires_at=None,
    )

    class FakePaths:
        pass

    class FakeSettings:
        pass

    settings = FakeSettings()
    settings.paths = FakePaths()
    settings.paths.data = tmp_path
    monkeypatch.setattr(
        "save_finder.remote.credentials_path", lambda s: tmp_path / "credentials.json"
    )

    path = save_credentials(settings, account)
    assert path.is_file()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600

    loaded = load_credentials(settings)
    assert loaded is not None
    assert loaded.email == EMAIL
    assert loaded.session_cookie == account.session_cookie

    # Corrupt file -> clean error instead of a traceback.
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RemoteError):
        load_credentials(settings)
