from __future__ import annotations

from save_finder.classify.fuse import classify_item
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import load_settings


def test_classify_respects_taxonomy_and_emits_tags() -> None:
    settings = load_settings()
    taxonomy = Taxonomy.load(settings.taxonomy_path)
    # Minimal item-like object for test
    from save_finder.models import Item, PostType, ItemStatus
    from save_finder.db import make_engine, make_session_factory, session_scope
    with session_scope(make_session_factory(make_engine(settings))) as session:
        item = session.query(Item).filter(Item.external_id == "DeJ-_uAAuz4").first()
        if item is not None:
            result = classify_item(item, taxonomy)
            assert len(result.tags) > 0
            assert all(t in {s.slug for s in (*taxonomy.broad, *taxonomy.specific)} for t in result.tags)
            assert result.error is None
