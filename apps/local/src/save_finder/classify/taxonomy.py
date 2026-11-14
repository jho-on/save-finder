"""Closed taxonomy: loading, validation and parent propagation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from save_finder.errors import TaxonomyError

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class TagDefinition:
    slug: str
    name: str
    level: str
    parent: str | None = None
    visual_description: str | None = None

    @property
    def is_broad(self) -> bool:
        return self.level == "broad"


class Taxonomy:
    """Immutable, validated tag tree."""

    def __init__(self, version: int, tags: Iterable[TagDefinition]) -> None:
        definitions = {tag.slug: tag for tag in tags}
        self.version = version
        self._tags = MappingProxyType(definitions)
        self._children: dict[str, tuple[str, ...]] = {
            slug: tuple(child.slug for child in definitions.values() if child.parent == slug)
            for slug in definitions
        }

    def __contains__(self, slug: object) -> bool:
        return slug in self._tags

    def __iter__(self) -> Iterator[TagDefinition]:
        return iter(self._tags.values())

    def __len__(self) -> int:
        return len(self._tags)

    def get(self, slug: str) -> TagDefinition:
        try:
            return self._tags[slug]
        except KeyError as exc:
            raise TaxonomyError(f"unknown tag: {slug}") from exc

    def require(self, slugs: Iterable[str]) -> list[TagDefinition]:
        """Return definitions, rejecting any slug outside the taxonomy."""
        return [self.get(slug) for slug in slugs]

    @property
    def tags(self) -> MappingProxyType[str, TagDefinition]:
        return self._tags

    @property
    def broad(self) -> tuple[TagDefinition, ...]:
        return tuple(tag for tag in self._tags.values() if tag.is_broad)

    @property
    def specific(self) -> tuple[TagDefinition, ...]:
        return tuple(tag for tag in self._tags.values() if not tag.is_broad)

    def children(self, slug: str) -> tuple[TagDefinition, ...]:
        self.get(slug)
        return tuple(self._tags[child] for child in self._children[slug])

    def propagate(self, slugs: Iterable[str]) -> set[str]:
        """Expand selected tags with their broad parent, as required by the plan."""
        selected: set[str] = set()
        for slug in slugs:
            tag = self.get(slug)
            selected.add(tag.slug)
            if tag.parent is not None:
                selected.add(tag.parent)
        return selected

    @classmethod
    def from_mapping(cls, raw: Any) -> Taxonomy:
        return _build(raw)

    @classmethod
    def load(cls, path: Path | str) -> Taxonomy:
        path = Path(path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TaxonomyError(f"taxonomy file not found: {path}") from exc
        except yaml.YAMLError as exc:
            raise TaxonomyError(f"invalid taxonomy YAML {path}: {exc}") from exc
        return _build(raw)


def _require_text(entry: dict[str, Any], key: str, *, required: bool = True) -> str | None:
    value = entry.get(key)
    if value is None:
        if required:
            raise TaxonomyError(f"missing '{key}' in taxonomy entry: {entry}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise TaxonomyError(f"'{key}' must be a non-empty string in taxonomy entry: {entry}")
    return value.strip()


def _build(raw: dict[str, Any]) -> Taxonomy:
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise TaxonomyError("taxonomy 'version' must be a positive integer")
    entries = raw.get("tags")
    if not isinstance(entries, list) or not entries:
        raise TaxonomyError("taxonomy 'tags' must be a non-empty list")

    parsed: list[tuple[str, str, str | None, str | None]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TaxonomyError(f"taxonomy entry must be a mapping: {entry}")
        slug = _require_text(entry, "slug")
        assert slug is not None
        if not SLUG_PATTERN.match(slug):
            raise TaxonomyError(f"invalid slug: {slug!r}")
        if slug in seen:
            raise TaxonomyError(f"duplicate tag slug: {slug}")
        seen.add(slug)
        name = _require_text(entry, "name")
        parent = _require_text(entry, "parent", required=False)
        description = _require_text(entry, "visual_description", required=False)
        parsed.append((slug, name or slug, parent, description))

    broad = {slug for slug, _, parent, _ in parsed if parent is None}
    definitions: list[TagDefinition] = []
    for slug, name, parent, description in parsed:
        if parent is None:
            if not description:
                raise TaxonomyError(f"broad tag '{slug}' needs a visual_description")
            definitions.append(
                TagDefinition(slug=slug, name=name, level="broad", visual_description=description)
            )
            continue
        if parent not in broad:
            raise TaxonomyError(f"tag '{slug}' has unknown or non-broad parent: {parent!r}")
        definitions.append(
            TagDefinition(
                slug=slug,
                name=name,
                level="specific",
                parent=parent,
                visual_description=description,
            )
        )

    return Taxonomy(version=version, tags=definitions)
