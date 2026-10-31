"""Platform adapter contract.

The core only talks to adapters through this module. No platform-specific
rule is allowed outside ``save_finder/platforms/<slug>.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class SavedItem:
    """Normalized item produced by an export parser."""

    platform: str
    external_id: str
    url: str
    source: str = "export"
    author: str | None = None
    collection: str | None = None
    saved_at: str | None = None


@dataclass(frozen=True, slots=True)
class Metadata:
    """Public metadata fetched during enrichment.

    ``thumbnail_url`` is only a transport value: it is downloaded and never
    persisted in the database.
    """

    author: str | None = None
    caption: str | None = None
    post_type: str | None = None
    media_count: int = 0
    duration_seconds: float | None = None
    thumbnail_url: str | None = None


class PlatformAdapter(Protocol):
    """Everything the core needs to know about a social network."""

    slug: str

    def parse_export(self, path: Path) -> Iterable[SavedItem]:
        """Yield normalized items from an official export."""
        ...

    def normalize_url(self, url: str) -> tuple[str, str]:
        """Return ``(canonical_url, external_id)`` or raise UnsupportedURL."""
        ...

    def enrich(self, item: SavedItem) -> Metadata:
        """Fetch public metadata for one item."""
        ...

    def download(self, item: SavedItem, dest: Path) -> Path:
        """Download every media piece of one item into ``dest``."""
        ...
