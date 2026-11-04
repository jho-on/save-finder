from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import Settings, load_settings
from save_finder.models import Item, ItemStatus, PlatformSource, PostType
from save_finder.repositories.tags import add_item_tags, replace_auto_tags, sync_taxonomy
from save_finder.web import api
from save_finder.web.app import create_app


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture()
def item(session: Session) -> Item:
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    sync_taxonomy(session, taxonomy)

    item = Item(
        platform="instagram",
        external_id="DeJ-test",
        url="https://www.instagram.com/p/DeJ-test/",
        source=PlatformSource.EXPORT,
        author="alice",
        caption="receitas de bolo",
        post_type=PostType.IMAGE,
        media_count=1,
        status=ItemStatus.ENRICHED,
    )
    session.add(item)
    session.flush()
    add_item_tags(session, item.id, ["receitas"])
    session.commit()
    return item


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["platforms"] == ["instagram"]


def test_pages_render(client: TestClient, item: Item) -> None:
    for path in ("/", "/library", "/tags", "/pipeline", f"/item/{item.id}"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html")


def test_item_detail_404(client: TestClient) -> None:
    assert client.get("/item/9999").status_code == 404


def test_api_items_and_detail(client: TestClient, item: Item) -> None:
    response = client.get("/api/items?status=enriched&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["external_id"] == item.external_id

    detail = client.get(f"/api/items/{item.id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "receitas de bolo"
    tags = {tag["slug"] for tag in detail.json()["tags"]}
    assert tags == {"comida-e-bebida", "receitas"}
    assert detail.json()["classification_method"] == "heuristic"


def test_item_exposes_ai_method_and_evidence(
    client: TestClient, item: Item, session: Session
) -> None:
    replace_auto_tags(
        session,
        item.id,
        ["receitas"],
        evidence="llm: receita de bolo caseiro",
    )
    session.commit()

    detail = client.get(f"/api/items/{item.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["classification_method"] == "ai"
    auto_tags = [tag for tag in body["tags"] if tag["source"] == "auto"]
    assert auto_tags
    assert all(tag["evidence"] == "llm: receita de bolo caseiro" for tag in auto_tags)

    page = client.get(f"/item/{item.id}")
    assert page.status_code == 200
    assert 'class="method-badge ai"' in page.text
    assert page.text.count("info-tip-popup") == 1
    assert "llm: receita de bolo caseiro" in page.text


def test_display_title_uses_caption() -> None:
    assert api.display_title("  Receita\ndo dia  ", "abc") == "Receita do dia"
    assert api.display_title(None, "abc") == "abc"
    assert api.display_title("", "abc") == "abc"


def test_api_items_filter_by_tag(client: TestClient, item: Item) -> None:
    response = client.get("/api/items?tag=receitas")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    empty = client.get("/api/items?tag=tecnologia")
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


def test_api_items_rejects_invalid_status(client: TestClient) -> None:
    assert client.get("/api/items?status=nope").status_code == 400


def test_api_search(client: TestClient, item: Item) -> None:
    response = client.get("/api/items/search?q=receitas&limit=3")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "receitas"
    assert data["total"] == 1
    assert data["items"][0]["id"] == item.id


def test_pipeline_start_and_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)
    response = client.post("/api/pipeline/enrich", data={"platform": "instagram"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"

    status = client.get(f"/api/pipeline/{body['job_id']}")
    assert status.status_code == 200
    assert status.json()["operation"] == "enrich"


def test_pipeline_unknown_job(client: TestClient) -> None:
    assert client.get("/api/pipeline/missing").status_code == 404


def test_pipeline_stats(client: TestClient, item: Item) -> None:
    response = client.get("/api/pipeline/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["general"]["total"] == 1
    assert body["enrich"]["target"] == 1


def test_shutdown_waits_for_background_tasks(settings: Settings) -> None:
    app = create_app(settings)
    started = threading.Event()
    finished = threading.Event()

    def task() -> None:
        started.set()
        threading.Event().wait(timeout=0.2)
        finished.set()

    with TestClient(app):
        api._start_background_task(
            task,
            app.state.background_tasks,
            app.state.background_tasks_lock,
        )
        assert started.wait(timeout=1)

    assert finished.is_set()
    assert not app.state.background_tasks


def test_pipeline_job_persists_across_restart(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/pipeline/enrich",
            data={"platform": "instagram"},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        status = client.get(f"/api/pipeline/{job_id}")
        assert status.json()["status"] == "running"

    app = create_app(settings)
    with TestClient(app) as client:
        data = client.get(f"/api/pipeline/{job_id}").json()

    assert data["job_id"] == job_id
    assert data["operation"] == "enrich"
    assert data["status"] == "interrupted"
    assert data["finished_at"] is not None
    assert data["error"] == "Server restarted while job was running"
