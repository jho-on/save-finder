"""Instagram adapter.

The saved export is JSON with localized keys, so the parser never depends on
English field names. It walks every JSON file below the given directory and
recognizes media references by the presence of an Instagram post ``href``.
Collection names are recognized by localized name-like keys.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from save_finder.errors import ExportImportError, UnsupportedURL
from save_finder.platforms.base import SavedItem, YtDlpAdapter

logger = logging.getLogger(__name__)

POST_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)(?:/|$)")
NAME_TOKENS = ("name", "nome", "nombre", "nom", "naam")


class InstagramAdapter(YtDlpAdapter):
    slug = "instagram"

    def __init__(self) -> None:
        self.skipped_urls: list[str] = []
        self.duplicate_count = 0
        self.unreadable_files: list[str] = []
        self._seen: dict[str, SavedItem] = {}

    # -- URL normalization -------------------------------------------------

    def normalize_url(self, url: str) -> tuple[str, str]:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host == "instagram.com" or host.endswith(".instagram.com")
        ):
            raise UnsupportedURL(f"not an Instagram URL: {url}")
        match = POST_PATH.match(parsed.path)
        if match is None:
            raise UnsupportedURL(f"not an Instagram post URL: {url}")
        kind, shortcode = match.groups()
        if kind == "reels":
            kind = "reel"
        return f"https://www.instagram.com/{kind}/{shortcode}/", shortcode

    # -- Export parsing ----------------------------------------------------

    def parse_export(self, path: Path) -> Iterable[SavedItem]:
        path = Path(path)
        self.skipped_urls = []
        self.duplicate_count = 0
        self.unreadable_files = []
        self._seen = {}

        files = self._candidate_files(path)
        if not files:
            raise ExportImportError(f"no JSON export files found in {path}")

        for file in files:
            document = self._load_json(file)
            if document is None:
                continue
            self._walk(document, author=None, collection=None, timestamp=None)

        if not self._seen and self.unreadable_files:
            raise ExportImportError(f"no readable JSON export files in {path}")
        return list(self._seen.values())

    def _candidate_files(self, path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if not path.is_dir():
            raise ExportImportError(f"export path does not exist: {path}")

        files = sorted(path.rglob("*.json"))
        if "saved" in path.name.casefold():
            return files
        # Pointing at the export root must not ingest likes or comments.
        return [
            file
            for file in files
            if "saved" in file.name.casefold()
            or any(part.casefold() == "saved" for part in file.relative_to(path).parts[:-1])
        ]

    def _load_json(self, path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("skipping unreadable export file %s: %s", path, exc)
            self.unreadable_files.append(str(path))
            return None

    def _walk(
        self,
        node: Any,
        *,
        author: str | None,
        collection: str | None,
        timestamp: Any,
    ) -> None:
        if isinstance(node, list):
            for entry in node:
                self._walk(
                    entry,
                    author=author,
                    collection=collection,
                    timestamp=timestamp,
                )
            return
        if not isinstance(node, dict):
            return

        if node.get("timestamp") is not None:
            timestamp = node["timestamp"]

        # Older exports carry the author in "title" next to string_map_data.
        title = node.get("title")
        if "string_map_data" in node and isinstance(title, str) and title.strip():
            author = title.strip()

        # Current exports put the author inside a nested "Dono"/"Owner" block.
        owner = self._owner_username(node)
        if owner:
            author = owner

        label = self._label(node)
        if label:
            collection = label

        direct_href = node.get("href")
        if isinstance(direct_href, str):
            self._emit(
                direct_href,
                author=author,
                collection=collection,
                timestamp=node.get("timestamp", timestamp),
            )

        child_containers: list[Any] = []
        for value in node.values():
            if isinstance(value, dict):
                href = value.get("href")
                if isinstance(href, str):
                    self._emit(
                        href,
                        author=author,
                        collection=collection,
                        timestamp=value.get("timestamp", timestamp),
                    )
                else:
                    child_containers.append(value)
            elif isinstance(value, list):
                child_containers.append(value)

        for child in child_containers:
            self._walk(
                child,
                author=author,
                collection=collection,
                timestamp=timestamp,
            )

    def _owner_username(self, node: dict[str, Any]) -> str | None:
        """Find a username inside a direct child section titled "Dono"/"Owner"."""
        for value in node.values():
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, dict):
                    continue
                title = child.get("title")
                if not isinstance(title, str):
                    continue
                normalized = title.casefold()
                if "dono" not in normalized and "owner" not in normalized:
                    continue
                username = _find_username(child)
                if username:
                    return username
        return None

    def _label(self, node: dict[str, Any]) -> str | None:
        for key, value in node.items():
            if isinstance(value, str) and _is_name_key(key) and value.strip():
                return value.strip()

        string_map_data = node.get("string_map_data")
        if isinstance(string_map_data, dict):
            for key, entry in string_map_data.items():
                if not isinstance(entry, dict) or not _is_name_key(key):
                    continue
                value = entry.get("value")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _emit(
        self,
        url: str,
        *,
        author: str | None,
        collection: str | None,
        timestamp: Any,
    ) -> None:
        try:
            canonical, external_id = self.normalize_url(url)
        except UnsupportedURL:
            self.skipped_urls.append(url)
            return

        existing = self._seen.get(external_id)
        if existing is not None:
            self.duplicate_count += 1
            if existing.collection is None and collection:
                existing.collection = collection
            if existing.author is None and author:
                existing.author = author
            if existing.saved_at is None:
                existing.saved_at = _iso_timestamp(timestamp)
            return

        self._seen[external_id] = SavedItem(
            platform=self.slug,
            external_id=external_id,
            url=canonical,
            source="export",
            author=author,
            collection=collection,
            saved_at=_iso_timestamp(timestamp),
        )


def _is_name_key(key: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", key.casefold()))
    return any(token in words for token in NAME_TOKENS)


def _find_username(node: Any) -> str | None:
    """Return the first username-like value in a small nested structure."""
    if isinstance(node, dict):
        label = node.get("label")
        if isinstance(label, str):
            normalized = label.casefold()
            if "usu" in normalized or "user" in normalized:
                value = node.get("value")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for value in node.values():
            found = _find_username(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_username(value)
            if found:
                return found
    return None


def _iso_timestamp(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    if seconds > 1_000_000_000_000:  # milliseconds
        seconds /= 1000
    return (
        datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
