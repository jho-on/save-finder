"""FTS5 search index: build, rebuild, incremental sync and ranking."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import Item, ItemStatus, PlatformSource, PostType
from save_finder.providers.base import TranscriptResult
from save_finder.repositories.analysis import save_transcript
from save_finder.repositories.search import (
    build_match,
    clear_search_index,
    rebuild_search_index,
    search_items,
    sync_search_index_item,
)
from save_finder.repositories.tags import (
    add_item_tags,
    apply_manual_tag_selection,
    sync_taxonomy,
)


@pytest.fixture()
def taxonomy() -> Taxonomy:
    return Taxonomy.load(load_settings().taxonomy_path)


@pytest.fixture()
def item_factory(session: Session):
    def make(caption: str | None, external_id: str, *, author: str | None = None) -> Item:
        item = Item(
            platform="instagram",
            external_id=external_id,
            url=f"https://www.instagram.com/p/{external_id}/",
            source=PlatformSource.EXPORT,
            author=author,
            caption=caption,
            post_type=PostType.VIDEO,
            media_count=1,
            status=ItemStatus.ENRICHED,
        )
        session.add(item)
        session.flush()
        return item

    return make


def test_build_match_ands_tokens_with_prefix() -> None:
    assert build_match("Receita de Bolos") == '"receita"* AND "de"* AND "bolos"*'


def test_build_match_strips_unsafe_characters() -> None:
    assert build_match('" OR "') == '"or"*'
    assert build_match("casa; DROP TABLE items") == '"casa"* AND "drop"* AND "table"* AND "items"*'


def test_build_match_rejects_empty_input() -> None:
    assert build_match("   ") is None
    assert build_match("***") is None


def test_rebuild_and_search_across_caption_transcript_and_tags(
    session: Session, item_factory, taxonomy
) -> None:
    by_caption = item_factory("bolo de fubá super macio", "search-caption")
    by_transcript = item_factory(None, "search-transcript")
    by_tag = item_factory(None, "search-tag")
    unrelated = item_factory("outro assunto completamente diferente", "search-none")

    sync_taxonomy(session, taxonomy)
    save_transcript(
        session,
        by_transcript.id,
        TranscriptResult(language="pt", text="como fazer massa caseira", segments=(), model="fake"),
    )
    add_item_tags(session, by_tag.id, ["receitas"], taxonomy=taxonomy)
    rebuild_search_index(session)

    found, total = search_items(session, "bolo")
    assert [item.id for item in found] == [by_caption.id]
    assert total == 1

    found, total = search_items(session, "massa")
    assert [item.id for item in found] == [by_transcript.id]
    assert total == 1

    found, _total = search_items(session, "receit")
    assert [item.id for item in found] == [by_tag.id]
    assert total == 1

    found, _ = search_items(session, "assunto")
    assert [item.id for item in found] == [unrelated.id]


def test_search_is_diacritic_insensitive(session: Session, item_factory) -> None:
    item = item_factory("pão de queijo com fécula", "search-diacritics")
    rebuild_search_index(session)

    found, _total = search_items(session, "fécula")
    assert [row.id for row in found] == [item.id]
    found, _total = search_items(session, "fecula")
    assert [row.id for row in found] == [item.id]


def test_search_prefix_matches_word_completions(session: Session, item_factory) -> None:
    item = item_factory("receitas para o dia a dia", "search-prefix")
    rebuild_search_index(session)

    found, _total = search_items(session, "receit")
    assert [row.id for row in found] == [item.id]


def test_sync_keeps_index_incremental(session: Session, item_factory, taxonomy) -> None:
    item = item_factory("temporada de compras", "search-sync")
    rebuild_search_index(session)

    found, _ = search_items(session, "oficina")
    assert found == []

    item.caption = "oficina aberta no sábado"
    session.flush()
    sync_search_index_item(session, item.id)
    found, _ = search_items(session, "oficina")
    assert [row.id for row in found] == [item.id]

    found, _ = search_items(session, "compras")
    assert found == []


def test_tag_selection_update_refreshes_index(session: Session, item_factory, taxonomy) -> None:
    item = item_factory(None, "search-tags-sync")
    sync_taxonomy(session, taxonomy)
    add_item_tags(session, item.id, ["receitas"], taxonomy=taxonomy)
    rebuild_search_index(session)

    found, _ = search_items(session, "receit")
    assert [row.id for row in found] == [item.id]

    apply_manual_tag_selection(session, item.id, [], taxonomy=taxonomy)
    found, _ = search_items(session, "receit")
    assert found == []


def test_search_filters_by_status_and_tag(session: Session, item_factory, taxonomy) -> None:
    pending = item_factory("receita pendente", "search-pending")
    pending.status = ItemStatus.PENDING
    classified = item_factory("receita classificada", "search-classified")
    session.flush()
    sync_taxonomy(session, taxonomy)
    add_item_tags(session, classified.id, ["tecnologia"], taxonomy=taxonomy)
    rebuild_search_index(session)

    found, total = search_items(session, "receita", status=ItemStatus.PENDING)
    assert [row.id for row in found] == [pending.id]
    assert total == 1

    found, total = search_items(session, "receita", tag_slug="tecnologia")
    assert [row.id for row in found] == [classified.id]
    assert total == 1

    found, total = search_items(session, "receita", tag_slug="receitas")
    assert found == []
    assert total == 0


def test_clear_and_rebuild_round_trip(session: Session, item_factory) -> None:
    item = item_factory("bolo de cenoura", "search-rebuild")
    rebuild_search_index(session)

    clear_search_index(session)
    found, total = search_items(session, "bolo")
    assert found == [] and total == 0

    rebuild_search_index(session)
    found, total = search_items(session, "bolo")
    assert [row.id for row in found] == [item.id]
    assert total == 1
