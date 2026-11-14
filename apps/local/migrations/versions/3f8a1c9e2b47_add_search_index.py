"""add derived FTS5 search index

The index is fully rebuildable from items, transcripts and item_tags.
It is never a source of truth; ``save-finder db reindex`` refreshes it.

Revision ID: 3f8a1c9e2b47
Revises: 8b2c4f1a7d90
Create Date: 2026-11-14 14:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f8a1c9e2b47"
down_revision: str | None = "8b2c4f1a7d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE items_fts USING fts5(
            item_id UNINDEXED,
            caption,
            author,
            external_id,
            transcript,
            tags,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
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
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS items_fts")
