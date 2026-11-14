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


def test_library_lists_all_items_and_filters_by_status(
    client: TestClient, item: Item, session: Session
) -> None:
    imported = Item(
        platform="instagram",
        external_id="DeJ-imported",
        url="https://www.instagram.com/p/DeJ-imported/",
        source=PlatformSource.EXPORT,
        status=ItemStatus.PENDING,
    )
    session.add(imported)
    session.commit()

    page = client.get("/library")
    assert page.status_code == 200
    assert "status=classified" not in page.text
    assert 'data-status="pending"' in page.text
    assert 'data-status="classified"' in page.text
    assert "Importado" in page.text

    data = client.get("/api/items?limit=10").json()
    assert {row["id"] for row in data["items"]} == {item.id, imported.id}


def test_api_items_and_search_filter_by_status(
    client: TestClient, item: Item, session: Session
) -> None:
    imported = Item(
        platform="instagram",
        external_id="DeJ-imported",
        url="https://www.instagram.com/p/DeJ-imported/",
        source=PlatformSource.EXPORT,
        status=ItemStatus.PENDING,
    )
    session.add(imported)
    session.commit()
    from save_finder.repositories.search import rebuild_search_index

    rebuild_search_index(session)
    session.commit()

    filtered = client.get("/api/items?status=pending").json()
    assert filtered["total"] == 1
    assert [row["id"] for row in filtered["items"]] == [imported.id]

    search = client.get("/api/items/search?q=DeJ-imported&status=pending").json()
    assert search["total"] == 1
    assert search["items"][0]["id"] == imported.id

    other_status = client.get("/api/items/search?q=DeJ-imported&status=classified").json()
    assert other_status["total"] == 0


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


def test_api_search_filters_by_tag(client: TestClient, item: Item) -> None:
    matching = client.get("/api/items/search?q=bolo&tag=receitas")
    assert matching.status_code == 200
    assert matching.json()["total"] == 1

    other = client.get("/api/items/search?q=bolo&tag=tecnologia")
    assert other.status_code == 200
    assert other.json()["total"] == 0


def test_tag_page_lists_specific_filters(client: TestClient, item: Item) -> None:
    response = client.get("/tag/comida-e-bebida")
    assert response.status_code == 200
    assert 'data-tag="comida-e-bebida"' in response.text
    assert 'data-tag="receitas"' in response.text
    assert "Receitas" in response.text


def test_api_update_item_tags_keeps_automatic_tags(client: TestClient, item: Item) -> None:
    response = client.put(
        f"/api/items/{item.id}/tags",
        json={"tags": ["receitas", "drinks"]},
    )
    assert response.status_code == 200

    body = response.json()
    tags = {tag["slug"]: tag["source"] for tag in body["tags"]}
    assert tags == {
        "comida-e-bebida": "manual",
        "receitas": "auto",
        "drinks": "manual",
    }
    assert body["excluded_tags"] == []


def test_api_update_item_tags_records_exclusions(client: TestClient, item: Item) -> None:
    response = client.put(
        f"/api/items/{item.id}/tags",
        json={"tags": ["comida-e-bebida"]},
    )
    assert response.status_code == 200

    detail = client.get(f"/api/items/{item.id}").json()
    assert {tag["slug"] for tag in detail["tags"]} == {"comida-e-bebida"}
    assert detail["excluded_tags"] == ["receitas"]

    listing = client.get("/api/items?status=enriched").json()
    assert listing["items"][0]["excluded_tags"] == ["receitas"]


def test_api_update_item_tags_rejects_invalid_selection(client: TestClient, item: Item) -> None:
    unknown = client.put(f"/api/items/{item.id}/tags", json={"tags": ["nope"]})
    assert unknown.status_code == 400

    two_broads = client.put(
        f"/api/items/{item.id}/tags",
        json={"tags": ["comida-e-bebida", "tecnologia"]},
    )
    assert two_broads.status_code == 400

    assert client.put("/api/items/9999/tags", json={"tags": []}).status_code == 404


def test_api_update_item_tags_removing_all_reverts_classified_status(
    client: TestClient, item: Item, session: Session
) -> None:
    from save_finder.models import ItemStatus

    item.status = ItemStatus.CLASSIFIED
    session.commit()

    response = client.put(f"/api/items/{item.id}/tags", json={"tags": []})
    assert response.status_code == 200
    assert response.json()["status"] == "analyzed"


def test_item_page_exposes_tag_editor(client: TestClient, item: Item) -> None:
    page = client.get(f"/item/{item.id}")
    assert page.status_code == 200
    assert "openTagEditor" in page.text
    assert 'id="tag-editor"' in page.text
    assert "broad-switch-modal" in page.text


def test_pipeline_run_accepts_reclassify_options(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)
    response = client.post(
        "/api/pipeline/run",
        data={
            "platform": "instagram",
            "reclassify": "true",
            "restore_removed": "true",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["operation"] == "pipeline"

    job = client.get(f"/api/pipeline/{body['job_id']}").json()
    assert job["params"]["reclassify"] is True
    assert job["params"]["restore_removed"] is True


def test_pipeline_classify_options_are_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)
    response = client.post(
        "/api/pipeline/classify",
        data={"platform": "instagram", "restore_removed": "true"},
    )
    assert response.status_code == 200
    job = client.get(f"/api/pipeline/{response.json()['job_id']}").json()
    assert job["params"]["restore_removed"] is True


def test_api_taxonomy_exposes_closed_tag_list(client: TestClient) -> None:
    response = client.get("/api/taxonomy")
    assert response.status_code == 200

    body = response.json()
    broad = [tag for tag in body["tags"] if tag["level"] == "broad"]
    specific = [tag for tag in body["tags"] if tag["level"] == "specific"]
    assert body["version"] == 1
    assert any(tag["slug"] == "comida-e-bebida" for tag in broad)
    assert any(tag["slug"] == "receitas" and tag["parent"] == "comida-e-bebida" for tag in specific)


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
    assert client.post("/api/pipeline/missing/cancel").status_code == 404


def test_pipeline_cancel_running_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)
    started = client.post("/api/pipeline/enrich", data={"platform": "instagram"})
    job_id = started.json()["job_id"]

    response = client.post(f"/api/pipeline/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "status": "cancelling",
        "cancelled": True,
    }
    assert client.app.state.job_cancel_events[job_id].is_set()
    assert client.get(f"/api/pipeline/{job_id}").json()["status"] == "cancelling"


def test_pipeline_cancel_finished_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "_start_background_task", lambda *args, **kwargs: None)
    started = client.post("/api/pipeline/classify", data={"platform": "instagram"})
    job_id = started.json()["job_id"]
    api._update_job(job_id, client.app.state.session_factory, status="done")

    response = client.post(f"/api/pipeline/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "status": "done",
        "cancelled": False,
    }


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
