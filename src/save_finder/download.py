"""Download stage: all media pieces into a temporary workspace."""

from __future__ import annotations

import logging
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from save_finder.config import Settings
from save_finder.errors import DownloadError as DomainDownloadError
from save_finder.models import Item, MediaAsset, MediaKind, utc_now_iso

logger = logging.getLogger(__name__)

WORKSPACE_MAX_PIECES = 20
DISK_MARGIN_GB = 2.0


@dataclass(frozen=True, slots=True)
class PieceInfo:
    position: int
    kind: MediaKind
    source_url: str | None
    file_path: Path | None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    item_id: int
    workspace: Path
    pieces: tuple[PieceInfo, ...]
    cleaned: bool = False
    error: str | None = None


class WorkspaceGuard:
    """Guarantees removal of the workspace, even on timeout or crash."""

    def __init__(self, workspace: Path, settings: Settings) -> None:
        self.workspace = workspace
        self.settings = settings

    def __enter__(self) -> Path:
        self.workspace.mkdir(parents=True, exist_ok=True)
        _check_disk(self.settings)
        return self.workspace

    def __exit__(self, exc_type: object, *_: object) -> None:
        if self.workspace.exists():
            try:
                shutil.rmtree(self.workspace)
            except OSError as exc:
                logger.warning("workspace cleanup failed for %s: %s", self.workspace, exc)
        if exc_type is not None:
            _check_tmp_empty(self.settings)


def _check_disk(settings: Settings, margin_gb: float = DISK_MARGIN_GB) -> None:
    usage = shutil.disk_usage(str(settings.paths.tmp))
    free_gb = usage.free / (1024**3)
    if free_gb < margin_gb:
        raise DomainDownloadError(f"free disk {free_gb:.1f} GB below margin {margin_gb} GB")


def _check_tmp_empty(settings: Settings) -> None:
    tmp = settings.paths.tmp
    leftovers = [p for p in tmp.rglob("*") if p.is_file()]
    if leftovers:
        logger.warning("data/tmp has %d leftover files: %s", len(leftovers), leftovers[:5])


def download_item(
    item: Item,
    session: Session,
    settings: Settings,
    *,
    adapter_factory: Any | None = None,
    timeout: int | None = None,
) -> DownloadResult:
    """Download all pieces of one item into a temporary workspace.

    Video pieces are downloaded with ``yt-dlp``; image/carousel pieces use
    the best thumbnail URL from ``yt-dlp`` info. The workspace is removed
    in ``finally``; if it is missing at exit, the batch reports it.
    """
    timeout = timeout or settings.pipeline.download_timeout
    workspace = settings.paths.tmp / f"{item.platform}-{item.external_id}"
    item_id = item.id

    result = DownloadResult(
        item_id=item_id,
        workspace=workspace,
        pieces=(),
    )

    try:
        with WorkspaceGuard(workspace, settings) as ws:
            pieces = _download_pieces(
                item.url,
                ws,
                settings,
                timeout=timeout,
            )
            # Persist media asset records (no file path in current schema)
            if session is not None:
                for p in pieces:
                    session.merge(
                        MediaAsset(
                            item_id=item_id,
                            position=p.position,
                            kind=p.kind,
                            duration_seconds=p.duration_seconds,
                            width=p.width,
                            height=p.height,
                            created_at=utc_now_iso(),
                        )
                    )
                session.commit()
            result = DownloadResult(
                item_id=item_id,
                workspace=ws,
                pieces=pieces,
                cleaned=False,
            )
    except DomainDownloadError as exc:
        logger.warning("download failed item=%s: %s", item.external_id, exc)
        result = DownloadResult(
            item_id=item_id,
            workspace=workspace,
            pieces=(),
            cleaned=False,
            error=str(exc),
        )
    except Exception as exc:
        message = f"unexpected download failure: {exc.__class__.__name__}"
        logger.exception("unexpected download failure item=%s", item.external_id)
        result = DownloadResult(
            item_id=item_id,
            workspace=workspace,
            pieces=(),
            cleaned=False,
            error=message,
        )
    finally:
        # Explicit cleanup; WorkspaceGuard also tries, but this makes it
        # visible to callers that inspect the result.
        if workspace.exists():
            try:
                shutil.rmtree(workspace)
                result = DownloadResult(
                    item_id=result.item_id,
                    workspace=result.workspace,
                    pieces=result.pieces,
                    cleaned=True,
                    error=result.error,
                )
            except OSError as exc:
                logger.error("cleanup failed for %s: %s", workspace, exc)
        else:
            result = DownloadResult(
                item_id=result.item_id,
                workspace=result.workspace,
                pieces=result.pieces,
                cleaned=True,
                error=result.error,
            )

    # Final verification for the batch
    _check_tmp_empty(settings)
    logger.info(
        "download item=%s pieces=%d cleaned=%s error=%s",
        item.external_id,
        len(result.pieces),
        result.cleaned,
        result.error is not None,
    )
    return result


