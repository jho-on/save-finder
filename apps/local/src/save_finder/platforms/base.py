"""Platform adapter contract.

The core only talks to adapters through this module. No platform-specific
rule is allowed outside ``save_finder/platforms/<slug>.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from save_finder.errors import EnrichError, EnrichTransient, EnrichUnavailable

VIDEO_EXTENSIONS = {"m4v", "mkv", "mov", "mp4", "webm"}
IMAGE_EXTENSIONS = {"avif", "gif", "heic", "jpeg", "jpg", "png", "webp"}
UNAVAILABLE_TOKENS = (
    "404",
    "410",
    "blocked in your country",
    "deleted",
    "does not exist",
    "geo-restricted",
    "login required",
    "not available",
    "not found",
    "private",
    "region",
    "removed",
    "requested content is not available",
    "unavailable",
)
TRANSIENT_TOKENS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "network",
    "rate limit",
    "rate-limit",
    "temporarily",
    "timed out",
    "timeout",
    "too many requests",
    "try again",
    "unable to download",
)


@dataclass(slots=True)
class SavedItem:
    """Normalized item produced by an export parser."""

    platform: str
    external_id: str
    url: str
    source: str = "export"
    author: str | None = None
    collection: str | None = None
    saved_at: str | None = None


@dataclass(frozen=True, slots=True)
class Metadata:
    """Public metadata fetched during enrichment.

    ``thumbnail_url`` is only a transport value: it is downloaded and never
    persisted in the database.
    """

    author: str | None = None
    caption: str | None = None
    post_type: str | None = None
    media_count: int = 0
    duration_seconds: float | None = None
    thumbnail_url: str | None = None


class PlatformAdapter(Protocol):
    """Everything the core needs to know about a social network."""

    slug: str

    def parse_export(self, path: Path) -> Iterable[SavedItem]:
        """Yield normalized items from an official export."""
        ...

    def normalize_url(self, url: str) -> tuple[str, str]:
        """Return ``(canonical_url, external_id)`` or raise UnsupportedURL."""
        ...

    def enrich(self, item: SavedItem) -> Metadata:
        """Fetch public metadata for one item."""
        ...

    def download(self, item: SavedItem, dest: Path) -> Path:
        """Download every media piece of one item into ``dest``."""
        ...


class YtDlpAdapter:
    """Default metadata implementation backed by yt-dlp.

    Adapters opt in by subclassing this class. Only public metadata is read;
    no authenticated endpoint or private API is used.
    """

    slug: str
    socket_timeout: float = 30.0

    def _ydl_options(self) -> dict[str, Any]:
        return {
            "cachedir": False,
            "extract_flat": False,
            "ignoreerrors": False,
            "ignore_no_formats_error": True,
            "noplaylist": False,
            "no_warnings": True,
            "quiet": True,
            "skip_download": True,
            "socket_timeout": self.socket_timeout,
        }

    def enrich(self, item: SavedItem) -> Metadata:
        try:
            with YoutubeDL(self._ydl_options()) as ydl:
                info = ydl.extract_info(item.url, download=False)
        except YoutubeDLError as exc:
            raise _translate_ytdl_error(exc) from exc

        if not isinstance(info, dict):
            raise EnrichUnavailable("no metadata returned for post")
        return _metadata_from_info(info)


def _metadata_from_info(info: dict[str, Any]) -> Metadata:
    raw_entries = info.get("entries")
    if raw_entries is None:
        entries = [info]
    else:
        entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    if not entries:
        raise EnrichUnavailable("post has no analyzable media")

    first = entries[0]
    author = _author(info) or _author(first)
    caption = _text(info.get("description")) or _text(first.get("description"))
    if not any(_has_media(entry) for entry in entries):
        return Metadata(author=author, caption=caption, post_type=None, media_count=0)

    durations = [
        float(duration)
        for entry in entries
        if (duration := _number(entry.get("duration"))) is not None and duration > 0
    ]

    if len(entries) > 1:
        post_type = "carousel"
    elif any(_is_video(entry) for entry in entries):
        post_type = "video"
    else:
        post_type = "image"

    return Metadata(
        author=author,
        caption=caption,
        post_type=post_type,
        media_count=len(entries),
        duration_seconds=sum(durations) if durations else None,
        thumbnail_url=_best_thumbnail(info) or _best_thumbnail(first),
    )


def _author(info: dict[str, Any]) -> str | None:
    for key in ("uploader_id", "uploader", "channel", "creator"):
        value = _text(info.get(key))
        if value:
            return value.removeprefix("@")
    return None


def _has_media(entry: dict[str, Any]) -> bool:
    if entry.get("formats") or entry.get("thumbnails") or entry.get("thumbnail"):
        return True
    extension = _text(entry.get("ext"))
    return bool(extension and extension.casefold() in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)


def _is_video(entry: dict[str, Any]) -> bool:
    extension = _text(entry.get("ext"))
    if extension and extension.casefold() in VIDEO_EXTENSIONS:
        return True
    vcodec = entry.get("vcodec")
    return isinstance(vcodec, str) and vcodec not in {"", "none"}


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    thumbnail = _text(info.get("thumbnail"))
    if thumbnail:
        return thumbnail

    thumbnails = info.get("thumbnails")
    if not isinstance(thumbnails, list):
        return None

    candidates = [
        entry for entry in thumbnails if isinstance(entry, dict) and _text(entry.get("url"))
    ]
    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda entry: (_number(entry.get("width")) or 0) * (_number(entry.get("height")) or 0),
    )
    return _text(best.get("url"))


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _translate_ytdl_error(exc: YoutubeDLError) -> EnrichError:
    message = str(exc).strip() or exc.__class__.__name__
    normalized = message.casefold()
    if any(token in normalized for token in UNAVAILABLE_TOKENS):
        return EnrichUnavailable(message)
    if any(token in normalized for token in TRANSIENT_TOKENS):
        return EnrichTransient(message)
    return EnrichError(message)
