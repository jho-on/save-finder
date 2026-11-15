"""E2EE upload of the local library to the remote API (apps/api).

The remote server stores only opaque ciphertext. Items are encrypted with
the account DEK (XChaCha20-Poly1305, ``blob = nonce || ciphertext``); the
DEK is unwrapped in memory from the account password and never persisted.
The only clear-text field sent to the server is ``item_key``
(``platform:external_id``), required for idempotent upserts.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from sqlalchemy import select
from sqlalchemy.orm import aliased

from save_finder.config import Settings
from save_finder.db import make_engine, make_session_factory, session_scope
from save_finder.errors import RemoteAuthError, RemoteError
from save_finder.models import Item, ItemTag, MediaAsset, Tag, Transcript
from save_finder.remote import (
    RemoteAccount,
    _b64d,
    derive_kek,
    load_credentials,
)

logger = logging.getLogger(__name__)

NONCE_BYTES = 24
BATCH_SIZE = 50
REQUEST_TIMEOUT = 120.0

_SESSION_EXPIRED_MSG = "the remote session expired; run 'save-finder connect' again"
_CSRF_MSG = "the remote rejected the CSRF token; reconnect with 'save-finder connect'"


@dataclass(frozen=True, slots=True)
class PushSummary:
    pushed: int
    failed: int
    missing_thumbs: int
    total: int


def _b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def encrypt_blob(dek: bytes, plaintext: bytes) -> bytes:
    """Encrypt a blob with the DEK: ``nonce || ciphertext``."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, None, nonce, dek)


def _auth_headers(account: RemoteAccount) -> dict[str, str]:
    return {"X-CSRF-Token": account.csrf_token}


def _make_client(
    account: RemoteAccount,
    transport: httpx.BaseTransport | None,
    timeout: float = REQUEST_TIMEOUT,
) -> httpx.Client:
    name, _, value = account.session_cookie.partition("=")
    return httpx.Client(timeout=timeout, transport=transport, cookies={name: value})


def fetch_crypto_metadata(client: httpx.Client, account: RemoteAccount) -> dict[str, Any]:
    resp = client.get(
        f"{account.api_url}/auth/crypto",
    )
    if resp.status_code == 401:
        raise RemoteAuthError(_SESSION_EXPIRED_MSG)
    if resp.status_code == 404:
        raise RemoteError(
            "this account has no encryption keys yet; "
            "register and set them up in the remote web app first"
        )
    if resp.status_code != 200:
        raise RemoteError(f"could not fetch the crypto metadata (status {resp.status_code})")
    return resp.json()


def unwrap_dek(password: str, meta: dict[str, Any]) -> bytes:
    """Unwrap the account DEK locally; it never leaves this process."""
    kek = derive_kek(password.encode("utf-8"), _b64d(meta["salt"]))
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(
            _b64d(meta["wrapped_dek"]),
            None,
            _b64d(meta["dek_nonce"]),
            kek,
        )
    except Exception as exc:
        raise RemoteAuthError(
            "the account key could not be unwrapped; the password may be wrong"
        ) from exc


def check_session(client: httpx.Client, account: RemoteAccount) -> dict[str, Any]:
    resp = client.get(
        f"{account.api_url}/auth/account",
    )
    if resp.status_code == 401:
        raise RemoteAuthError(_SESSION_EXPIRED_MSG)
    if resp.status_code != 200:
        raise RemoteError(f"could not verify the remote session (status {resp.status_code})")
    return resp.json()


def _tags_for_item(session, item_id: int) -> list[dict[str, Any]]:
    parent_tag = aliased(Tag)
    rows = session.execute(
        select(ItemTag, Tag, parent_tag.slug)
        .join(Tag, ItemTag.tag_id == Tag.id)
        .outerjoin(parent_tag, parent_tag.id == Tag.parent_id)
        .where(ItemTag.item_id == item_id)
        .order_by(Tag.level, Tag.name)
    ).all()
    return [
        {
            "slug": tag.slug,
            "name": tag.name,
            "level": tag.level.value,
            "parent": parent_slug,
            "confidence": item_tag.confidence,
            "source": item_tag.source.value,
            "evidence": item_tag.evidence,
        }
        for item_tag, tag, parent_slug in rows
    ]


def _media_assets_for_item(session, item_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(MediaAsset).where(MediaAsset.item_id == item_id).order_by(MediaAsset.position)
    ).scalars()
    return [
        {
            "position": asset.position,
            "kind": asset.kind.value,
            "duration_seconds": asset.duration_seconds,
            "width": asset.width,
            "height": asset.height,
        }
        for asset in rows
    ]


def build_item_payload(session, item: Item) -> dict[str, Any]:
    return {
        "platform": item.platform,
        "external_id": item.external_id,
        "url": item.url,
        "source": item.source.value,
        "author": item.author,
        "caption": item.caption,
        "post_type": item.post_type.value if item.post_type else None,
        "media_count": item.media_count,
        "duration_seconds": item.duration_seconds,
        "saved_at": item.saved_at,
        "collection": item.collection,
        "status": item.status.value,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "media_assets": _media_assets_for_item(session, item.id),
        "tags": _tags_for_item(session, item.id),
    }


