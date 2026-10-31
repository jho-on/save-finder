from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from save_finder.repositories.items import count_items
from save_finder.web.app import create_app


def test_health(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["platforms"] == ["instagram"]


def test_import_page(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="dropzone"' in response.text
    assert "/static/import.js" in response.text
    assert f"{settings.import_config.max_archive_mb} MB" in response.text


def test_upload_zip(settings, session: Session, instagram_zip: Path) -> None:
    client = TestClient(create_app(settings))
    with instagram_zip.open("rb") as archive:
        response = client.post(
            "/api/import",
            files={"file": (instagram_zip.name, archive, "application/zip")},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["platform"] == "instagram"
    assert (body["new"], body["existing"], body["total"]) == (4, 0, 4)
    assert body["ignored"] == 2
    assert count_items(session) == 4
    assert list(settings.paths.tmp.iterdir()) == []


def test_upload_rejects_invalid_archive(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.post(
        "/api/import",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert "invalid ZIP" in response.json()["detail"]
    assert list(settings.paths.tmp.iterdir()) == []


def test_upload_rejects_unknown_platform(settings, instagram_zip: Path) -> None:
    client = TestClient(create_app(settings))
    with instagram_zip.open("rb") as archive:
        response = client.post(
            "/api/import",
            data={"platform": "youtube"},
            files={"file": (instagram_zip.name, archive, "application/zip")},
        )

    assert response.status_code == 400
    assert "unknown platform" in response.json()["detail"]
    assert list(settings.paths.tmp.iterdir()) == []
