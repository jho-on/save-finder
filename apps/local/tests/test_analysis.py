"""Analysis stage tests with injected providers and a fake media preparation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from save_finder.analysis import analyze_item
from save_finder.config import Settings
from save_finder.media import MediaPrepResult
from save_finder.models import Item, PlatformSource, PostType
from save_finder.providers.base import TranscriptResult, TranscriptSegment
from save_finder.repositories.analysis import (
    aggregate_vectors,
    load_embedding,
    load_transcript_text,
    pack_vector,
    unpack_vector,
)


class FakeASR:
    name = "fake-asr"

    def __init__(self) -> None:
        self.paths: list[Path] = []

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        self.paths.append(audio_path)
        return TranscriptResult(
            language="en",
            text="a quick cooking tutorial",
            segments=(
                TranscriptSegment(start=0.0, end=1.5, text="a quick"),
                TranscriptSegment(start=1.5, end=3.0, text="cooking tutorial"),
            ),
            model=self.name,
        )


class FakeVision:
    name = "fake-vision"

    def __init__(self) -> None:
        self.batches: list[list[Path]] = []

    def embed_images(self, image_paths: Sequence[Path]) -> list[list[float]]:
        self.batches.append(list(image_paths))
        return [[3.0, 4.0] for _ in image_paths]

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.6, 0.8] for _ in texts]


@pytest.fixture()
def item(session: Session) -> Item:
    row = Item(
        platform="instagram",
        external_id="analysis-1",
        url="https://www.instagram.com/p/analysis-1/",
        source=PlatformSource.EXPORT,
        post_type=PostType.CAROUSEL,
        media_count=2,
    )
    session.add(row)
    session.flush()
    return row


def _workspace(tmp_path: Path, settings: Settings) -> Path:
    workspace = settings.paths.tmp / "analysis-workspace"
    workspace.mkdir(parents=True)
    (workspace / "audio.wav").write_bytes(b"fake-audio")
    (workspace / "piece_0.jpg").write_bytes(b"fake-image")
    return workspace


def test_analysis_persists_transcript_and_aggregated_embedding(
    tmp_path: Path,
    settings: Settings,
    session: Session,
    item: Item,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, settings)
    audio = workspace / "audio.wav"
    image = workspace / "piece_0.jpg"

    def fake_prepare(*args, **kwargs) -> MediaPrepResult:
        return MediaPrepResult(
            item_id=item.id,
            audio_path=audio,
            keyframe_path=None,
            thumb_path="thumbs/instagram-analysis.webp",
        )

    monkeypatch.setattr("save_finder.analysis.prepare_media", fake_prepare)
    asr = FakeASR()
    vision = FakeVision()

    result = analyze_item(workspace, item, session, settings, asr=asr, vision=vision)
    session.commit()
    session.refresh(item)

    assert result.transcript_saved is True
    assert result.embedding_saved is True
    assert result.error is None
    assert result.language == "en"
    assert asr.paths == [audio]
    assert vision.batches == [[image]]
    assert load_transcript_text(item) == "a quick cooking tutorial"
    assert item.thumb_path == "thumbs/instagram-analysis.webp"

    vector = load_embedding(item)
    assert vector is not None
    assert vector == pytest.approx([0.6, 0.8])


def test_analysis_records_provider_failure_without_crashing(
    settings: Settings,
    tmp_path: Path,
    session: Session,
    item: Item,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, settings)
    audio = workspace / "audio.wav"

    def fake_prepare(*args, **kwargs) -> MediaPrepResult:
        return MediaPrepResult(
            item_id=item.id,
            audio_path=audio,
            keyframe_path=None,
            thumb_path=None,
        )

    class BrokenASR:
        name = "broken"

        def transcribe(self, audio_path: Path) -> TranscriptResult:
            from save_finder.errors import ClassifierError

            raise ClassifierError("model unavailable")

    monkeypatch.setattr("save_finder.analysis.prepare_media", fake_prepare)

    result = analyze_item(
        workspace,
        item,
        session,
        settings,
        asr=BrokenASR(),
        vision=None,
    )

    assert result.transcript_saved is False
    assert result.embedding_saved is False
    assert result.error is not None
    assert "model unavailable" in result.error


def test_vector_roundtrip_and_aggregation() -> None:
    payload = pack_vector([1.0, -2.0, 0.5])
    assert unpack_vector(payload, 3) == pytest.approx([1.0, -2.0, 0.5])

    vector = aggregate_vectors([[3.0, 4.0], [3.0, 4.0]])
    assert vector == pytest.approx([0.6, 0.8])

    normalized = aggregate_vectors([[1.0, 0.0], [0.0, 1.0]])
    assert sum(value**2 for value in normalized) == pytest.approx(1.0)
