"""JSON API routes for library browsing and pipeline execution (fase 5)."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select, update

from save_finder.classify.fuse import classify_persisted_item
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import Settings, ensure_directories
from save_finder.db import session_scope
from save_finder.enrich import enrich_items
from save_finder.errors import ExportImportError
from save_finder.importer import copy_upload, import_export
from save_finder.models import (
    Item,
    ItemStatus,
    ItemTag,
    MediaAsset,
    PipelineJob,
    Tag,
    utc_now_iso,
)
from save_finder.providers.base import LLMProvider, VisionProvider
from save_finder.providers.factory import build_llm, build_vision
from save_finder.repositories.tags import replace_auto_tags

router = APIRouter(prefix="/api", tags=["library"])

_UNSET = object()


def _start_background_task(
    target,
    tasks: set[threading.Thread],
    lock: threading.Lock,
    *,
    name: str | None = None,
) -> None:
    thread: threading.Thread

    def _run() -> None:
        try:
            target()
        finally:
            with lock:
                tasks.discard(thread)

    thread = threading.Thread(target=_run, daemon=False, name=name)

    with lock:
        tasks.add(thread)

    try:
        thread.start()
    except BaseException:
        with lock:
            tasks.discard(thread)
        raise


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def classification_method(tags: Sequence[dict[str, object]]) -> str | None:
    """Summarize how an item was classified for the UI.

    ``ai`` means at least one automatic tag carries LLM/vision evidence.
    Automatic tags without evidence come from the old keyword heuristic.
    """
    if not tags:
        return None
    automatic = [tag for tag in tags if tag.get("source") == "auto"]
    if not automatic:
        return "manual"
    evidence = " ".join(str(tag.get("evidence") or "") for tag in automatic)
    if any(
        marker in evidence for marker in ("llm:", "vision:", "text+visual", "ollama:", "open-clip:")
    ):
        return "ai"
    return "heuristic"


def display_title(caption: str | None, external_id: str, limit: int = 120) -> str:
    """Return a human-readable title derived from the caption.

    Posts do not have a title field, so the caption is the closest thing to a
    name. Fall back to the external id when the caption is empty.
    """
    text = " ".join((caption or "").split())
    if not text:
        return external_id
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _item_to_dict(item: Item, tags: Sequence[dict[str, object]] | None = None) -> dict[str, object]:
    thumb = item.thumb_path or None
    if thumb is not None and thumb.startswith("http"):
        thumb = None
    tag_list = list(tags or [])
    return {
        "id": item.id,
        "platform": item.platform,
        "external_id": item.external_id,
        "title": display_title(item.caption, item.external_id),
        "url": item.url,
        "author": item.author,
        "caption": item.caption,
        "collection": item.collection,
        "status": item.status.value,
        "post_type": item.post_type.value if item.post_type else None,
        "media_count": item.media_count,
        "duration_seconds": item.duration_seconds,
        "thumb_path": thumb,
        "saved_at": item.saved_at,
        "enriched_at": item.enriched_at,
        "classification_method": classification_method(tag_list),
        "tags": tag_list,
    }


def _tags_for_items(session, item_ids: Sequence[int]) -> dict[int, list[dict[str, object]]]:
    tags_map: dict[int, list[dict[str, object]]] = {}
    if not item_ids:
        return tags_map
    rows = session.execute(
        select(ItemTag, Tag)
        .join(Tag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id.in_(item_ids))
        .order_by(Tag.level, Tag.name)
    ).all()
    for item_tag, tag in rows:
        tags_map.setdefault(item_tag.item_id, []).append(
            {
                "slug": tag.slug,
                "name": tag.name,
                "level": tag.level.value,
                "confidence": item_tag.confidence,
                "source": item_tag.source.value,
                "evidence": item_tag.evidence,
            }
        )
    return tags_map


@router.get("/items")
def list_items(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    platform: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    with session_scope(request.app.state.session_factory) as session:
        query = select(Item)
        if status is not None:
            try:
                query = query.where(Item.status == ItemStatus(status))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid status: {status}") from None
        if platform is not None:
            query = query.where(Item.platform == platform)
        if tag is not None:
            tag_id = session.scalar(select(Tag.id).where(Tag.slug == tag))
            if tag_id is None:
                return {"items": [], "total": 0, "limit": limit, "offset": offset}
            query = query.join(ItemTag, Item.id == ItemTag.item_id).where(ItemTag.tag_id == tag_id)

        total = session.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
        rows = session.scalars(query.order_by(Item.id.desc()).offset(offset).limit(limit)).all()
        tags_map = _tags_for_items(session, [row.id for row in rows])
        return {
            "items": [_item_to_dict(item, tags_map.get(item.id)) for item in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/items/search")
def search_items(
    request: Request,
    q: Annotated[str, Query(min_length=1)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    like_term = f"%{q.lower()}%"
    with session_scope(request.app.state.session_factory) as session:
        query = select(Item).where(
            Item.caption.ilike(like_term)
            | Item.author.ilike(like_term)
            | Item.external_id.ilike(like_term)
        )
        if status is not None:
            try:
                query = query.where(Item.status == ItemStatus(status))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid status: {status}") from None

        total = session.execute(select(func.count()).select_from(query.subquery())).scalar() or 0
        rows = session.scalars(query.order_by(Item.id.desc()).offset(offset).limit(limit)).all()
        tags_map = _tags_for_items(session, [row.id for row in rows])
        return {
            "query": q,
            "items": [_item_to_dict(item, tags_map.get(item.id)) for item in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/items/{item_id}")
def get_item_detail(request: Request, item_id: int) -> dict[str, object]:
    with session_scope(request.app.state.session_factory) as session:
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        result = _item_to_dict(item, _tags_for_items(session, [item_id]).get(item_id))
        return result


# Pipeline execution (fase 5 — background threads, not a durable worker queue)
def _start_job(operation: str, session_factory, **params: object) -> str:
    job_id = str(uuid4())
    job = PipelineJob(
        id=job_id,
        operation=operation,
        status="running",
        params=params,
    )
    with session_scope(session_factory) as session:
        session.add(job)
    return job_id


def _update_job(
    job_id: str,
    session_factory,
    *,
    status: Any = _UNSET,
    progress: Any = _UNSET,
    result: Any = _UNSET,
    error: Any = _UNSET,
) -> None:
    values: dict[str, object] = {}
    if status is not _UNSET:
        values["status"] = status
    if progress is not _UNSET:
        values["progress"] = progress
    if result is not _UNSET:
        values["result"] = result
    if error is not _UNSET:
        values["error"] = error
    if status in ("done", "error", "interrupted"):
        values["finished_at"] = utc_now_iso()
    if not values:
        return
    with session_scope(session_factory) as session:
        session.execute(update(PipelineJob).where(PipelineJob.id == job_id).values(**values))


def _download_one(item_id: int, settings: Settings, session_factory) -> dict[str, object]:
    from save_finder.download import download_item

    with session_scope(session_factory) as session:
        item = session.get(Item, item_id)
        if item is None:
            return {
                "item_id": item_id,
                "error": "item not found",
                "pieces": 0,
                "cleaned": False,
            }

        external_id = item.external_id
        result = download_item(
            item,
            session,
            settings,
            timeout=settings.pipeline.download_timeout,
        )
        return {
            "item_id": item_id,
            "external_id": external_id,
            "pieces": len(result.pieces),
            "cleaned": result.cleaned,
            "analyzed": result.analyzed,
            "transcript_saved": result.transcript_saved,
            "embedding_saved": result.embedding_saved,
            "error": result.error,
            "analysis_error": result.analysis_error,
        }


def _classify_one(
    item_id: int,
    taxonomy: Taxonomy,
    session_factory,
    vision: VisionProvider | None = None,
    llm: LLMProvider | None = None,
) -> dict[str, object]:
    with session_scope(session_factory) as session:
        item = session.get(Item, item_id)
        if item is None:
            return {"item_id": item_id, "error": "item not found", "tags_emitted": 0}

        result = classify_persisted_item(item, taxonomy, vision=vision, llm=llm)
        inserted = replace_auto_tags(
            session,
            item.id,
            result.tags,
            confidences=result.confidences,
            evidence=result.evidence,
            taxonomy=taxonomy,
        )

        item.status = ItemStatus.CLASSIFIED
        item.classified_at = item.updated_at = utc_now_iso()
        session.commit()
        return {
            "item_id": item_id,
            "tags": list(result.tags),
            "tags_emitted": inserted,
            "error": result.error,
        }


def _enrich_one(item_id: int, settings: Settings, session_factory) -> dict[str, object]:
    from save_finder.enrich import enrich_item

    with session_scope(session_factory) as session:
        item = session.get(Item, item_id)
        if item is None:
            return {"item_id": item_id, "status": "error", "error": "item not found"}

        outcome = enrich_item(session, item, settings)
        return {
            "item_id": item_id,
            "platform": outcome.platform,
            "external_id": outcome.external_id,
            "status": outcome.status,
            "media_count": outcome.media_count,
            "error": outcome.error,
        }


def _run_continuous_pipeline(
    job_id: str,
    settings: Settings,
    session_factory,
    platform: str | None = None,
    shutdown_event: threading.Event | None = None,
) -> None:
    taxonomy = Taxonomy.load(settings.taxonomy_path)
    vision = build_vision(settings)
    llm = build_llm(settings)
    stages = ("enrich", "download", "classify")
    workers = max(1, settings.pipeline.max_concurrency)

    try:
        with session_scope(session_factory) as session:
            enrich_query = select(Item.id).where(Item.status == ItemStatus.PENDING)
            download_query = select(Item.id).where(
                Item.status == ItemStatus.ENRICHED,
                ~Item.id.in_(select(MediaAsset.item_id)),
            )
            classify_query = select(Item.id).where(
                Item.status.in_((ItemStatus.ENRICHED, ItemStatus.ANALYZED)),
                Item.id.in_(select(MediaAsset.item_id)),
                ~Item.id.in_(select(ItemTag.item_id)),
            )

            if platform is not None:
                enrich_query = enrich_query.where(Item.platform == platform)
                download_query = download_query.where(Item.platform == platform)
                classify_query = classify_query.where(Item.platform == platform)

            queues = {
                "enrich": deque(session.scalars(enrich_query.order_by(Item.id)).all()),
                "download": deque(session.scalars(download_query.order_by(Item.id)).all()),
                "classify": deque(session.scalars(classify_query.order_by(Item.id)).all()),
            }

        completed = {stage: 0 for stage in stages}
        errors = {stage: 0 for stage in stages}
        running: dict[str, set[int]] = {stage: set() for stage in stages}
        futures: dict[Any, tuple[str, int]] = {}

        def is_shutting_down() -> bool:
            return shutdown_event is not None and shutdown_event.is_set()

        def update_progress() -> None:
            stage_progress = {}
            for stage in stages:
                pending = len(queues[stage])
                active = len(running[stage])
                done = completed[stage]
                stage_progress[stage] = {
                    "done": done,
                    "pending": pending,
                    "running": active,
                    "errors": errors[stage],
                    "total": done + pending + active,
                    "current_items": sorted(running[stage]),
                }

            _update_job(
                job_id,
                session_factory,
                progress={
                    "stages": stage_progress,
                    "done": sum(completed.values()),
                    "total": sum(data["total"] for data in stage_progress.values()),
                },
            )

        def submit_available(executors: dict[str, ThreadPoolExecutor]) -> None:
            if is_shutting_down():
                return

            for stage in stages:
                while queues[stage] and len(running[stage]) < workers and not is_shutting_down():
                    item_id = queues[stage].popleft()
                    if stage == "enrich":
                        future = executors[stage].submit(
                            _enrich_one, item_id, settings, session_factory
                        )
                    elif stage == "download":
                        future = executors[stage].submit(
                            _download_one, item_id, settings, session_factory
                        )
                    else:
                        future = executors[stage].submit(
                            _classify_one,
                            item_id,
                            taxonomy,
                            session_factory,
                            vision,
                            llm,
                        )
                    futures[future] = (stage, item_id)
                    running[stage].add(item_id)

            update_progress()

        with (
            ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as enrich_executor,
            ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="download"
            ) as download_executor,
            ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="classify"
            ) as classify_executor,
        ):
            executors = {
                "enrich": enrich_executor,
                "download": download_executor,
                "classify": classify_executor,
            }
            update_progress()

            while any(queues[stage] for stage in stages) or futures:
                if is_shutting_down():
                    for stage in stages:
                        queues[stage].clear()
                else:
                    submit_available(executors)

                if not futures:
                    break

                finished, _ = wait(futures, return_when=FIRST_COMPLETED)

                for future in finished:
                    stage, item_id = futures.pop(future)
                    running[stage].discard(item_id)
                    completed[stage] += 1

                    try:
                        result = future.result()
                    except Exception:
                        errors[stage] += 1
                        continue

                    if result.get("error"):
                        errors[stage] += 1

                    if is_shutting_down():
                        continue

                    if stage == "enrich" and result.get("status") == ItemStatus.ENRICHED.value:
                        queues["download"].append(item_id)

                    elif stage == "download":
                        with session_scope(session_factory) as session:
                            has_media = session.scalar(
                                select(MediaAsset.id).where(MediaAsset.item_id == item_id).limit(1)
                            )
                            has_tags = session.scalar(
                                select(ItemTag.item_id).where(ItemTag.item_id == item_id).limit(1)
                            )
                        if has_media is not None and has_tags is None:
                            queues["classify"].append(item_id)

                update_progress()

        _update_job(
            job_id,
            session_factory,
            status="interrupted" if is_shutting_down() else "done",
            result={
                stage: {
                    "completed": completed[stage],
                    "errors": errors[stage],
                }
                for stage in stages
            },
        )

    except Exception as exc:
        _update_job(job_id, session_factory, status="error", error=str(exc))


@router.post("/pipeline/import")
def pipeline_import(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    platform: Annotated[str, Form()] = "instagram",
) -> dict[str, object]:
    settings = _settings(request)
    ensure_directories(settings)
    upload_path = settings.paths.tmp / f"upload-pipeline-{uuid4().hex}.zip"
    try:
        file.file.seek(0)
        copy_upload(
            file.file,
            upload_path,
            max_bytes=settings.import_config.max_archive_bytes,
        )
    except ExportImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = _start_job("import", request.app.state.session_factory, platform=platform)

    def _run() -> None:
        try:
            with session_scope(request.app.state.session_factory) as session:
                result = import_export(
                    session,
                    platform=platform,
                    path=upload_path,
                    tmp_dir=settings.paths.tmp,
                    limits=settings.import_config,
                )
            _update_job(
                job_id,
                request.app.state.session_factory,
                status="done",
                result={
                    "new": result.new,
                    "existing": result.existing,
                    "ignored": result.ignored,
                    "total": result.total,
                },
            )
        except Exception as exc:
            _update_job(
                job_id,
                request.app.state.session_factory,
                status="error",
                error=str(exc),
            )
        finally:
            upload_path.unlink(missing_ok=True)

    _start_background_task(
        _run,
        request.app.state.background_tasks,
        request.app.state.background_tasks_lock,
        name=f"pipeline-{job_id[:8]}",
    )
    return {"job_id": job_id, "status": "started", "operation": "import"}


@router.post("/pipeline/enrich")
def pipeline_enrich(
    request: Request,
    platform: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    settings = _settings(request)
    job_id = _start_job("enrich", request.app.state.session_factory, platform=platform)

    def _run() -> None:
        try:

            def _progress(done: int, total: int) -> None:
                _update_job(
                    job_id,
                    request.app.state.session_factory,
                    progress={"done": done, "total": total},
                )

            with session_scope(request.app.state.session_factory) as session:
                report = enrich_items(
                    session,
                    settings,
                    limit=None,
                    platform=platform,
                    progress_callback=_progress,
                    shutdown_event=request.app.state.shutdown_event,
                )

            _update_job(
                job_id,
                request.app.state.session_factory,
                status=("interrupted" if request.app.state.shutdown_event.is_set() else "done"),
                result={"total": report.total, "counts": report.counts},
            )
        except Exception as exc:
            _update_job(
                job_id,
                request.app.state.session_factory,
                status="error",
                error=str(exc),
            )

    _start_background_task(
        _run,
        request.app.state.background_tasks,
        request.app.state.background_tasks_lock,
        name=f"pipeline-{job_id[:8]}",
    )
    return {"job_id": job_id, "status": "started", "operation": "enrich"}


def _run_items_job(
    request: Request,
    job_id: str,
    item_ids: Sequence[int],
    worker,
    *,
    count_key: str | None = None,
) -> None:
    total_items = len(item_ids)
    results: list[dict[str, object]] = []
    done = 0
    counter = 0
    max_workers = max(1, min(request.app.state.settings.pipeline.max_concurrency, total_items))

    def _progress() -> None:
        _update_job(
            job_id,
            request.app.state.session_factory,
            progress={"done": done, "total": total_items},
        )

    try:
        _progress()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            pending_items = iter(item_ids)
            futures: dict[Any, int] = {}

            def submit_available() -> None:
                while len(futures) < max_workers and not request.app.state.shutdown_event.is_set():
                    try:
                        item_id = next(pending_items)
                    except StopIteration:
                        break
                    futures[executor.submit(worker, item_id)] = item_id

            submit_available()
            while futures:
                finished, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    futures.pop(future)
                    result = future.result()
                    results.append(result)
                    if count_key is not None:
                        counter += int(result.get(count_key, 0))
                    done += 1
                    _progress()
                submit_available()

        payload: dict[str, object] = {
            "processed": len(results),
            "results": results,
        }
        if count_key is not None:
            payload["total"] = counter
        _update_job(
            job_id,
            request.app.state.session_factory,
            status=("interrupted" if request.app.state.shutdown_event.is_set() else "done"),
            result=payload,
        )
    except Exception as exc:
        _update_job(
            job_id,
            request.app.state.session_factory,
            status="error",
            error=str(exc),
        )


@router.post("/pipeline/download")
def pipeline_download(
    request: Request,
    platform: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    settings = _settings(request)
    job_id = _start_job("download", request.app.state.session_factory, platform=platform)

    with session_scope(request.app.state.session_factory) as session:
        query = select(Item.id).where(
            Item.status == ItemStatus.ENRICHED,
            ~Item.id.in_(select(MediaAsset.item_id)),
        )
        if platform is not None:
            query = query.where(Item.platform == platform)
        item_ids = list(session.scalars(query.order_by(Item.id)).all())

    def _worker(item_id: int) -> dict[str, object]:
        return _download_one(item_id, settings, request.app.state.session_factory)

    def _run() -> None:
        _run_items_job(request, job_id, item_ids, _worker)

    _start_background_task(
        _run,
        request.app.state.background_tasks,
        request.app.state.background_tasks_lock,
        name=f"pipeline-{job_id[:8]}",
    )
    return {"job_id": job_id, "status": "started", "operation": "download"}


@router.post("/pipeline/classify")
def pipeline_classify(
    request: Request,
    platform: Annotated[str | None, Form()] = None,
    reclassify: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    settings = _settings(request)
    taxonomy = Taxonomy.load(settings.taxonomy_path)
    vision = build_vision(settings)
    llm = build_llm(settings)
    job_id = _start_job(
        "classify",
        request.app.state.session_factory,
        platform=platform,
        reclassify=reclassify,
    )

    with session_scope(request.app.state.session_factory) as session:
        if reclassify:
            query = select(Item.id).where(
                Item.status.in_((ItemStatus.ENRICHED, ItemStatus.ANALYZED, ItemStatus.CLASSIFIED))
            )
        else:
            query = select(Item.id).where(
                Item.status.in_((ItemStatus.ENRICHED, ItemStatus.ANALYZED)),
                Item.id.in_(select(MediaAsset.item_id)),
                ~Item.id.in_(select(ItemTag.item_id)),
            )
        if platform is not None:
            query = query.where(Item.platform == platform)
        item_ids = list(session.scalars(query.order_by(Item.id)).all())

    def _worker(item_id: int) -> dict[str, object]:
        return _classify_one(
            item_id,
            taxonomy,
            request.app.state.session_factory,
            vision,
            llm,
        )

    def _run() -> None:
        _run_items_job(request, job_id, item_ids, _worker, count_key="tags_emitted")

    _start_background_task(
        _run,
        request.app.state.background_tasks,
        request.app.state.background_tasks_lock,
        name=f"pipeline-{job_id[:8]}",
    )
    return {"job_id": job_id, "status": "started", "operation": "classify"}


@router.post("/pipeline/run")
def pipeline_run(
    request: Request,
    platform: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    settings = _settings(request)
    job_id = _start_job("pipeline", request.app.state.session_factory, platform=platform)

    def _run() -> None:
        _run_continuous_pipeline(
            job_id,
            settings,
            session_factory=request.app.state.session_factory,
            platform=platform,
            shutdown_event=request.app.state.shutdown_event,
        )

    _start_background_task(
        _run,
        request.app.state.background_tasks,
        request.app.state.background_tasks_lock,
        name=f"pipeline-{job_id[:8]}",
    )
    return {"job_id": job_id, "status": "started", "operation": "pipeline"}


@router.get("/pipeline/stats")
def pipeline_stats(request: Request) -> dict[str, object]:
    with session_scope(request.app.state.session_factory) as session:

        def count(statement) -> int:
            return session.execute(statement).scalar() or 0

        total = count(select(func.count()).select_from(Item))
        enriched = count(
            select(func.count()).select_from(Item).where(Item.status == ItemStatus.ENRICHED)
        )
        classified = count(
            select(func.count()).select_from(Item).where(Item.status == ItemStatus.CLASSIFIED)
        )
        pending = count(
            select(func.count()).select_from(Item).where(Item.status == ItemStatus.PENDING)
        )
        error = count(select(func.count()).select_from(Item).where(Item.status == ItemStatus.ERROR))
        with_media = count(
            select(func.count(func.distinct(MediaAsset.item_id))).select_from(MediaAsset)
        )
        tagged_items = count(
            select(func.count(func.distinct(ItemTag.item_id))).select_from(ItemTag)
        )

    def pct(done: int) -> float | None:
        if total <= 0:
            return None
        return round(min(100, done / total * 100), 1)

    enrich_done = enriched + classified

    return {
        "general": {
            "total": total,
            "enriched": enriched,
            "pending": pending,
            "error": error,
            "with_media_assets": with_media,
            "tagged": tagged_items,
        },
        "enrich": {"done": enrich_done, "target": total, "percent": pct(enrich_done)},
        "download": {"done": with_media, "target": total, "percent": pct(with_media)},
        "classify": {"done": classified, "target": total, "percent": pct(classified)},
    }


@router.get("/pipeline/{job_id}")
def pipeline_status(request: Request, job_id: str) -> dict[str, object]:
    with session_scope(request.app.state.session_factory) as session:
        job = session.get(PipelineJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "job_id": job.id,
            "operation": job.operation,
            "status": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "result": job.result,
            "error": job.error,
            "progress": job.progress,
        }
