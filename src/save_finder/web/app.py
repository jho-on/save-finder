"""FastAPI application factory.

The import page is the first client of the JSON API. Item browsing and the
library UI arrive in phase 5 and will reuse the same backend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from save_finder import __version__
from save_finder.config import Settings, ensure_directories, load_settings
from save_finder.db import make_engine, make_session_factory, session_scope
from save_finder.errors import ExportImportError, SaveFinderError
from save_finder.importer import copy_upload, import_export
from save_finder.platforms.registry import available_platforms

WEB_DIR = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ensure_directories(settings)

    engine = make_engine(settings)
    session_factory: sessionmaker[Session] = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="SaveFinder API",
        version=__version__,
        description="Local library for media saved on social networks.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="import.html",
            context={
                "app_name": settings.app_name,
                "version": __version__,
                "max_upload_mb": settings.import_config.max_archive_mb,
            },
        )

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "platforms": list(available_platforms()),
        }

    @app.post("/api/import", tags=["import"])
    def upload_import(
        file: Annotated[UploadFile, File(description="Official export ZIP.")],
        platform: Annotated[str, Form()] = "instagram",
    ) -> dict[str, object]:
        upload = settings.paths.tmp / f"upload-{uuid4().hex}.zip"
        try:
            file.file.seek(0)
            try:
                copy_upload(
                    file.file,
                    upload,
                    max_bytes=settings.import_config.max_archive_bytes,
                )
                with session_scope(session_factory) as session:
                    result = import_export(
                        session,
                        platform=platform,
                        path=upload,
                        tmp_dir=settings.paths.tmp,
                        limits=settings.import_config,
                    )
            except ExportImportError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except SaveFinderError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except OperationalError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Banco não inicializado. Execute `save-finder db init`.",
                ) from exc
        finally:
            upload.unlink(missing_ok=True)

        payload = asdict(result)
        payload["total"] = result.total
        return payload

    return app
