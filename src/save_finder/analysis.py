"""Analysis stage: transcribe audio and aggregate visual embeddings.

This runs while the downloaded workspace still exists (download deletes it
afterwards). Only the derived transcript and embedding are persisted; original
media never survives the stage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session

from save_finder.config import Settings
from save_finder.errors import ClassifierError
from save_finder.media import prepare_media
from save_finder.models import Item, utc_now_iso
from save_finder.providers.base import ASRProvider, VisionProvider
from save_finder.providers.factory import build_asr, build_vision
from save_finder.repositories.analysis import (
    aggregate_vectors,
    save_embedding,
    save_transcript,
)

logger = logging.getLogger(__name__)

_UNSET = object()
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    item_id: int
    transcript_saved: bool = False
    embedding_saved: bool = False
    language: str | None = None
    transcript_chars: int = 0
    embedding_dimension: int = 0
    error: str | None = None

    @property
    def has_signals(self) -> bool:
        return self.transcript_saved or self.embedding_saved


def analyze_item(
    workspace: Path,
    item: Item,
    session: Session,
    settings: Settings,
    *,
    asr: object = _UNSET,
    vision: object = _UNSET,
) -> AnalysisResult:
    """Extract every signal available in the workspace and persist it.

    ``asr`` and ``vision`` may be injected (tests or alternate callers). When
    omitted, providers come from ``config/settings.toml``. Passing ``None``
    explicitly disables that signal.
    """
    asr_provider = build_asr(settings) if asr is _UNSET else cast(ASRProvider | None, asr)
    vision_provider = (
        build_vision(settings) if vision is _UNSET else cast(VisionProvider | None, vision)
    )

    prep = prepare_media(workspace, item, settings, session=session)
    if prep.thumb_path:
        item.thumb_path = prep.thumb_path

    errors: list[str] = []
    transcript_saved = False
    embedding_saved = False
    language: str | None = None
    transcript_chars = 0
    embedding_dimension = 0

    if prep.error:
        errors.append(prep.error)

    if prep.audio_path is not None and prep.audio_path.exists() and asr_provider is not None:
        try:
            transcript = asr_provider.transcribe(prep.audio_path)
            if transcript.text.strip():
                save_transcript(session, item.id, transcript)
                transcript_saved = True
                language = transcript.language
                transcript_chars = len(transcript.text)
            else:
                logger.info("empty transcript item=%s", item.external_id)
        except ClassifierError as exc:
            errors.append(str(exc))
            logger.warning("transcription failed item=%s: %s", item.external_id, exc)

    images = _collect_images(workspace, prep.keyframe_path)
    if images and vision_provider is not None:
        try:
            vectors = vision_provider.embed_images(images)
            aggregated = aggregate_vectors(vectors)
            if aggregated:
                save_embedding(session, item.id, aggregated, model=vision_provider.name)
                embedding_saved = True
                embedding_dimension = len(aggregated)
        except ClassifierError as exc:
            errors.append(str(exc))
            logger.warning("visual embedding failed item=%s: %s", item.external_id, exc)

    item.analyzed_at = utc_now_iso()
    item.updated_at = item.analyzed_at
    if errors:
        item.error = "; ".join(errors)[:2000]
    elif item.error and not prep.error:
        item.error = None
    session.flush()

    result = AnalysisResult(
        item_id=item.id,
        transcript_saved=transcript_saved,
        embedding_saved=embedding_saved,
        language=language,
        transcript_chars=transcript_chars,
        embedding_dimension=embedding_dimension,
        error="; ".join(errors) if errors else None,
    )
    logger.info(
        "analyze item=%s transcript=%s embedding=%s dims=%d errors=%d",
        item.external_id,
        transcript_saved,
        embedding_saved,
        embedding_dimension,
        len(errors),
    )
    return result


def _collect_images(workspace: Path, keyframe: Path | None) -> list[Path]:
    images: list[Path] = []
    if keyframe is not None and keyframe.exists():
        images.append(keyframe)
    for path in sorted(workspace.iterdir()):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        if keyframe is not None and path == keyframe:
            continue
        images.append(path)
    return images
