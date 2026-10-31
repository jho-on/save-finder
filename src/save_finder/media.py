"""Media preparation: audio extraction, keyframes and definitive thumbnail."""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from save_finder.config import Settings
from save_finder.errors import MediaError
from save_finder.models import Item

logger = logging.getLogger(__name__)

THUMB_FORMAT = "webp"
AUDIO_FORMAT = "wav"
AUDIO_SAMPLE_RATE = 16000
KEYFRAME_FORMAT = "jpg"


@dataclass(frozen=True, slots=True)
class MediaPrepResult:
    item_id: int
    audio_path: Path | None
    keyframe_path: Path | None
    thumb_path: str | None  # relative to settings.paths.data
    error: str | None = None


def prepare_media(
    workspace: Path,
    item: Item,
    settings: Settings,
    *,
    session: Session | None = None,
) -> MediaPrepResult:
    """Produce audio, keyframe and definitive thumbnail from the workspace.

    The workspace must still contain the downloaded pieces. After this
    function returns, the caller (usually ``download``) removes it.
    """
    video_file = _find_video(workspace)
    image_file = _find_image(workspace)

    audio_path: Path | None = None
    keyframe_path: Path | None = None
    thumb_path_str: str | None = None

    try:
        # Audio from video
        if video_file is not None:
            audio_path = workspace / f"audio.{AUDIO_FORMAT}"
            _run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(video_file),
                    "-vn",
                    "-ar",
                    str(AUDIO_SAMPLE_RATE),
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(audio_path),
                ],
                timeout=60,
            )

            # One keyframe near midpoint (or first 2s if very short)
            duration = _ffprobe_duration(video_file)
            seek = min(2.0, (duration / 2) if duration and duration > 4 else 1.0)
            keyframe_path = workspace / f"keyframe.{KEYFRAME_FORMAT}"
            _run_ffmpeg(
                [
                    "-y",
                    "-ss",
                    f"{seek:.2f}",
                    "-i",
                    str(video_file),
                    "-vframes",
                    "1",
                    "-q:v",
                    "2",
                    str(keyframe_path),
                ],
                timeout=30,
            )

        # Thumbnail definitive from best source
        source_for_thumb = (
            keyframe_path
            if keyframe_path and keyframe_path.exists()
            else image_file
            if image_file and image_file.exists()
            else video_file
            if video_file and video_file.exists()
            else None
        )
        if source_for_thumb is not None:
            thumb_path = settings.paths.thumbs / _thumb_filename(item)
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            _run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(source_for_thumb),
                    "-vf",
                    "scale='min(640,iw)':'min(640,ih)':force_original_aspect_ratio=decrease",
                    "-c:v",
                    "libwebp",
                    "-q:v",
                    "75",
                    "-loop",
                    "0",
                    "-an",
                    str(thumb_path.with_suffix(f".{THUMB_FORMAT}")),
                ],
                timeout=30,
            )
            thumb_path_str = (
                thumb_path.with_suffix(f".{THUMB_FORMAT}")
                .relative_to(settings.paths.data)
                .as_posix()
            )

        if session is not None and audio_path is not None and audio_path.exists():
            # Persist a note that audio exists (no separate table yet)
            session.flush()

    except Exception as exc:
        message = f"media prep failed: {exc.__class__.__name__}: {exc}"
        logger.warning("media prep item=%s: %s", item.external_id, message)
        return MediaPrepResult(
            item_id=item.id,
            audio_path=audio_path,
            keyframe_path=keyframe_path,
            thumb_path=thumb_path_str,
            error=message,
        )

    return MediaPrepResult(
        item_id=item.id,
        audio_path=audio_path,
        keyframe_path=keyframe_path,
        thumb_path=thumb_path_str,
    )


def _find_video(workspace: Path) -> Path | None:
    candidates = [
        p
        for p in workspace.iterdir()
        if p.is_file() and p.suffix in {".mp4", ".mkv", ".webm", ".mov"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _find_image(workspace: Path) -> Path | None:
    candidates = [
        p
        for p in workspace.iterdir()
        if p.is_file() and p.suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _thumb_filename(item: Item) -> str:
    digest = hashlib.sha1(f"{item.platform}:{item.external_id}".encode()).hexdigest()[:10]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.external_id)[:60]
    return f"{item.platform}-{safe_id}-{digest}"


def _ffprobe_duration(video_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        val = float(result.stdout.strip())
        return val if val > 0 else None
    except Exception:
        return None


def _run_ffmpeg(args: Sequence[str], *, timeout: int = 60) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg timeout after {timeout}s") from exc
    if result.returncode != 0:
        stderr = result.stderr[-500:] if result.stderr else "no output"
        raise MediaError(f"ffmpeg failed (exit {result.returncode}): {stderr}")
