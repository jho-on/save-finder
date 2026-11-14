"""Full-text search over items, derived and rebuildable.

``items_fts`` (created by migration ``3f8a1c9e2b47``) aggregates the caption,
author, external id, transcript text and tag display names of one item. It
holds no independent data: ``rebuild_search_index`` reconstructs it from
``items``, ``transcripts`` and ``item_tags`` at any time, and row-level
``sync_search_index_item`` keeps it fresh after each mutation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from save_finder.models import Item, ItemStatus

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_TOKENS = 8

_INSERT_SQL = """
INSERT INTO items_fts (item_id, caption, author, external_id, transcript, tags)
SELECT i.id,
       coalesce(i.caption, ''),
       coalesce(i.author, ''),
       i.external_id,
       coalesce(t.text, ''),
       coalesce(g.tags, '')
FROM items i
LEFT JOIN transcripts t ON t.item_id = i.id
LEFT JOIN (
    SELECT it.item_id AS item_id, group_concat(tg.name, ' ') AS tags
    FROM item_tags it
    JOIN tags tg ON tg.id = it.tag_id
    GROUP BY it.item_id
) g ON g.item_id = i.id
"""


def build_match(query: str) -> str | None:
    """Turn free text into an FTS5 MATCH expression.

    Tokens are ANDed with prefix matching, so a partial word matches its
    completions ("receit" finds "receita"). Tokens come from ``\\w+`` so the
    expression never carries stray quotes.
    """
    tokens = _TOKEN_RE.findall(query.lower())[:_MAX_TOKENS]
    if not tokens:
        return None
    return " AND ".join(f'"{token}"*' for token in tokens)


def rebuild_search_index(session: Session) -> int:
    """Drop and repopulate the derived index. Returns the row count."""
    session.execute(text("DELETE FROM items_fts"))
    result = session.execute(text(_INSERT_SQL))
    session.flush()
    return result.rowcount if result.rowcount is not None else 0


def sync_search_index_item(session: Session, item_id: int) -> None:
    """Refresh one row of the derived index from the source tables."""
    session.execute(text("DELETE FROM items_fts WHERE item_id = :id"), {"id": item_id})
    session.execute(text(f"{_INSERT_SQL} WHERE i.id = :item_id"), {"item_id": item_id})
    session.flush()


def clear_search_index(session: Session) -> None:
    """Empty the index; tolerate databases created before the FTS migration."""
    has_table = session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'items_fts'")
    ).scalar()
    if has_table:
        session.execute(text("DELETE FROM items_fts"))
        session.flush()


def search_items(
    session: Session,
    query: str,
    *,
    status: ItemStatus | None = None,
    tag_slug: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[Sequence[Item], int]:
    """Search items by caption, author, external id, transcript and tags.

    Ranked by BM25 (best match first). Returns the matching items (in rank
    order) and the total number of matches.
    """
    match = build_match(query)
    if match is None:
        return [], 0

    params: dict[str, object] = {"match": match, "limit": limit, "offset": offset}
    joins = ["JOIN items_fts ON CAST(items_fts.item_id AS INTEGER) = items.id"]
    filters = ["items_fts MATCH :match"]
    if status is not None:
        filters.append("items.status = :status")
        params["status"] = status.value
    if tag_slug is not None:
        joins.append(
            "JOIN item_tags it_search ON it_search.item_id = items.id "
            "JOIN tags tg_search ON tg_search.id = it_search.tag_id "
            "AND tg_search.slug = :tag_slug"
        )
        params["tag_slug"] = tag_slug

    from_clause = " FROM items " + " ".join(joins)
    where_clause = " WHERE " + " AND ".join(filters)

    total = (
        session.execute(
            text(f"SELECT count(DISTINCT items.id){from_clause}{where_clause}"), params
        ).scalar()
        or 0
    )
    rows = session.execute(
        text(
            "SELECT DISTINCT items.id, bm25(items_fts) AS rank"
            + from_clause
            + where_clause
            + " ORDER BY rank, items.id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).all()
    ids = [row.id for row in rows]
    if not ids:
        return [], total
    found = {item.id: item for item in session.scalars(select(Item).where(Item.id.in_(ids))).all()}
    return [found[item_id] for item_id in ids if item_id in found], total
