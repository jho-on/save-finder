from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session
from typer.testing import CliRunner

from save_finder.cli import app
from save_finder.models import ItemStatus
from save_finder.platforms.base import Metadata, SavedItem
from save_finder.repositories.items import get_item, import_saved_items


def test_cli_import_twice(settings, instagram_export: Path) -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["import", "instagram", str(instagram_export)])
    assert first.exit_code == 0, first.output
    assert "New" in first.stdout
    assert "4" in first.stdout

    second = runner.invoke(app, ["import", "instagram", str(instagram_export)])
    assert second.exit_code == 0, second.output
    assert "Existing" in second.stdout


def test_cli_import_zip(settings, instagram_zip: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["import", "instagram", str(instagram_zip)])

    assert result.exit_code == 0, result.output
    assert "New" in result.stdout
    assert "4" in result.stdout
    assert "Ignored" in result.stdout
    assert "2" in result.stdout
    assert list(settings.paths.tmp.iterdir()) == []


def test_cli_rejects_unknown_platform(settings, instagram_export: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["import", "youtube", str(instagram_export)])
    assert result.exit_code == 1
    assert "unknown platform" in result.stdout


def test_cli_enrich(session: Session, monkeypatch) -> None:
    import_saved_items(
        session,
        [
            SavedItem(
                platform="fake",
                external_id="fake-1",
                url="https://example.test/fake-1",
                source="export",
            )
        ],
    )
    session.commit()

    class FakeAdapter:
        slug = "fake"

        def enrich(self, item: SavedItem) -> Metadata:
            return Metadata(author="alice", post_type="image", media_count=1)

    monkeypatch.setattr("save_finder.enrich.get_adapter", lambda _slug: FakeAdapter())

    result = CliRunner().invoke(app, ["enrich", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert "Enriched 1 item(s)" in result.stdout
    assert "enriched" in result.stdout
    item = get_item(session, "fake", "fake-1")
    assert item is not None
    assert item.status == ItemStatus.ENRICHED


def test_cli_enrich_rejects_unknown_platform(settings) -> None:
    result = CliRunner().invoke(app, ["enrich", "--platform", "youtube"])

    assert result.exit_code == 1
    assert "unknown platform" in result.stdout
