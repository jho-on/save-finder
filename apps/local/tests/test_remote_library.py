"""Tests for the E2EE library upload (`save-finder remote push|wipe`)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.config import Settings
from save_finder.errors import RemoteAuthError, RemoteError
from save_finder.models import (
    Item,
    ItemStatus,
    ItemTag,
    MediaAsset,
    MediaKind,
    PlatformSource,
    PostType,
    Tag,
    TagLevel,
    TagSource,
    Transcript,
)
from save_finder.remote import RemoteAccount, derive_kek, save_credentials
from save_finder.remote_library import push_library, wipe_library

PASSWORD = "senha-super-secreta-cli"
API_URL = "http://api.test"
SALT = bytes(range(16))
DEK = b"\xab" * 32
NONCE = b"\xcd" * 24
THUMB_BYTES = b"\x89fake-thumb-bytes" * 4


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


def _account() -> RemoteAccount:
    return RemoteAccount(
        api_url=API_URL,
        email="cli@example.com",
        username="cliuser",
        session_cookie="save_finder_session=session-cookie-value",
        csrf_token="csrf-token-123",
        expires_at="2026-12-14T00:00:00Z",
    )


def _check_csrf_and_auth(request: httpx.Request, *, mutating: bool = True) -> None:
    if mutating:
        assert request.headers.get("x-csrf-token") == "csrf-token-123"
    assert request.headers.get("cookie") == "save_finder_session=session-cookie-value"


def _handler(state: dict, *, session_ok: bool = True, wipe_ok: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/auth/account":
            if not session_ok:
                return httpx.Response(
                    401, json={"error": {"code": "session_expired", "message": "expired"}}
                )
            _check_csrf_and_auth(request, mutating=False)
            return httpx.Response(
                200, json={"user": {"email": "cli@example.com"}, "csrf_token": "csrf-token-123"}
            )
        if request.method == "GET" and path == "/auth/crypto":
            return httpx.Response(200, json=_crypto_payload())
        if request.method == "POST" and path == "/library/items/batch":
            _check_csrf_and_auth(request)
            body = json.loads(request.content.decode())
            state.setdefault("batches", []).append(body["items"])
            return httpx.Response(200, json={"upserted": len(body["items"])})
        if request.method == "DELETE" and path == "/library":
            _check_csrf_and_auth(request)
            body = json.loads(request.content.decode())
            if body.get("password") != PASSWORD or not wipe_ok:
                return httpx.Response(
                    401, json={"error": {"code": "invalid_credentials", "message": "invalid"}}
                )
            return httpx.Response(200, json={"deleted": state.get("count", 3)})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "not found"}})

    return handler


def _make_item(session: Session, external_id: str, *, with_thumb_path: str | None = None) -> Item:
    item = Item(
        platform="instagram",
        external_id=external_id,
        url=f"https://www.instagram.com/p/{external_id}/",
        source=PlatformSource.EXPORT,
        author="chef.ana",
        caption=f"receita {external_id}",
        post_type=PostType.VIDEO,
        media_count=1,
        status=ItemStatus.CLASSIFIED,
        saved_at="2026-11-01T00:00:00Z",
        collection="receitas",
        thumb_path=with_thumb_path,
    )
    session.add(item)
    session.flush()
    return item


def _seed_item(session: Session, settings: Settings, external_id: str, *, with_thumb: bool) -> Item:
    item = _make_item(
        session, external_id, with_thumb_path=f"thumbs/{external_id}.webp" if with_thumb else None
    )
    broad = session.execute(select(Tag).where(Tag.slug == "comida-e-bebida")).scalar_one_or_none()
    if broad is None:
        broad = Tag(slug="comida-e-bebida", name="Comida e bebida", level=TagLevel.BROAD)
        session.add(broad)
        session.flush()
    session.add(
        ItemTag(
            item_id=item.id,
            tag_id=broad.id,
            confidence=0.9,
            source=TagSource.AUTO,
            evidence="llm:receita",
        )
    )
    session.add(
        MediaAsset(
            item_id=item.id,
            position=0,
            kind=MediaKind.VIDEO,
            duration_seconds=31.5,
            width=720,
            height=1280,
        )
    )
    session.add(
        Transcript(
            item_id=item.id,
            language="pt",
            text="junte a farinha",
            segments=[{"t": 0, "text": "junte"}],
            model="whisper",
        )
    )
    if with_thumb:
        thumb_file = Path(settings.paths.data) / f"thumbs/{external_id}.webp"
        thumb_file.parent.mkdir(parents=True, exist_ok=True)
        thumb_file.write_bytes(THUMB_BYTES)
    session.commit()
    return item


def _decrypt(dek: bytes, blob_b64: str) -> bytes:
    blob = base64.b64decode(blob_b64)
    return crypto_aead_xchacha20poly1305_ietf_decrypt(blob[24:], None, blob[:24], dek)


def test_push_uploads_encrypted_items(
    session: Session, session_factory, settings: Settings
) -> None:
    _seed_item(session, settings, "ABC123", with_thumb=True)
    _seed_item(session, settings, "DEF456", with_thumb=False)
    save_credentials(settings, _account())

    state: dict = {}
    transport = httpx.MockTransport(_handler(state))
    summary = push_library(settings, password=PASSWORD, transport=transport)

    assert summary.pushed == 2
    assert summary.failed == 0
    assert summary.missing_thumbs == 1
    assert summary.total == 2

    items = [item for batch in state["batches"] for item in batch]
    assert {item["item_key"] for item in items} == {"instagram:ABC123", "instagram:DEF456"}
    by_key = {item["item_key"]: item for item in items}

    payload = json.loads(_decrypt(DEK, by_key["instagram:ABC123"]["payload"]))
    assert payload["caption"] == "receita ABC123"
    assert payload["status"] == "classified"
    assert payload["post_type"] == "video"
    assert payload["collection"] == "receitas"
    assert payload["media_assets"][0]["kind"] == "video"
    assert payload["tags"][0]["slug"] == "comida-e-bebida"
    # The server never sees cleartext: no caption bytes in the raw ciphertext.
    assert b"receita" not in base64.b64decode(by_key["instagram:ABC123"]["payload"])

    transcript = json.loads(_decrypt(DEK, by_key["instagram:ABC123"]["transcript"]))
    assert transcript["text"] == "junte a farinha"
    assert transcript["language"] == "pt"

    thumb_bytes = _decrypt(DEK, by_key["instagram:ABC123"]["thumb"])
    assert thumb_bytes == THUMB_BYTES
    assert "thumb" not in by_key["instagram:DEF456"]
    assert "transcript" in by_key["instagram:DEF456"]


def test_push_requires_credentials(session: Session, settings: Settings) -> None:
    with pytest.raises(RemoteError):
        push_library(settings, password=PASSWORD, transport=httpx.MockTransport(_handler({})))


def test_push_session_expired(session: Session, settings: Settings) -> None:
    save_credentials(settings, _account())
    with pytest.raises(RemoteAuthError):
        push_library(
            settings,
            password=PASSWORD,
            transport=httpx.MockTransport(_handler({}, session_ok=False)),
        )


def test_push_wrong_password(session: Session, settings: Settings) -> None:
    _seed_item(session, settings, "WRONG1", with_thumb=False)
    save_credentials(settings, _account())
    with pytest.raises(RemoteAuthError):
        push_library(
            settings,
            password="senha-errada",
            transport=httpx.MockTransport(_handler({})),
        )


def test_wipe_success_and_failure(session: Session, settings: Settings) -> None:
    save_credentials(settings, _account())
    state = {"count": 7}
    transport = httpx.MockTransport(_handler(state))
    assert wipe_library(settings, password=PASSWORD, transport=transport) == 7

    with pytest.raises(RemoteAuthError):
        wipe_library(
            settings,
            password="senha-errada",
            transport=httpx.MockTransport(_handler(state)),
        )
