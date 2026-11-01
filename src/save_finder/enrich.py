"""Enrichment stage: public metadata and a locally stored thumbnail."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from save_finder.config import Settings
from save_finder.errors import EnrichError, EnrichTransient, EnrichUnavailable
from save_finder.models import Item, ItemStatus
from save_finder.platforms.base import Metadata, PlatformAdapter, SavedItem
from save_finder.platforms.registry import get_adapter
from save_finder.repositories.items import list_pending_items, mark_item_status, save_enrichment

logger = logging.getLogger(__name__)

USER_AGENT = "SaveFinder/0.1 (local library)"
THUMBNAIL_MAX_BYTES = 10 * 1024 * 1024
POST_TYPES = {"video", "image", "carousel"}
CONTENT_TYPE_SUFFIXES = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

AdapterFactory = Callable[[str], PlatformAdapter]
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class EnrichmentOutcome:
    item_id: int
    platform: str
    external_id: str
    status: str
    post_type: str | None = None
    media_count: int = 0
    duration_seconds: float | None = None
    thumbnail_saved: bool = False
    error: str | None = None
    elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class EnrichReport:
    outcomes: tuple[EnrichmentOutcome, ...] = ()

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(outcome.status for outcome in self.outcomes))


@dataclass(frozen=True, slots=True)
class _TaskResult:
    outcome: EnrichmentOutcome
    metadata: Metadata | None = None
    thumb_path: str | None = None


def enrich_items(
    session: Session,
    settings: Settings,
    *,
    limit: int | None = 30,
    platform: str | None = None,
    retry_errors: bool = False,
    adapter_factory: AdapterFactory | None = None,
    thumbnail_fetcher: Callable[..., Path] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    shutdown_event: threading.Event | None = None,
) -> EnrichReport:
    """Enrich pending items, committing each outcome independently.

    Network calls run concurrently; database writes stay on the caller thread.
    An item that fails never interrupts the batch. ``retry_errors`` also picks
    items left in ``error`` by a previous run.
    """
    fetcher = thumbnail_fetcher or download_thumbnail
    factory = adapter_factory or get_adapter
    rows = list_pending_items(
        session,
        limit=limit,
        platform=platform,
        include_errors=retry_errors,
    )
    if not rows:
        return EnrichReport()

    tasks = [
        (
            row.id,
            SavedItem(
                platform=row.platform,
                external_id=row.external_id,
                url=row.url,
                source=row.source.value,
                author=row.author,
                collection=row.collection,
                saved_at=row.saved_at,
            ),
        )
        for row in rows
    ]
    workers = max(1, min(settings.pipeline.max_concurrency, len(tasks)))

    results: list[_TaskResult] = []

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="enrich",
    ) as executor:
        pending_tasks = iter(tasks)
        futures = {}

        def submit_available() -> None:
            while len(futures) < workers and (
                shutdown_event is None or not shutdown_event.is_set()
            ):
                try:
                    item_id, item = next(pending_tasks)
                except StopIteration:
                    break

                future = executor.submit(
                    _enrich_one,
                    item_id,
                    item,
                    settings,
                    factory,
                    fetcher,
                )
                futures[future] = item_id

        submit_available()

        while futures:
            finished, _ = wait(
                futures,
                return_when=FIRST_COMPLETED,
            )

            for future in finished:
                item_id = futures.pop(future)
                result = future.result()

                item = session.get(Item, item_id)
                if item is not None:
                    _persist_enrichment(session, item, result)
                    session.commit()

                results.append(result)

                if progress_callback is not None:
                    progress_callback(len(results), len(tasks))

            submit_available()

    order = {item_id: index for index, (item_id, _item) in enumerate(tasks)}
    results.sort(key=lambda result: order.get(result.outcome.item_id, 0))
    return EnrichReport(tuple(result.outcome for result in results))


def enrich_item(
    session: Session,
    item: Item,
    settings: Settings,
    *,
    adapter_factory: AdapterFactory | None = None,
    thumbnail_fetcher: Callable[..., Path] | None = None,
) -> EnrichmentOutcome:
    """Enrich and persist one item."""
    saved_item = SavedItem(
        platform=item.platform,
        external_id=item.external_id,
        url=item.url,
        source=item.source.value,
        author=item.author,
        collection=item.collection,
        saved_at=item.saved_at,
    )

    result = _enrich_one(
        item.id,
        saved_item,
        settings,
        adapter_factory or get_adapter,
        thumbnail_fetcher or download_thumbnail,
    )

    _persist_enrichment(session, item, result)
    session.commit()

    return result.outcome


def _persist_enrichment(
    session: Session,
    item: Item,
    result: _TaskResult,
) -> None:
    if result.metadata is not None:
        save_enrichment(
            session,
            item,
            result.metadata,
            thumb_path=result.thumb_path,
        )
    else:
        mark_item_status(
            session,
            item,
            ItemStatus(result.outcome.status),
            error=result.outcome.error,
        )


def _enrich_one(
    item_id: int,
    item: SavedItem,
    settings: Settings,
    adapter_factory: AdapterFactory,
    thumbnail_fetcher: Callable[..., Path],
) -> _TaskResult:
    started = time.monotonic()
    try:
        adapter = adapter_factory(item.platform)
        _jitter(settings)
        metadata = _with_retry(lambda: adapter.enrich(item), settings)
        _validate_metadata(metadata)

        thumb_path = None
        if metadata.thumbnail_url:
            try:
                _jitter(settings)
                thumb = _with_retry(
                    lambda: thumbnail_fetcher(
                        metadata.thumbnail_url,
                        _thumbnail_stem(settings, item),
                        timeout=settings.pipeline.download_timeout,
                        max_bytes=THUMBNAIL_MAX_BYTES,
                    ),
                    settings,
                )
                thumb_path = _relative_to_data(thumb, settings.paths.data)
            except EnrichError as exc:
                logger.warning(
                    "thumbnail failed platform=%s external_id=%s: %s",
                    item.platform,
                    item.external_id,
                    exc,
                )

        status = ItemStatus.ENRICHED if metadata.media_count > 0 else ItemStatus.SKIPPED
        outcome = _outcome(
            item_id, item, status, started, metadata=metadata, thumb_saved=bool(thumb_path)
        )
    except EnrichUnavailable as exc:
        outcome = _outcome(item_id, item, ItemStatus.UNAVAILABLE, started, error=None)
        logger.info(
            "unavailable platform=%s external_id=%s reason=%s",
            item.platform,
            item.external_id,
            exc,
        )
        return _TaskResult(outcome)
    except EnrichError as exc:
        outcome = _outcome(item_id, item, ItemStatus.ERROR, started, error=str(exc))
        logger.warning(
            "enrich failed platform=%s external_id=%s: %s",
            item.platform,
            item.external_id,
            exc,
        )
        return _TaskResult(outcome)
    except Exception as exc:
        message = f"unexpected enrichment failure: {exc.__class__.__name__}"
        outcome = _outcome(item_id, item, ItemStatus.ERROR, started, error=message)
        logger.exception(
            "unexpected enrich failure platform=%s external_id=%s",
            item.platform,
            item.external_id,
        )
        return _TaskResult(outcome)

    logger.info(
        "enrich platform=%s external_id=%s status=%s post_type=%s media_count=%d "
        "thumb=%s elapsed_ms=%d",
        item.platform,
        item.external_id,
        outcome.status,
        outcome.post_type,
        outcome.media_count,
        outcome.thumbnail_saved,
        outcome.elapsed_ms,
    )
    return _TaskResult(outcome, metadata, thumb_path)


def _outcome(
    item_id: int,
    item: SavedItem,
    status: ItemStatus,
    started: float,
    *,
    metadata: Metadata | None = None,
    thumb_saved: bool = False,
    error: str | None = None,
) -> EnrichmentOutcome:
    return EnrichmentOutcome(
        item_id=item_id,
        platform=item.platform,
        external_id=item.external_id,
        status=status.value,
        post_type=metadata.post_type if metadata else None,
        media_count=metadata.media_count if metadata else 0,
        duration_seconds=metadata.duration_seconds if metadata else None,
        thumbnail_saved=thumb_saved,
        error=error,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def _validate_metadata(metadata: Metadata) -> None:
    if metadata.media_count < 0:
        raise EnrichError("adapter returned a negative media count")
    if metadata.post_type not in POST_TYPES and metadata.media_count > 0:
        raise EnrichError(f"adapter returned an invalid post type: {metadata.post_type!r}")
    if metadata.duration_seconds is not None and metadata.duration_seconds < 0:
        raise EnrichError("adapter returned a negative duration")


def _with_retry(call: Callable[[], T], settings: Settings) -> T:
    attempts = max(0, settings.pipeline.max_retries) + 1
    for attempt in range(attempts):
        try:
            return call()
        except EnrichTransient:
            if attempt == attempts - 1:
                raise
            delay = settings.pipeline.retry_backoff_seconds * (2**attempt)
            logger.info("transient failure, retrying in %.1fs", delay)
            if delay > 0:
                time.sleep(delay)
    raise AssertionError("unreachable")


def _jitter(settings: Settings) -> None:
    maximum = max(0.0, settings.pipeline.request_jitter_seconds)
    if maximum <= 0:
        return
    time.sleep(random.uniform(0, maximum))


def _thumbnail_stem(settings: Settings, item: SavedItem) -> Path:
    digest = hashlib.sha1(f"{item.platform}:{item.external_id}".encode()).hexdigest()[:10]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.external_id)[:60]
    return settings.paths.thumbs / f"{item.platform}-{safe_id}-{digest}"


def _relative_to_data(path: Path, data_dir: Path) -> str:
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return str(path)


def download_thumbnail(
    url: str,
    dest_stem: Path,
    *,
    timeout: float,
    max_bytes: int = THUMBNAIL_MAX_BYTES,
) -> Path:
    """Download one thumbnail with a size limit and atomic rename."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise EnrichError("thumbnail URL must use http or https")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise EnrichError(f"unexpected thumbnail content type: {content_type}")
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        if exc.code in {404, 410}:
            raise EnrichError(f"thumbnail not found: HTTP {exc.code}") from exc
        if exc.code == 429 or 500 <= exc.code < 600:
            raise EnrichTransient(f"thumbnail request failed: HTTP {exc.code}") from exc
        raise EnrichError(f"thumbnail request failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise EnrichTransient(f"thumbnail request failed: {exc}") from exc

    if len(data) > max_bytes:
        raise EnrichError(f"thumbnail exceeds {max_bytes // (1024 * 1024)} MB")
    if not data:
        raise EnrichError("thumbnail is empty")

    destination = dest_stem.with_suffix(CONTENT_TYPE_SUFFIXES.get(content_type, ".jpg"))
    temporary = None

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as temp_file:
            temporary = Path(temp_file.name)
            temp_file.write(data)

        temporary.replace(destination)

    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise EnrichError(f"could not store thumbnail: {exc}") from exc

    return destination
