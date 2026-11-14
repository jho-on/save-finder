from __future__ import annotations

import pytest
from yt_dlp.utils import DownloadError

from save_finder.errors import EnrichTransient, EnrichUnavailable
from save_finder.platforms.base import _metadata_from_info, _translate_ytdl_error


def test_metadata_for_carousel_sums_durations_and_picks_largest_thumb() -> None:
    info = {
        "uploader_id": "@alice",
        "description": "carousel caption",
        "entries": [
            {
                "ext": "mp4",
                "duration": 10,
                "thumbnails": [
                    {"url": "https://cdn.test/small.jpg", "width": 320, "height": 320},
                    {"url": "https://cdn.test/large.jpg", "width": 1080, "height": 1080},
                ],
            },
            {"ext": "mp4", "duration": 5.5},
            {"ext": "jpg"},
        ],
    }

    metadata = _metadata_from_info(info)

    assert metadata.author == "alice"
    assert metadata.caption == "carousel caption"
    assert metadata.post_type == "carousel"
    assert metadata.media_count == 3
    assert metadata.duration_seconds == 15.5
    assert metadata.thumbnail_url == "https://cdn.test/large.jpg"


def test_metadata_for_single_image() -> None:
    metadata = _metadata_from_info({"ext": "jpg", "description": "photo"})

    assert metadata.post_type == "image"
    assert metadata.media_count == 1
    assert metadata.duration_seconds is None


def test_metadata_for_image_post_without_formats() -> None:
    metadata = _metadata_from_info(
        {
            "ext": None,
            "formats": [],
            "thumbnails": [
                {"url": "https://cdn.test/photo-small.jpg", "width": 320, "height": 320},
                {"url": "https://cdn.test/photo.jpg", "width": 1080, "height": 1080},
            ],
        }
    )

    assert metadata.post_type == "image"
    assert metadata.media_count == 1
    assert metadata.thumbnail_url == "https://cdn.test/photo.jpg"


def test_metadata_without_payload_is_skipped() -> None:
    metadata = _metadata_from_info({"title": "empty", "formats": []})

    assert metadata.media_count == 0
    assert metadata.post_type is None


def test_metadata_for_single_video_uses_top_level_thumbnail() -> None:
    metadata = _metadata_from_info(
        {
            "ext": "mp4",
            "duration": 42,
            "thumbnail": "https://cdn.test/cover.jpg",
            "uploader": "bob",
        }
    )

    assert metadata.post_type == "video"
    assert metadata.media_count == 1
    assert metadata.duration_seconds == 42.0
    assert metadata.thumbnail_url == "https://cdn.test/cover.jpg"
    assert metadata.author == "bob"


def test_metadata_rejects_empty_carousel() -> None:
    with pytest.raises(EnrichUnavailable):
        _metadata_from_info({"entries": []})


@pytest.mark.parametrize(
    ("message", "error_type"),
    [
        ("ERROR: Private video", EnrichUnavailable),
        ("ERROR: This content is not available in your country", EnrichUnavailable),
        ("ERROR: HTTP Error 404: Not Found", EnrichUnavailable),
        ("ERROR: Unable to download webpage: timed out", EnrichTransient),
        ("HTTP Error 429: Too Many Requests", EnrichTransient),
    ],
)
def test_ytdlp_error_translation(message: str, error_type: type[Exception]) -> None:
    assert isinstance(_translate_ytdl_error(DownloadError(message)), error_type)
