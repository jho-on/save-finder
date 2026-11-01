from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import Item, ItemStatus, ItemTag, PlatformSource, Tag, TagLevel
from save_finder.repositories.tags import add_item_tags, sync_taxonomy


def _item(session: Session) -> Item:
    item = Item(
        platform="instagram",
        external_id="rule-test",
        url="https://www.instagram.com/p/rule-test/",
        source=PlatformSource.EXPORT,
        status=ItemStatus.ENRICHED,
    )
    session.add(item)
    session.flush()
    return item


def _tagged(session: Session, item_id: int) -> tuple[set[str], set[str]]:
    rows = list(
        session.execute(
            select(Tag).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item_id)
        ).scalars()
    )
    broad = {tag.slug for tag in rows if tag.level == TagLevel.BROAD}
    specific = {tag.slug for tag in rows if tag.level == TagLevel.SPECIFIC}
    return broad, specific


def test_persists_one_broad_and_its_specifics(session: Session) -> None:
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    sync_taxonomy(session, taxonomy)
    item = _item(session)

    add_item_tags(session, item.id, ["receitas", "carros"])
    session.commit()

    broad, specific = _tagged(session, item.id)
    assert broad == {"comida-e-bebida"}
    assert specific == {"receitas"}


def test_specific_without_broad_adds_parent(session: Session) -> None:
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    sync_taxonomy(session, taxonomy)
    item = _item(session)

    add_item_tags(session, item.id, ["receitas"])
    session.commit()

    broad, specific = _tagged(session, item.id)
    assert broad == {"comida-e-bebida"}
    assert specific == {"receitas"}


def test_existing_manual_broad_is_kept(session: Session) -> None:
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    sync_taxonomy(session, taxonomy)
    item = _item(session)

    add_item_tags(session, item.id, ["tecnologia"], source="manual")
    add_item_tags(session, item.id, ["receitas", "programacao"])
    session.commit()

    broad, specific = _tagged(session, item.id)
    assert broad == {"tecnologia"}
    assert specific == {"programacao"}
