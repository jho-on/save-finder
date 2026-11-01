"""Tag persistence and taxonomy synchronization."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import ItemTag, Tag, TagLevel, TagSource, utc_now_iso


def add_item_tags(
    session: Session,
    item_id: int,
    tag_slugs: Sequence[str],
    *,
    confidences: Sequence[float] | None = None,
    source: str = "auto",
) -> int:
    """Persist taxonomy tags for one item, preserving parent rules.

    The product rule is one broad tag per item plus the specific tags that
    belong to it. Incoming specifics select their broad parent; an existing
    broad tag already on the item (manual tags first) wins over a new one.
    Only tags present in the taxonomy are inserted; duplicates for the same
    item are updated unless the stored tag is manual.
    """
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    definitions = {tag.slug: tag for tag in (*taxonomy.broad, *taxonomy.specific)}

    confidence_by_slug: dict[str, float] = {}
    for index, slug in enumerate(tag_slugs):
        if slug not in definitions:
            continue
        if confidences is not None and index < len(confidences):
            confidence_by_slug[slug] = confidences[index]
        else:
            confidence_by_slug.setdefault(slug, 0.6)

    existing_rows = session.execute(
        select(ItemTag, Tag)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id == item_id, Tag.level == TagLevel.BROAD)
    ).all()

    selected_broad: str | None = None
    for item_tag, tag in existing_rows:
        if item_tag.source == TagSource.MANUAL:
            selected_broad = tag.slug
            break
    if selected_broad is None and existing_rows:
        selected_broad = existing_rows[0][1].slug

    if selected_broad is None:
        for slug in confidence_by_slug:
            definition = definitions[slug]
            if definition.is_broad:
                selected_broad = definition.slug
                break
            if definition.parent is not None:
                selected_broad = definition.parent
                break

    if selected_broad is None:
        return 0

    target_slugs = {selected_broad}
    target_slugs.update(
        slug
        for slug, definition in definitions.items()
        if not definition.is_broad
        and definition.parent == selected_broad
        and slug in confidence_by_slug
    )

    broad_confidence = max(
        (
            value
            for slug, value in confidence_by_slug.items()
            if slug == selected_broad or definitions[slug].parent == selected_broad
        ),
        default=0.6,
    )
    confidence_by_slug.setdefault(selected_broad, broad_confidence)

    tag_ids = {
        slug: session.scalar(select(Tag.id).where(Tag.slug == slug)) for slug in target_slugs
    }

    inserted = 0
    for slug in target_slugs:
        tag_id = tag_ids.get(slug)
        if tag_id is None:
            continue
        confidence = confidence_by_slug.get(slug, 0.6)
        existing = session.scalar(
            select(ItemTag).where(ItemTag.item_id == item_id, ItemTag.tag_id == tag_id)
        )
        if existing is not None:
            if existing.source == TagSource.MANUAL:
                continue
            existing.confidence = confidence
            existing.source = TagSource(source)
            session.flush()
            inserted += 1
            continue
        session.add(
            ItemTag(
                item_id=item_id,
                tag_id=tag_id,
                confidence=confidence,
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
