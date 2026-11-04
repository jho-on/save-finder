"""FastAPI application factory.

The web interface provides the library, pipeline and taxonomy views.
The JSON API handles imports and item processing.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker

from save_finder import __version__
from save_finder.config import Settings, ensure_directories, load_settings
from save_finder.db import make_engine, make_session_factory, session_scope
from save_finder.models import Item, ItemTag, PipelineJob, Tag, TagLevel, utc_now_iso
from save_finder.platforms.registry import available_platforms
from save_finder.web import api as web_api

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ensure_directories(settings)

    engine = make_engine(settings)
    session_factory: sessionmaker[Session] = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if inspect(engine).has_table("pipeline_jobs"):
            with session_scope(session_factory) as session:
                session.execute(
                    update(PipelineJob)
                    .where(PipelineJob.status == "running")
                    .values(
                        status="interrupted",
                        finished_at=utc_now_iso(),
                        error="Server restarted while job was running",
                    )
                )
        else:
            logger.warning("Database not initialized; run `save-finder db init`.")

        try:
            yield
        finally:
            app.state.shutdown_event.set()

            while True:
                with app.state.background_tasks_lock:
                    tasks = list(app.state.background_tasks)

                if not tasks:
                    break

                for task in tasks:
                    task.join()

            engine.dispose()

    app = FastAPI(
        title="SaveFinder API",
        version=__version__,
        description="Local library for media saved on social networks.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.background_tasks = set()
    app.state.background_tasks_lock = threading.Lock()
    app.state.shutdown_event = threading.Event()

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.mount(
        "/data/thumbs",
        StaticFiles(directory=str(settings.paths.thumbs)),
        name="thumbs",
    )

    @app.get("/item/{item_id}", response_class=HTMLResponse, include_in_schema=False)
    def item_detail(request: Request, item_id: int) -> HTMLResponse:
        with session_scope(app.state.session_factory) as session:
            item = session.get(Item, item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="item not found")

            rows = session.execute(
                select(ItemTag, Tag)
                .join(Tag, Tag.id == ItemTag.tag_id)
                .where(ItemTag.item_id == item_id)
                .order_by(Tag.level, Tag.name)
            ).all()
            tag_list = [
                {
                    "slug": tag.slug,
                    "name": tag.name,
                    "confidence": item_tag.confidence,
                    "source": item_tag.source.value,
                    "evidence": item_tag.evidence,
                }
                for item_tag, tag in rows
            ]
            method = web_api.classification_method(tag_list)
            evidence = next(
                (tag["evidence"] for tag in tag_list if tag["evidence"]),
                None,
            )
            title = web_api.display_title(item.caption, item.external_id)

        return templates.TemplateResponse(
            request=request,
            name="item.html",
            context={
                "app_name": settings.app_name,
                "item": item,
                "title": title,
                "tags": tag_list,
                "classification_method": method,
                "classification_evidence": evidence,
            },
        )

    @app.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
    def pipeline(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={"app_name": settings.app_name, "page": "pipeline"},
        )

    @app.get("/tags", response_class=HTMLResponse, include_in_schema=False)
    def tags(request: Request) -> HTMLResponse:
        with session_scope(app.state.session_factory) as session:
            rows = session.execute(
                select(Tag, func.count(ItemTag.item_id).label("count"))
                .join(ItemTag, ItemTag.tag_id == Tag.id)
                .where(Tag.level == TagLevel.BROAD)
                .group_by(Tag.id)
                .order_by(func.count(ItemTag.item_id).desc(), Tag.name)
            ).all()

            tags_data = []
            for tag, count in rows[:20]:
                thumbs = session.scalars(
                    select(Item.thumb_path)
                    .join(ItemTag, Item.id == ItemTag.item_id)
                    .where(ItemTag.tag_id == tag.id)
                    .limit(4)
                ).all()
                tags_data.append(
                    {
                        "slug": tag.slug,
                        "name": tag.name,
                        "count": count,
                        "thumbs": [thumb for thumb in thumbs if thumb],
                    }
                )

        return templates.TemplateResponse(
            request=request,
            name="tags.html",
            context={
                "app_name": settings.app_name,
                "tags": tags_data,
                "page": "tags",
            },
        )

    @app.get("/tag/{tag_slug}", response_class=HTMLResponse, include_in_schema=False)
    def tag_detail(request: Request, tag_slug: str) -> HTMLResponse:
        with session_scope(app.state.session_factory) as session:
            tag = session.scalar(select(Tag).where(Tag.slug == tag_slug))
            if tag is None:
                raise HTTPException(status_code=404, detail="tag not found")

            count = (
                session.execute(
                    select(func.count())
                    .select_from(Item)
                    .join(ItemTag, Item.id == ItemTag.item_id)
                    .where(ItemTag.tag_id == tag.id)
                ).scalar()
                or 0
            )

            broad = tag if tag.parent_id is None else session.get(Tag, tag.parent_id)
            broad_slug = broad.slug if broad is not None else tag.slug
            broad_name = broad.name if broad is not None else tag.name
            broad_count = (
                session.execute(
                    select(func.count())
                    .select_from(ItemTag)
                    .join(Tag, Tag.id == ItemTag.tag_id)
                    .where(Tag.slug == broad_slug)
                ).scalar()
                or 0
            )

            parent_for_filters = tag.id if tag.parent_id is None else tag.parent_id
            filter_rows = session.execute(
                select(Tag, func.count(ItemTag.item_id))
                .join(ItemTag, ItemTag.tag_id == Tag.id)
                .where(Tag.parent_id == parent_for_filters)
                .group_by(Tag.id)
                .order_by(Tag.name)
            ).all()
            tag_filters = [
                {"slug": specific.slug, "name": specific.name, "count": specific_count}
                for specific, specific_count in filter_rows
            ]

        return templates.TemplateResponse(
            request=request,
            name="tag.html",
            context={
                "app_name": settings.app_name,
                "tag_name": tag.name,
                "tag_slug": tag.slug,
                "count": count,
                "broad_slug": broad_slug,
                "broad_name": broad_name,
                "broad_count": broad_count,
                "tag_filters": tag_filters,
                "page": "tag",
            },
        )

    @app.get("/library", response_class=HTMLResponse, include_in_schema=False)
    def library(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="library.html",
            context={"app_name": settings.app_name, "page": "library"},
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "app_name": settings.app_name,
                "version": __version__,
            },
        )

    app.include_router(web_api.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "platforms": list(available_platforms()),
        }

    return app
