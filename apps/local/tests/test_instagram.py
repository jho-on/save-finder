from __future__ import annotations

from pathlib import Path

import pytest

from save_finder.errors import UnsupportedURL
from save_finder.platforms.instagram import InstagramAdapter


@pytest.fixture()
def adapter() -> InstagramAdapter:
    return InstagramAdapter()


@pytest.mark.parametrize(
    ("url", "canonical", "external_id"),
    [
        (
            "https://www.instagram.com/p/ABC_123/?igsh=xyz",
            "https://www.instagram.com/p/ABC_123/",
            "ABC_123",
        ),
        (
            "https://instagram.com/reel/XYZ-9/",
            "https://www.instagram.com/reel/XYZ-9/",
            "XYZ-9",
        ),
        (
            "https://www.instagram.com/reels/XYZ-9",
            "https://www.instagram.com/reel/XYZ-9/",
            "XYZ-9",
        ),
        (
            "http://m.instagram.com/tv/TV_1/",
            "https://www.instagram.com/tv/TV_1/",
            "TV_1",
        ),
        (
            "https://www.instagram.com/some.user/p/Post42/?utm_source=x#frag",
            "https://www.instagram.com/p/Post42/",
            "Post42",
        ),
    ],
)
def test_normalize_url(
    adapter: InstagramAdapter, url: str, canonical: str, external_id: str
) -> None:
    assert adapter.normalize_url(url) == (canonical, external_id)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/p/ABC/",
        "https://www.instagram.com/stories/user/123/",
        "https://www.instagram.com/some.user/",
        "not-a-url",
        "ftp://instagram.com/p/ABC/",
    ],
)
def test_normalize_url_rejects_unsupported(adapter: InstagramAdapter, url: str) -> None:
    with pytest.raises(UnsupportedURL):
        adapter.normalize_url(url)


def test_parse_export_localized_and_deduplicated(
    adapter: InstagramAdapter, instagram_export: Path
) -> None:
    items = {item.external_id: item for item in adapter.parse_export(instagram_export)}

    assert set(items) == {"AAA111", "BBB222", "CCC333", "DDD444"}
    assert "ZZZ999" not in items  # comments.json is outside the saved folder
    assert len(adapter.skipped_urls) == 1
    assert adapter.duplicate_count == 1

    aaa = items["AAA111"]
    assert aaa.url == "https://www.instagram.com/p/AAA111/"
    assert aaa.author == "alice"
    assert aaa.collection == "Receitas"
    assert aaa.saved_at == "2023-07-22T04:26:40Z"
    assert aaa.source == "export"

    assert items["BBB222"].collection is None
    assert items["CCC333"].collection == "Receitas"
    assert items["DDD444"].collection == "Viagens"
    assert items["DDD444"].url == "https://www.instagram.com/tv/DDD444/"


def test_parse_export_is_deterministic(adapter: InstagramAdapter, instagram_export: Path) -> None:
    first = [item.external_id for item in adapter.parse_export(instagram_export)]
    second = [item.external_id for item in adapter.parse_export(instagram_export)]
    assert first == second


def test_parse_export_missing_path(tmp_path: Path, adapter: InstagramAdapter) -> None:
    from save_finder.errors import ExportImportError

    with pytest.raises(ExportImportError):
        adapter.parse_export(tmp_path / "missing")


def test_parse_current_export_format(adapter: InstagramAdapter, instagram_export_v2: Path) -> None:
    items = {item.external_id: item for item in adapter.parse_export(instagram_export_v2)}

    assert set(items) == {"V2AAA", "V2BBB"}
    assert adapter.skipped_urls == ["https://www.instagram.com/stories/someone/123/"]
    assert adapter.duplicate_count == 1
    assert adapter.unreadable_files == []

    aaa = items["V2AAA"]
    assert aaa.url == "https://www.instagram.com/reel/V2AAA/"
    assert aaa.author == "alice"
    assert aaa.collection is None
    assert aaa.saved_at == "2024-10-25T23:46:40Z"
    assert aaa.source == "export"

    bbb = items["V2BBB"]
    assert bbb.author == "bob"
    assert bbb.saved_at == "2024-10-25T23:48:20Z"