def _download_pieces(
    url: str,
    workspace: Path,
    settings: Settings,
    *,
    timeout: int,
) -> tuple[PieceInfo, ...]:
    info = _yt_info(url, settings)
    entries = info.get("entries")
    if entries is not None and isinstance(entries, list):
        pieces = entries
    else:
        pieces = [info] if info else []

    result: list[PieceInfo] = []
    for idx, entry in enumerate(pieces[:WORKSPACE_MAX_PIECES]):
        if not isinstance(entry, dict):
            continue
        position = idx
        piece_path: Path | None = None

        # Video pieces via yt-dlp download
        if _is_video_piece(entry):
            piece_path = _download_video_piece(entry, workspace, settings, timeout=timeout)
            result.append(
                PieceInfo(
                    position=position,
                    kind=MediaKind.VIDEO,
                    source_url=entry.get("webpage_url") or url,
                    file_path=piece_path,
                    duration_seconds=_number(entry.get("duration")),
                    width=_int(entry.get("width")),
                    height=_int(entry.get("height")),
                )
            )
        else:
            # Image / carousel piece: best thumbnail URL
            thumb_url = _best_thumb_url(entry) or _best_thumb_url(info)
            if thumb_url:
                piece_path = _download_image_piece(
                    thumb_url, workspace / f"piece_{position}.jpg", timeout=timeout
                )
            result.append(
                PieceInfo(
                    position=position,
                    kind=MediaKind.IMAGE,
                    source_url=thumb_url,
                    file_path=piece_path,
                    duration_seconds=None,
                    width=_int(entry.get("width")),
                    height=_int(entry.get("height")),
                )
            )

    # Random jitter between pieces (already between downloads via loop sleep)
    time.sleep(random.uniform(0, max(0.5, settings.pipeline.request_jitter_seconds)))
    return tuple(result)


def _yt_info(url: str, settings: Settings) -> dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": False,
        "ignore_no_formats_error": True,
        "cachedir": False,
        "noplaylist": False,
        "socket_timeout": settings.pipeline.download_timeout,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info if isinstance(info, dict) else {}


def _is_video_piece(entry: dict[str, Any]) -> bool:
    ext = _text(entry.get("ext"))
    if ext and ext.casefold() in {"mp4", "m4v", "mov", "webm", "mkv"}:
        return True
    formats = entry.get("formats")
    if isinstance(formats, list) and formats:
        return any(
            f.get("vcodec") and f.get("vcodec") not in ("none", "")
            for f in formats
            if isinstance(f, dict)
        )
    return False


def _download_video_piece(
    entry: dict[str, Any], workspace: Path, settings: Settings, *, timeout: int
) -> Path | None:
    # Use the entry URL for playlist items; for single video use the top-level url
    video_url = entry.get("webpage_url") or entry.get("url")
    if not video_url:
        return None
    destination = workspace / "video.mp4"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": False,
        "format": "bv*+ba/b",
        "outtmpl": str(workspace / "video.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": timeout,
        "max_filesize": 500 * 1024 * 1024,  # 500 MB guard per piece
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(video_url, download=True)
        # Find the produced file
        candidates = [
            p
            for p in workspace.iterdir()
            if p.is_file() and p.suffix in {".mp4", ".mkv", ".webm", ".mov"}
        ]
        best = max(candidates, key=lambda p: p.stat().st_size) if candidates else None
        if best is not None and best.name != "video.mp4":
            best.rename(destination)
        return destination if destination.exists() else (best if best else None)
    except DownloadError:
        raise
    except Exception as exc:
        raise DomainDownloadError(f"video download failed: {exc}") from exc


def _download_image_piece(url: str, destination: Path, *, timeout: int) -> Path | None:
    try:
        import urllib.request
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "SaveFinder/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(20 * 1024 * 1024)  # 20 MB guard
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination
    except Exception as exc:
        logger.warning("image download failed url=%s: %s", url, exc)
        return None


def _best_thumb_url(entry: dict[str, Any]) -> str | None:
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        best = max(
            (t for t in thumbnails if isinstance(t, dict) and t.get("url")),
            key=lambda t: (_int(t.get("width")) or 0) * (_int(t.get("height")) or 0),
            default=None,
        )
        if best is not None:
            return best.get("url")
    thumb = _text(entry.get("thumbnail"))
    if thumb:
        return thumb
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    n = _number(value)
    return int(n) if n is not None else None
