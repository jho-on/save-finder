from __future__ import annotations

import pytest

from save_finder.config import Settings
from save_finder.errors import MediaError
from save_finder.media import _run_ffmpeg, _thumb_filename


def test_run_ffmpeg_raises_on_bad_args() -> None:
    with pytest.raises(MediaError, match="ffmpeg failed"):
        _run_ffmpeg(["-y", "-fake", "bad"])


def test_thumb_filename_is_deterministic(settings: Settings) -> None:
    from save_finder.db import make_engine, make_session_factory, session_scope
    from save_finder.models import Item

    with session_scope(make_session_factory(make_engine(settings))) as s:
        item = s.query(Item).filter(Item.external_id == "DeJ-_uAAuz4").first()
        if item:
            name = _thumb_filename(item)
            assert "instagram-" in name
            assert "bb08e37cc6" in name  # digest suffix matches actual thumb
