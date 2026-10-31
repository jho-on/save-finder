"""Tag persistence and taxonomy synchronization."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.models import Tag, TagLevel, utc_now_iso


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
