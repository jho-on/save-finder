"""SQLAlchemy models for the local SaveFinder database."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now_iso() -> str:
    """Timestamp in ISO 8601 UTC, as required by the data invariants."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class PlatformSource(StrEnum):
    EXPORT = "export"
    SHARE = "share"
    MANUAL = "manual"


class ItemStatus(StrEnum):
    PENDING = "pending"
    ENRICHED = "enriched"
    ANALYZED = "analyzed"
    CLASSIFIED = "classified"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class PostType(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    CAROUSEL = "carousel"


class MediaKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"


class TagLevel(StrEnum):
    BROAD = "broad"
    SPECIFIC = "specific"


class TagSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


def _enum(enum_type: type[StrEnum], name: str, *, length: int = 32) -> SAEnum:
    return SAEnum(
        enum_type,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
        create_constraint=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON,
        list[dict[str, Any]]: JSON,
    }


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_items_platform_external_id"),
        Index("ix_items_platform_status", "platform", "status"),
        Index("ix_items_saved_at", "saved_at"),
        Index("ix_items_collection", "collection"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[PlatformSource] = mapped_column(
        _enum(PlatformSource, "platform_source", length=16),
        nullable=False,
        default=PlatformSource.EXPORT,
    )
    author: Mapped[str | None] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(Text)
    post_type: Mapped[PostType | None] = mapped_column(_enum(PostType, "post_type", length=16))
    media_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    saved_at: Mapped[str | None] = mapped_column(String(32))
    collection: Mapped[str | None] = mapped_column(String(255))
    thumb_path: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[ItemStatus] = mapped_column(
        _enum(ItemStatus, "item_status", length=16),
        nullable=False,
        default=ItemStatus.PENDING,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        String(32), nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )
    enriched_at: Mapped[str | None] = mapped_column(String(32))
    analyzed_at: Mapped[str | None] = mapped_column(String(32))
    classified_at: Mapped[str | None] = mapped_column(String(32))

    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="MediaAsset.position",
    )
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False
    )
    embedding: Mapped[Embedding | None] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False
    )
    item_tags: Mapped[list[ItemTag]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Item {self.platform}:{self.external_id} status={self.status}>"


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("item_id", "position", name="uq_media_assets_item_position"),
        Index("ix_media_assets_item_id", "item_id"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[MediaKind] = mapped_column(_enum(MediaKind, "media_kind", length=16))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)

    item: Mapped[Item] = relationship(back_populates="media_assets")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)

    item: Mapped[Item] = relationship(back_populates="transcript")


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    # float32 vector, encoded by the repository layer.
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)

    item: Mapped[Item] = relationship(back_populates="embedding")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (Index("ix_tags_parent_id", "parent_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[TagLevel] = mapped_column(_enum(TagLevel, "tag_level", length=16))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="SET NULL"))
    visual_description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        String(32), nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )

    parent: Mapped[Tag | None] = relationship(remote_side="Tag.id", back_populates="children")
    children: Mapped[list[Tag]] = relationship(back_populates="parent")
    item_tags: Mapped[list[ItemTag]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (
        UniqueConstraint("item_id", "tag_id", name="uq_item_tags_item_tag"),
        Index("ix_item_tags_tag_id", "tag_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[TagSource] = mapped_column(
        _enum(TagSource, "tag_source", length=16),
        nullable=False,
        default=TagSource.AUTO,
    )
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        String(32), nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )

    item: Mapped[Item] = relationship(back_populates="item_tags")
    tag: Mapped[Tag] = relationship(back_populates="item_tags")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("success_count >= 0", name="success_non_negative"),
        CheckConstraint("failure_count >= 0", name="failure_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    finished_at: Mapped[str | None] = mapped_column(String(32))
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
