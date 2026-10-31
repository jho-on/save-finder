from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from save_finder.config import ImportConfig, Settings
from save_finder.errors import ExportImportError
from save_finder.importer import copy_upload, import_export, materialize_export
from save_finder.models import Item
from save_finder.repositories.items import count_items

LIMITS = ImportConfig()


def _import(session: Session, settings: Settings, path: Path):
    return import_export(
        session,
        platform="instagram",
        path=path,
        tmp_dir=settings.paths.tmp,
        limits=LIMITS,
    )


def _zip_with(tmp_path: Path, members: dict[str, str]) -> Path:
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        for name, content in members.items():
            zip_file.writestr(name, content)
    return archive


def test_import_zip_is_idempotent(
    session: Session, settings: Settings, instagram_zip: Path
) -> None:
    first = _import(session, settings, instagram_zip)
    assert first.platform == "instagram"
    assert (first.new, first.existing, first.total) == (4, 0, 4)
    assert first.ignored == 2  # one unsupported URL and one duplicate
    assert count_items(session) == 4

    second = _import(session, settings, instagram_zip)
    assert (second.new, second.existing) == (0, 4)
    assert count_items(session) == 4


def test_import_zip_ignores_unrelated_activity(
    session: Session, settings: Settings, instagram_zip: Path
) -> None:
    _import(session, settings, instagram_zip)

    external_ids = set(session.scalars(Item.__table__.select().with_only_columns(Item.external_id)))
    assert external_ids == {"AAA111", "BBB222", "CCC333", "DDD444"}
    assert "ZZZ999" not in external_ids


def test_import_zip_cleans_temporary_files(
    session: Session, settings: Settings, instagram_zip: Path
) -> None:
    _import(session, settings, instagram_zip)

    assert list(settings.paths.tmp.iterdir()) == []


def test_import_zip_without_saved_json(
    session: Session, settings: Settings, tmp_path: Path
) -> None:
    archive = _zip_with(
        tmp_path,
        {
            "your_instagram_activity/comments/comments.json": "[]",
            "your_instagram_activity/likes/liked_posts.json": "[]",
        },
    )

    with pytest.raises(ExportImportError, match="no saved JSON"):
        _import(session, settings, archive)
    assert list(settings.paths.tmp.iterdir()) == []


def test_import_zip_rejects_path_traversal(
    session: Session, settings: Settings, tmp_path: Path
) -> None:
    archive = _zip_with(tmp_path, {"../saved/evil.json": "[]"})

    with pytest.raises(ExportImportError, match="unsafe path"):
        _import(session, settings, archive)
    assert not (tmp_path.parent / "evil.json").exists()


def test_import_rejects_bad_zip(session: Session, settings: Settings, tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    with pytest.raises(ExportImportError, match="invalid ZIP"):
        _import(session, settings, archive)


def test_materialize_directory_passes_through(settings: Settings, instagram_export: Path) -> None:
    with materialize_export(instagram_export, tmp_dir=settings.paths.tmp, limits=LIMITS) as source:
        assert source == instagram_export


def test_copy_upload_enforces_size_limit(tmp_path: Path) -> None:
    dest = tmp_path / "upload.zip"

    with pytest.raises(ExportImportError, match="limit"):
        copy_upload(io.BytesIO(b"x" * 32), dest, max_bytes=16)

    assert not dest.exists()


def test_copy_upload_rejects_empty_file(tmp_path: Path) -> None:
    dest = tmp_path / "upload.zip"

    with pytest.raises(ExportImportError, match="empty"):
        copy_upload(io.BytesIO(b""), dest, max_bytes=16)

    assert not dest.exists()
