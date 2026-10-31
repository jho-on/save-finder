"""Tag persistence and taxonomy synchronization."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.models import Tag, TagLevel, utc_now_iso


def add_item_tags(
    session: Session,
    item_id: int,
    tag_slugs: Sequence[str],
    *,
    confidences: Sequence[float] | None = None,
    source: str = "auto",
) -> int:
    """Persist taxonomy tags for one item, preserving parent rules.

    Only tags present in the taxonomy are inserted; duplicates for the same
    item are ignored (upsert by ``(item_id, tag_slug)``).
    """
    from save_finder.classify.taxonomy import Taxonomy
    from save_finder.config import load_settings
    from save_finder.models import ItemTag, Tag, TagSource

    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    valid_slugs = {t.slug for t in (*taxonomy.broad, *taxonomy.specific)}
    tag_ids = {
        slug: session.scalar(select(Tag.id).where(Tag.slug == slug))
        for slug in set(tag_slugs)
        if slug in valid_slugs
    }
    inserted = 0
    for idx, slug in enumerate(tag_slugs):
        if slug not in valid_slugs:
            continue
        tag_id = tag_ids.get(slug)
        if tag_id is None:
            continue
        existing = session.scalar(
            select(ItemTag).where(ItemTag.item_id == item_id, ItemTag.tag_id == tag_id)
        )
        if existing is not None:
            if existing.source == TagSource.MANUAL:
                continue
            existing.confidence = confidences[idx] if confidences else existing.confidence
            existing.source = TagSource(source)
            session.flush()
            inserted += 1
            continue
        session.add(
            ItemTag(
                item_id=item_id,
                tag_id=tag_id,
                confidence=confidences[idx] if confidences else 0.6,
                source=TagSource(source),
            )
        )
        inserted += 1
    session.flush()
    return inserted


def sync_taxonomy(session: Session, taxonomy: Taxonomy) -> tuple[int, int]:
    """Upsert taxonomy tags into the database.

    Returns ``(created, updated)``. Tags removed from the taxonomy are kept
    because items may still reference them; removing tags requires an explicit
    migration with reclassification.
    """
    existing = {tag.slug: tag for tag in session.scalars(select(Tag))}
    created = 0
    updated = 0

    for definition in (*taxonomy.broad, *taxonomy.specific):
        parent = existing.get(definition.parent) if definition.parent else None
        tag = existing.get(definition.slug)
        level = TagLevel(definition.level)
        if tag is None:
            tag = Tag(
                slug=definition.slug,
                name=definition.name,
                level=level,
                parent=parent,
                visual_description=definition.visual_description,
            )
            session.add(tag)
            existing[definition.slug] = tag
            created += 1
            continue

        changed = False
        if tag.name != definition.name:
            tag.name = definition.name
            changed = True
        if tag.level != level:
            tag.level = level
            changed = True
        if tag.parent is not parent:
            tag.parent = parent
            changed = True
        if tag.visual_description != definition.visual_description:
            tag.visual_description = definition.visual_description
            changed = True
        if changed:
            tag.updated_at = utc_now_iso()
            updated += 1

    session.flush()
    return created, updated
