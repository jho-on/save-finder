from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from save_finder.config import Settings, ensure_directories, load_settings
from save_finder.db import make_engine, make_session_factory, session_scope, upgrade_database

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SAVE_FINDER_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SAVE_FINDER_DATA_DIR", str(tmp_path / "data"))
    settings = load_settings()
    ensure_directories(settings)
    upgrade_database(settings)
    return settings


@pytest.fixture()
def session_factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(settings)
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_scope(session_factory) as session:
        yield session


@pytest.fixture()
def instagram_export() -> Path:
    return FIXTURES / "instagram" / "export"


@pytest.fixture()
def instagram_export_v2() -> Path:
    return FIXTURES / "instagram" / "export_v2"


@pytest.fixture()
def instagram_zip(tmp_path: Path) -> Path:
    """Official-export style ZIP with unrelated files mixed in."""
    archive = tmp_path / "instagram-jh0n-2026-10-29-YH8Dbtk7.zip"
    root = "your_instagram_activity"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name in ("saved_posts.json", "saved_collections.json"):
            source = FIXTURES / "instagram" / "export" / "saved" / name
            zip_file.write(source, f"{root}/saved/{name}")
        zip_file.writestr(
            f"{root}/comments/saved_comments.json",
            '{"comments_media_comments": [{"href": "https://www.instagram.com/p/ZZZ999/"}]}',
        )
        zip_file.writestr(f"{root}/likes/liked_posts.json", "[]")
        zip_file.writestr("README.txt", "export metadata")
    return archive
