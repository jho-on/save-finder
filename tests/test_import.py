from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import project_root
from save_finder.models import Item, Tag
from save_finder.platforms.instagram import InstagramAdapter
from save_finder.repositories.items import count_items, import_saved_items
from save_finder.repositories.tags import sync_taxonomy


def _items(path: Path):
    return list(InstagramAdapter().parse_export(path))


def test_import_is_idempotent(session: Session, instagram_export: Path) -> None:
    first = import_saved_items(session, _items(instagram_export))
    assert (first.new, first.existing) == (4, 0)

    second = import_saved_items(session, _items(instagram_export))
    assert (second.new, second.existing) == (0, 4)
    assert count_items(session) == 4

    aaa = session.scalar(select(Item).where(Item.external_id == "AAA111"))
    assert aaa is not None
    assert aaa.collection == "Receitas"
    assert aaa.platform == "instagram"
    assert aaa.status.value == "pending"


def test_import_fills_missing_collection(session: Session, instagram_export: Path) -> None:
    adapter = InstagramAdapter()
    items = [
        item for item in adapter.parse_export(instagram_export) if item.external_id == "AAA111"
    ]
    items[0].collection = None
    import_saved_items(session, items)

    refreshed = list(adapter.parse_export(instagram_export))
    import_saved_items(session, refreshed)

    aaa = session.scalar(select(Item).where(Item.external_id == "AAA111"))
    assert aaa is not None
    assert aaa.collection == "Receitas"


def test_sync_taxonomy(session: Session) -> None:
    taxonomy = Taxonomy.load(project_root() / "config" / "taxonomy.yaml")

    created, updated = sync_taxonomy(session, taxonomy)
    assert (created, updated) == (len(taxonomy), 0)

    created_again, updated_again = sync_taxonomy(session, taxonomy)
    assert (created_again, updated_again) == (0, 0)

    receitas = session.scalar(select(Tag).where(Tag.slug == "receitas"))
    assert receitas is not None
    assert receitas.level.value == "specific"
    assert receitas.parent is not None
    assert receitas.parent.slug == "comida-e-bebida"
    assert session.scalar(select(Tag).where(Tag.slug == "comida-e-bebida")).parent is None
