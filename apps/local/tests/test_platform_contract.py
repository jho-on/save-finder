from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.models import Item
from save_finder.platforms.base import Metadata, SavedItem
from save_finder.repositories.items import count_items, import_saved_items


class FakeAdapter:
    """Minimal adapter proving the core does not depend on Instagram."""

    slug = "fake"

    def parse_export(self, path: Path) -> Iterable[SavedItem]:
        yield SavedItem(
            platform=self.slug,
            external_id="fake-1",
            url="https://example.test/media/fake-1",
            source="export",
            author="someone",
            collection="Favorites",
            saved_at="2026-01-01T00:00:00Z",
        )

    def normalize_url(self, url: str) -> tuple[str, str]:
        return url, "fake-1"

    def enrich(self, item: SavedItem) -> Metadata:
        return Metadata(author=item.author, post_type="image", media_count=1)

    def download(self, item: SavedItem, dest: Path) -> Path:
        return dest


def test_core_imports_any_platform(session: Session, tmp_path: Path) -> None:
    adapter = FakeAdapter()
    items = list(adapter.parse_export(tmp_path))
    stats = import_saved_items(session, items)

    assert stats.new == 1
    assert count_items(session, platform="fake") == 1

    row = session.scalar(select(Item).where(Item.platform == "fake"))
    assert row is not None
    assert row.external_id == "fake-1"
    assert row.collection == "Favorites"
    assert row.url == "https://example.test/media/fake-1"