def _transcript_payload(item: Item) -> dict[str, Any] | None:
    transcript: Transcript | None = item.transcript
    if transcript is None:
        return None
    return {
        "language": transcript.language,
        "text": transcript.text,
        "segments": transcript.segments,
        "model": transcript.model,
    }


def item_key(item: Item) -> str:
    return f"{item.platform}:{item.external_id}"


def require_credentials(settings: Settings) -> RemoteAccount:
    account = load_credentials(settings)
    if account is None:
        raise RemoteError("not connected; run 'save-finder connect' first")
    return account


def push_library(
    settings: Settings,
    *,
    password: str,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    transport: httpx.BaseTransport | None = None,
    progress: Any | None = None,
) -> PushSummary:
    """Encrypt and upload every local item (idempotent upsert)."""
    account = require_credentials(settings)
    dek: bytes | None = None
    try:
        with _make_client(account, transport) as client:
            check_session(client, account)
            meta = fetch_crypto_metadata(client, account)
            dek = unwrap_dek(password, meta)

            engine = make_engine(settings)
            try:
                with session_scope(make_session_factory(engine)) as session:
                    stmt = select(Item).order_by(Item.id)
                    if limit is not None:
                        stmt = stmt.limit(limit)
                    items = list(session.execute(stmt).scalars())

                    payloads: list[dict[str, Any]] = []
                    failed = 0
                    missing_thumbs = 0
                    data_dir = settings.paths.data
                    for item in items:
                        try:
                            body = json.dumps(
                                build_item_payload(session, item),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                            entry: dict[str, Any] = {
                                "item_key": item_key(item),
                                "payload": _b64e(encrypt_blob(dek, body)),
                            }
                            transcript = _transcript_payload(item)
                            if transcript is not None:
                                t_body = json.dumps(
                                    transcript, ensure_ascii=False, separators=(",", ":")
                                ).encode("utf-8")
                                entry["transcript"] = _b64e(encrypt_blob(dek, t_body))
                            if item.thumb_path:
                                thumb_file = data_dir / item.thumb_path
                                try:
                                    thumb_bytes = thumb_file.read_bytes()
                                except OSError:
                                    thumb_bytes = None
                                if thumb_bytes is None:
                                    missing_thumbs += 1
                                else:
                                    entry["thumb"] = _b64e(encrypt_blob(dek, thumb_bytes))
                            else:
                                missing_thumbs += 1
                            payloads.append(entry)
                        except Exception:
                            logger.exception(
                                "failed to build payload platform=%s external_id=%s",
                                item.platform,
                                item.external_id,
                            )
                            failed += 1
            finally:
                engine.dispose()

            pushed = 0
            for start in range(0, len(payloads), batch_size):
                batch = payloads[start : start + batch_size]
                resp = client.post(
                    f"{account.api_url}/library/items/batch",
                    json={"items": batch},
                    headers=_auth_headers(account),
                )
                if resp.status_code == 401:
                    raise RemoteAuthError(_SESSION_EXPIRED_MSG)
                if resp.status_code == 403:
                    raise RemoteError(_CSRF_MSG)
                if resp.status_code == 429:
                    raise RemoteError("too many requests; try again later")
                if resp.status_code != 200:
                    raise RemoteError(
                        f"upload failed (status {resp.status_code}): {resp.text[:200]}"
                    )
                pushed += resp.json().get("upserted", len(batch))
                if progress is not None:
                    progress(pushed, len(payloads))

            return PushSummary(
                pushed=pushed, failed=failed, missing_thumbs=missing_thumbs, total=len(items)
            )
    except (RemoteError, RemoteAuthError):
        raise
    except httpx.HTTPError as exc:
        raise RemoteError(f"could not reach the remote API at {account.api_url}") from exc
    finally:
        del dek


def wipe_library(
    settings: Settings,
    *,
    password: str,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Delete every library item stored on the remote API."""
    account = require_credentials(settings)
    try:
        with _make_client(account, transport) as client:
            check_session(client, account)
            resp = client.request(
                "DELETE",
                f"{account.api_url}/library",
                json={"password": password},
                headers=_auth_headers(account),
            )
            if resp.status_code == 401:
                raise RemoteAuthError("invalid password or expired session")
            if resp.status_code == 403:
                raise RemoteError(_CSRF_MSG)
            if resp.status_code == 429:
                raise RemoteError("too many attempts; try again later")
            if resp.status_code != 200:
                raise RemoteError(f"wipe failed (status {resp.status_code}): {resp.text[:200]}")
            try:
                return int(resp.json().get("deleted", 0))
            except (binascii.Error, ValueError, json.JSONDecodeError):
                return 0
    except (RemoteError, RemoteAuthError):
        raise
    except httpx.HTTPError as exc:
        raise RemoteError(f"could not reach the remote API at {account.api_url}") from exc
