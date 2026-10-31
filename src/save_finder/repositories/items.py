"""Item persistence and import."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from save_finder.models import Item, ItemStatus, PlatformSource, Tag, utc_now_iso
from save_finder.platforms.base import SavedItem


@dataclass(frozen=True, slots=True)
class ImportStats:
    new: int
    existing: int

    @property
    def total(self) -> int:
        return self.new + self.existing


@dataclass(frozen=True, slots=True)
class DatabaseStats:
    total: int
    by_status: dict[str, int]
    by_platform: dict[str, int]
    tags: int


def import_saved_items(session: Session, items: Iterable[SavedItem]) -> ImportStats:
    """Idempotently insert normalized items.

    The identity is ``(platform, external_id)``. Existing rows are never
    duplicated; a missing collection/author/saved date is filled in when the
    new export supplies it.
    """
    batches: dict[str, dict[str, SavedItem]] = {}
    for item in items:
        batch = batches.setdefault(item.platform, {})
        current = batch.get(item.external_id)
        if current is None:
            batch[item.external_id] = item
            continue
        # Defensive dedupe: adapters normally dedupe already.
        if current.collection is None and item.collection:
            current.collection = item.collection
        if current.author is None and item.author:
            current.author = item.author
        if current.saved_at is None and item.saved_at:
            current.saved_at = item.saved_at

    new = 0
    existing = 0
    for platform, batch in batches.items():
        rows = session.scalars(
            select(Item).where(
                Item.platform == platform,
                Item.external_id.in_(tuple(batch)),
            )
        )
        known = {row.external_id: row for row in rows}
        for external_id, saved in batch.items():
            row = known.get(external_id)
            if row is None:
                session.add(
                    Item(
                        platform=saved.platform,
                        external_id=saved.external_id,
                        url=saved.url,
                        source=PlatformSource(saved.source),
                        author=saved.author,
                        collection=saved.collection,
                        saved_at=saved.saved_at,
                        status=ItemStatus.PENDING,
                    )
                )
                new += 1
                continue

            existing += 1
            changed = False
            if row.collection is None and saved.collection:
                row.collection = saved.collection
                changed = True
            if row.author is None and saved.author:
                row.author = saved.author
                changed = True
            if row.saved_at is None and saved.saved_at:
                row.saved_at = saved.saved_at
                changed = True
            if changed:
                row.updated_at = utc_now_iso()

    session.flush()
    return ImportStats(new=new, existing=existing)


def get_item(session: Session, platform: str, external_id: str) -> Item | None:
    return session.scalar(
        select(Item).where(
            Item.platform == platform,
            Item.external_id == external_id,
        )
    )


def list_items(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    status: ItemStatus | None = None,
    platform: str | None = None,
    collection: str | None = None,
) -> Sequence[Item]:
    statement: Select[tuple[Item]] = select(Item)
    if status is not None:
        statement = statement.where(Item.status == status)
    if platform is not None:
        statement = statement.where(Item.platform == platform)
    if collection is not None:
        statement = statement.where(Item.collection == collection)
    statement = (
        statement.order_by(Item.saved_at.is_(None), Item.saved_at.desc(), Item.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(statement).all()


def count_items(session: Session, *, platform: str | None = None) -> int:
    statement = select(func.count()).select_from(Item)
    if platform is not None:
        statement = statement.where(Item.platform == platform)
    return session.scalar(statement) or 0


def database_stats(session: Session) -> DatabaseStats:
    total = session.scalar(select(func.count()).select_from(Item)) or 0
    by_status = {
        _value(status): count
        for status, count in session.execute(
            select(Item.status, func.count()).group_by(Item.status)
        )
    }
    by_platform = {
        platform: count
        for platform, count in session.execute(
            select(Item.platform, func.count()).group_by(Item.platform)
        )
    }
    tags = session.scalar(select(func.count()).select_from(Tag)) or 0
    return DatabaseStats(
        total=total,
        by_status=by_status,
        by_platform=by_platform,
        tags=tags,
    )


def _value(value: object) -> str:
    return str(getattr(value, "value", value))
