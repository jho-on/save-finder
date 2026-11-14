from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.errors import InvalidTagSelection
from save_finder.models import Item, ItemStatus, ItemTag, PlatformSource, PostType, Tag
from save_finder.repositories.tags import (
    add_item_tags,
    apply_manual_tag_selection,
    excluded_tag_slugs,
    replace_auto_tags,
    sync_taxonomy,
)


@pytest.fixture()
def taxonomy() -> Taxonomy:
    return Taxonomy.load(load_settings().taxonomy_path)


@pytest.fixture()
def item(session: Session, taxonomy: Taxonomy) -> Item:
    sync_taxonomy(session, taxonomy)
    item = Item(
        platform="instagram",
        external_id="manual-tags",
        url="https://www.instagram.com/p/manual-tags/",
        source=PlatformSource.EXPORT,
        caption="receitas de bolo",
        post_type=PostType.IMAGE,
        media_count=1,
        status=ItemStatus.ENRICHED,
    )
    session.add(item)
    session.flush()
    add_item_tags(session, item.id, ["receitas"], taxonomy=taxonomy)
    session.flush()
    return item


def _tag_slugs(session: Session, item: Item) -> set[str]:
    return set(
        session.scalars(
            select(Tag.slug)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id)
        ).all()
    )


def test_manual_selection_adds_tags_and_propagates_broad(
    session: Session, item: Item, taxonomy: Taxonomy
) -> None:
    apply_manual_tag_selection(session, item.id, ["receitas", "drinks"], taxonomy=taxonomy)

    assert _tag_slugs(session, item) == {"comida-e-bebida", "receitas", "drinks"}
    assert excluded_tag_slugs(session, item.id) == set()

    sources = dict(
        session.execute(
            select(Tag.slug, ItemTag.source)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id)
        ).all()
    )
    assert sources["receitas"].value == "auto"
    assert sources["drinks"].value == "manual"
    assert sources["comida-e-bebida"].value == "manual"


def test_removing_tag_records_exclusion_and_reclassification_respects_it(
    session: Session, item: Item, taxonomy: Taxonomy
) -> None:
    apply_manual_tag_selection(session, item.id, ["drinks"], taxonomy=taxonomy)
    apply_manual_tag_selection(session, item.id, ["receitas"], taxonomy=taxonomy)

    assert _tag_slugs(session, item) == {"comida-e-bebida", "receitas"}
    assert excluded_tag_slugs(session, item.id) == {"drinks"}

    replace_auto_tags(
        session,
        item.id,
        ["receitas", "drinks"],
        taxonomy=taxonomy,
    )
    assert "drinks" not in _tag_slugs(session, item)
    assert excluded_tag_slugs(session, item.id) == {"drinks"}

    replace_auto_tags(
        session,
        item.id,
        ["receitas", "drinks"],
        taxonomy=taxonomy,
        restore_excluded=True,
    )
    assert {"comida-e-bebida", "receitas", "drinks"} <= _tag_slugs(session, item)
    assert excluded_tag_slugs(session, item.id) == set()


def test_manual_tags_survive_reclassification(
    session: Session, item: Item, taxonomy: Taxonomy
) -> None:
    apply_manual_tag_selection(session, item.id, ["receitas", "drinks"], taxonomy=taxonomy)

    replace_auto_tags(session, item.id, ["programacao"], taxonomy=taxonomy)

    # The manual specific keeps its manual broad parent. The untouched
    # automatic specific is replaced by the new automatic classification.
    assert _tag_slugs(session, item) == {"comida-e-bebida", "drinks"}


def test_manual_selection_rejects_more_than_one_broad(
    session: Session, item: Item, taxonomy: Taxonomy
) -> None:
    with pytest.raises(InvalidTagSelection):
        apply_manual_tag_selection(
            session,
            item.id,
            ["receitas", "programacao"],
            taxonomy=taxonomy,
        )


def test_manual_selection_rejects_unknown_tag(
    session: Session, item: Item, taxonomy: Taxonomy
) -> None:
    with pytest.raises(InvalidTagSelection):
        apply_manual_tag_selection(session, item.id, ["not-in-taxonomy"], taxonomy=taxonomy)
