from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from save_finder.config import Settings
from save_finder.enrich import download_thumbnail, enrich_items
from save_finder.errors import EnrichError, EnrichTransient, EnrichUnavailable
from save_finder.models import ItemStatus, PostType
from save_finder.platforms.base import Metadata, SavedItem
from save_finder.repositories.items import get_item, import_saved_items


class ScriptedAdapter:
    slug = "fake"

    def __init__(self, actions: dict[str, object]) -> None:
        self.actions = actions
        self.calls: dict[str, int] = {external_id: 0 for external_id in actions}

    def enrich(self, item: SavedItem) -> Metadata:
        self.calls[item.external_id] += 1
        action = self.actions[item.external_id]
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(self.calls[item.external_id])
        assert isinstance(action, Metadata)
        return action


def _seed(session: Session, external_ids: tuple[str, ...] = ("one",)) -> None:
    import_saved_items(
        session,
        [
            SavedItem(
                platform="fake",
                external_id=external_id,
                url=f"https://example.test/{external_id}",
                source="export",
            )
            for external_id in external_ids
        ],
    )
    session.commit()


def _fast(settings: Settings) -> Settings:
    return replace(
        settings,
        pipeline=replace(
            settings.pipeline,
            request_jitter_seconds=0.0,
            retry_backoff_seconds=0.0,
        ),
    )


def _write_thumbnail(url: str, dest_stem: Path, *, timeout: float, max_bytes: int) -> Path:
    path = dest_stem.with_suffix(".jpg")
    path.write_bytes(b"thumbnail")
    return path


def test_enrich_persists_metadata_and_local_thumbnail(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter(
        {
            "one": Metadata(
                author="alice",
                caption="a caption",
                post_type="carousel",
                media_count=2,
                duration_seconds=12.5,
                thumbnail_url="https://cdn.example.test/thumb.jpg",
            )
        }
    )

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert report.total == 1
    outcome = report.outcomes[0]
    assert outcome.status == ItemStatus.ENRICHED.value
    assert outcome.thumbnail_saved is True

    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.ENRICHED
    assert item.author == "alice"
    assert item.caption == "a caption"
    assert item.post_type == PostType.CAROUSEL
    assert item.media_count == 2
    assert item.duration_seconds == 12.5
    assert item.thumb_path is not None
    assert item.thumb_path.startswith("thumbs/")
    assert not item.thumb_path.startswith("http")
    assert (settings.paths.data / item.thumb_path).is_file()
    assert item.enriched_at is not None
    assert item.error is None


def test_enrich_is_idempotent_for_enriched_items(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter({"one": Metadata(post_type="image", media_count=1)})

    first = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )
    second = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert first.total == 1
    assert second.total == 0
    assert adapter.calls["one"] == 1


def test_enrich_marks_private_post_unavailable(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter({"one": EnrichUnavailable("private post")})

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert report.outcomes[0].status == ItemStatus.UNAVAILABLE.value
    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.UNAVAILABLE
    assert item.error is None


def test_enrich_marks_failure_as_error(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter({"one": EnrichError("extractor changed")})

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert report.outcomes[0].status == ItemStatus.ERROR.value
    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.ERROR
    assert item.error == "extractor changed"


def test_enrich_retries_transient_failures(session: Session, settings: Settings) -> None:
    _seed(session)

    def flaky(attempt: int) -> Metadata:
        if attempt < 3:
            raise EnrichTransient("timeout")
        return Metadata(post_type="video", media_count=1)

    adapter = ScriptedAdapter({"one": flaky})

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert report.outcomes[0].status == ItemStatus.ENRICHED.value
    assert adapter.calls["one"] == 3


def test_enrich_marks_item_without_media_as_skipped(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter({"one": Metadata(post_type=None, media_count=0)})

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert report.outcomes[0].status == ItemStatus.SKIPPED.value
    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.SKIPPED


def test_enrich_retry_errors_can_recover(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter({"one": EnrichError("temporary parser issue")})

    first = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )
    skipped = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    adapter.actions["one"] = Metadata(post_type="image", media_count=1)
    recovered = enrich_items(
        session,
        _fast(settings),
        retry_errors=True,
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=_write_thumbnail,
    )

    assert first.outcomes[0].status == ItemStatus.ERROR.value
    assert skipped.total == 0
    assert recovered.outcomes[0].status == ItemStatus.ENRICHED.value
    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.ENRICHED
    assert item.error is None


def test_thumbnail_failure_keeps_metadata(session: Session, settings: Settings) -> None:
    _seed(session)
    adapter = ScriptedAdapter(
        {
            "one": Metadata(
                post_type="image",
                media_count=1,
                thumbnail_url="https://cdn.example.test/thumb.jpg",
            )
        }
    )

    def failing_fetcher(url: str, dest_stem: Path, *, timeout: float, max_bytes: int) -> Path:
        raise EnrichError("thumbnail unavailable")

    report = enrich_items(
        session,
        _fast(settings),
        adapter_factory=lambda _platform: adapter,
        thumbnail_fetcher=failing_fetcher,
    )

    assert report.outcomes[0].status == ItemStatus.ENRICHED.value
    assert report.outcomes[0].thumbnail_saved is False
    item = get_item(session, "fake", "one")
    assert item is not None
    assert item.status == ItemStatus.ENRICHED
    assert item.thumb_path is None


def test_download_thumbnail_is_atomic_and_uses_content_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResponse:
        def __init__(self) -> None:
            from email.message import Message

            self.headers = Message()
            self.headers["Content-Type"] = "image/png"

        def read(self, size: int) -> bytes:
            return b"png-bytes"[:size]

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request, timeout: float):
        assert request.full_url == "https://cdn.example.test/thumb"
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setattr("save_finder.enrich.urlopen", fake_urlopen)

    path = download_thumbnail(
        "https://cdn.example.test/thumb",
        tmp_path / "thumb",
        timeout=5,
    )

    assert path == tmp_path / "thumb.png"
    assert path.read_bytes() == b"png-bytes"
    assert not (tmp_path / "thumb.png.part").exists()


def test_download_thumbnail_rejects_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResponse:
        def __init__(self) -> None:
            from email.message import Message

            self.headers = Message()
            self.headers["Content-Type"] = "image/jpeg"

        def read(self, size: int) -> bytes:
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("save_finder.enrich.urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(EnrichError, match="exceeds"):
        download_thumbnail(
            "https://cdn.example.test/thumb",
            tmp_path / "thumb",
            timeout=5,
            max_bytes=4,
        )


def test_download_thumbnail_rejects_non_http_url(tmp_path: Path) -> None:
    with pytest.raises(EnrichError, match="http"):
        download_thumbnail("file:///etc/passwd", tmp_path / "thumb", timeout=5)
