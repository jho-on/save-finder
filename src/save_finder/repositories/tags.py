"""Tag persistence and taxonomy synchronization."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.errors import InvalidTagSelection
from save_finder.models import (
    ItemTag,
    ItemTagExclusion,
    Tag,
    TagLevel,
    TagSource,
    utc_now_iso,
)


def excluded_tag_slugs(session: Session, item_id: int) -> set[str]:
    """Return slugs the user removed from the item."""
    return set(
        session.scalars(
            select(Tag.slug)
            .join(ItemTagExclusion, ItemTagExclusion.tag_id == Tag.id)
            .where(ItemTagExclusion.item_id == item_id)
        ).all()
    )


def excluded_tags_for_items(session: Session, item_ids: Sequence[int]) -> dict[int, list[str]]:
    """Batch-load excluded tag slugs for the library listing."""
    result: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
    if not item_ids:
        return result
    rows = session.execute(
        select(ItemTagExclusion.item_id, Tag.slug)
        .join(Tag, Tag.id == ItemTagExclusion.tag_id)
        .where(ItemTagExclusion.item_id.in_(item_ids))
        .order_by(Tag.name)
    ).all()
    for item_id, slug in rows:
        result.setdefault(item_id, []).append(slug)
    return result


def clear_item_tag_exclusions(
    session: Session,
    item_id: int,
    *,
    tag_slugs: Sequence[str] | None = None,
) -> int:
    """Allow automatic classification to emit the removed tags again."""
    statement = delete(ItemTagExclusion).where(ItemTagExclusion.item_id == item_id)
    if tag_slugs is not None:
        tag_ids = select(Tag.id).where(Tag.slug.in_(tuple(tag_slugs)))
        statement = statement.where(ItemTagExclusion.tag_id.in_(tag_ids))
    result = session.execute(statement)
    session.flush()
    return result.rowcount or 0


def _add_exclusion(session: Session, item_id: int, tag_id: int) -> None:
    existing = session.scalar(
        select(ItemTagExclusion.id).where(
            ItemTagExclusion.item_id == item_id,
            ItemTagExclusion.tag_id == tag_id,
        )
    )
    if existing is None:
        session.add(ItemTagExclusion(item_id=item_id, tag_id=tag_id))


def apply_manual_tag_selection(
    session: Session,
    item_id: int,
    tag_slugs: Sequence[str],
    *,
    taxonomy: Taxonomy | None = None,
) -> int:
    """Replace the item's selection while preserving automatic tags.

    Tags that stay selected keep their previous source: an automatic tag the
    user did not touch remains automatic and may be replaced by a future
    reclassification. New tags become manual. Removed tags are recorded as
    exclusions so automatic classification does not bring them back until the
    user asks for it. Adding a manual specific tag locks its broad parent as
    manual too, so a later reclassification cannot leave the specific tag
    without the broad tag the parent rule requires.
    """
    taxonomy = taxonomy or Taxonomy.load(load_settings().taxonomy_path)
    definitions = {tag.slug: tag for tag in (*taxonomy.broad, *taxonomy.specific)}

    requested: list[str] = []
    seen: set[str] = set()
    for slug in tag_slugs:
        if slug in seen:
            continue
        if slug not in definitions:
            raise InvalidTagSelection(f"unknown tag: {slug}")
        seen.add(slug)
        requested.append(slug)

    broad_requested = {slug for slug in requested if definitions[slug].is_broad}
    parent_requested = {
        definitions[slug].parent
        for slug in requested
        if not definitions[slug].is_broad and definitions[slug].parent is not None
    }

    if len(broad_requested) > 1:
        raise InvalidTagSelection("select at most one broad tag")
    if len(parent_requested) > 1:
        raise InvalidTagSelection("specific tags must belong to the same broad tag")
    if broad_requested and parent_requested and broad_requested != parent_requested:
        raise InvalidTagSelection("specific tags must belong to the selected broad tag")

    target = set(requested)
    target.update(parent for parent in parent_requested if parent is not None)

    tags_by_slug = {
        tag.slug: tag for tag in session.scalars(select(Tag).where(Tag.slug.in_(target))).all()
    }

    current_rows = session.execute(
        select(ItemTag, Tag).join(Tag, Tag.id == ItemTag.tag_id).where(ItemTag.item_id == item_id)
    ).all()
    current_by_slug = {tag.slug: (item_tag, tag) for item_tag, tag in current_rows}

    newly_added: set[str] = set()
    for slug in target:
        tag = tags_by_slug.get(slug)
        if tag is None:
            continue
        if slug not in current_by_slug:
            session.add(
                ItemTag(
                    item_id=item_id,
                    tag_id=tag.id,
                    confidence=1.0,
                    source=TagSource.MANUAL,
                    evidence="manual selection",
                )
            )
            newly_added.add(slug)
        session.execute(
            delete(ItemTagExclusion).where(
                ItemTagExclusion.item_id == item_id,
                ItemTagExclusion.tag_id == tag.id,
            )
        )

    # A manually chosen specific tag implies its broad parent. If the parent
    # was automatic, lock it as manual so a later reclassification cannot
    # leave the specific tag without its broad tag.
    for slug in newly_added:
        parent = definitions[slug].parent
        if parent is None or parent not in current_by_slug:
            continue
        item_tag, _ = current_by_slug[parent]
        if item_tag.source != TagSource.MANUAL:
            item_tag.source = TagSource.MANUAL
            item_tag.confidence = 1.0
            item_tag.evidence = "manual selection"

    for slug, (item_tag, tag) in current_by_slug.items():
        if slug in target:
            continue
        session.delete(item_tag)
        _add_exclusion(session, item_id, tag.id)

    session.flush()
    return len(target)


def add_item_tags(
    session: Session,
    item_id: int,
    tag_slugs: Sequence[str],
    *,
    confidences: Sequence[float] | None = None,
    source: str = "auto",
    taxonomy: Taxonomy | None = None,
) -> int:
    """Persist taxonomy tags for one item, preserving parent rules.

    The product rule is one broad tag per item plus the specific tags that
    belong to it. Incoming specifics select their broad parent; an existing
    broad tag already on the item (manual tags first) wins over a new one.
    Only tags present in the taxonomy are inserted; duplicates for the same
    item are updated unless the stored tag is manual.
    """
    taxonomy = taxonomy or Taxonomy.load(load_settings().taxonomy_path)
    definitions = {tag.slug: tag for tag in (*taxonomy.broad, *taxonomy.specific)}
    excluded = (
        excluded_tag_slugs(session, item_id) if TagSource(source) == TagSource.AUTO else set()
    )

    confidence_by_slug: dict[str, float] = {}
    for index, slug in enumerate(tag_slugs):
        if slug not in definitions or slug in excluded:
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
    if selected_broad in excluded:
        return 0

    target_slugs = {selected_broad}
    target_slugs.update(
        slug
        for slug, definition in definitions.items()
        if not definition.is_broad
        and definition.parent == selected_broad
        and slug in confidence_by_slug
        and slug not in excluded
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


def replace_auto_tags(
    session: Session,
    item_id: int,
    tag_slugs: Sequence[str],
    *,
    confidences: Sequence[float] | None = None,
    evidence: str | None = None,
    taxonomy: Taxonomy | None = None,
    restore_excluded: bool = False,
) -> int:
    """Replace automatic tags while preserving manual corrections.

    Reclassification must not accumulate stale automatic tags. Manual tags are
    left untouched and continue to win over the new automatic result. Tags the
    user removed stay excluded unless ``restore_excluded`` is true.
    """
    if restore_excluded:
        clear_item_tag_exclusions(session, item_id)
    session.execute(
        delete(ItemTag).where(
            ItemTag.item_id == item_id,
            ItemTag.source == TagSource.AUTO,
        )
    )
    session.flush()
    inserted = add_item_tags(
        session,
        item_id,
        tag_slugs,
        confidences=confidences,
        source="auto",
        taxonomy=taxonomy,
    )
    if evidence:
        for item_tag in session.scalars(
            select(ItemTag).where(
                ItemTag.item_id == item_id,
                ItemTag.source == TagSource.AUTO,
            )
        ):
            item_tag.evidence = evidence
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
