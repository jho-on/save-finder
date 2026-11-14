"""add item tag exclusions

Revision ID: 8b2c4f1a7d90
Revises: 396ef97e34df
Create Date: 2026-11-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b2c4f1a7d90"
down_revision: str | None = "396ef97e34df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_tag_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name=op.f("fk_item_tag_exclusions_item_id_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name=op.f("fk_item_tag_exclusions_tag_id_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_tag_exclusions")),
        sa.UniqueConstraint("item_id", "tag_id", name="uq_item_tag_exclusions_item_tag"),
    )
    with op.batch_alter_table("item_tag_exclusions", schema=None) as batch_op:
        batch_op.create_index("ix_item_tag_exclusions_item_id", ["item_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("item_tag_exclusions", schema=None) as batch_op:
        batch_op.drop_index("ix_item_tag_exclusions_item_id")

    op.drop_table("item_tag_exclusions")
