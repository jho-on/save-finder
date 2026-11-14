"""Automatic tag replacement and offline reclassification rules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.classify.fuse import classify_persisted_item
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings
from save_finder.models import Item, ItemTag, PlatformSource, PostType, Tag
from save_finder.providers.base import TranscriptResult, TranscriptSegment
from save_finder.repositories.analysis import save_embedding, save_transcript
from save_finder.repositories.tags import add_item_tags, replace_auto_tags, sync_taxonomy


def _prepare(session: Session) -> tuple[Item, Taxonomy]:
    taxonomy = Taxonomy.load(load_settings().taxonomy_path)
    sync_taxonomy(session, taxonomy)
    item = Item(
        platform="instagram",
        external_id="tags-1",
        url="https://www.instagram.com/p/tags-1/",
        source=PlatformSource.EXPORT,
        post_type=PostType.IMAGE,
    )
    session.add(item)
    session.flush()
    return item, taxonomy


def _slugs(session: Session, item: Item) -> set[str]:
    rows = session.execute(
        select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item.id)
    )
    return set(rows.scalars().all())


def test_replace_auto_tags_removes_stale_automatic_tags(session: Session) -> None:
    item, _ = _prepare(session)
    add_item_tags(session, item.id, ["comida-e-bebida", "receitas"])
    assert _slugs(session, item) == {"comida-e-bebida", "receitas"}

    replace_auto_tags(session, item.id, ["tecnologia", "ia"])

    assert _slugs(session, item) == {"tecnologia", "ia"}


def test_manual_tag_survives_reclassification(session: Session) -> None:
    item, _ = _prepare(session)
    add_item_tags(session, item.id, ["receitas"], source="manual")
    assert _slugs(session, item) == {"comida-e-bebida", "receitas"}

    replace_auto_tags(session, item.id, ["tecnologia", "ia"])

    assert _slugs(session, item) == {"comida-e-bebida", "receitas"}


def test_offline_reclassification_reads_persisted_signals(session: Session) -> None:
    item, taxonomy = _prepare(session)
    save_transcript(
        session,
        item.id,
        TranscriptResult(
            language="pt",
            text="um tutorial sobre programacao e IA",
            segments=(TranscriptSegment(0.0, 2.0, "um tutorial sobre programacao e IA"),),
            model="fake-asr",
        ),
    )
    save_embedding(session, item.id, [0.6, 0.8], model="fake-vision")
    session.commit()
    session.refresh(item)

    class FakeLLM:
        name = "fake-llm"

        def complete(self, *, system: str, prompt: str) -> str:
            assert "um tutorial sobre programacao e IA" in prompt
            return (
                '{"broad":"tecnologia","specific":["programacao","ia"],'
                '"confidence":0.9,"evidence":"tutorial"}'
            )

    result = classify_persisted_item(item, taxonomy, llm=FakeLLM())

    assert result.tags == ("tecnologia", "ia", "programacao")
    assert result.confidences[0] == 0.9


def test_evidence_is_persisted_on_automatic_tags(session: Session) -> None:
    item, _ = _prepare(session)
    replace_auto_tags(
        session,
        item.id,
        ["animais"],
        confidences=[0.8],
        evidence="llm: pet video",
    )

    rows = session.scalars(select(ItemTag).where(ItemTag.item_id == item.id)).all()
    assert rows
    assert all(row.evidence == "llm: pet video" for row in rows)
