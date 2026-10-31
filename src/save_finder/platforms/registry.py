"""Platform adapter registry.

Importing this module registers the built-in adapters. Adding a network means
implementing the adapter and registering it here — nothing else in the core.
Each ``get_adapter`` call returns a fresh instance so parse counters are not
shared between concurrent imports.
"""

from __future__ import annotations

from collections.abc import Callable

from save_finder.errors import UnknownPlatform
from save_finder.platforms.base import PlatformAdapter
from save_finder.platforms.instagram import InstagramAdapter

_ADAPTER_FACTORIES: dict[str, Callable[[], PlatformAdapter]] = {}


def register_adapter(factory: Callable[[], PlatformAdapter]) -> None:
    _ADAPTER_FACTORIES[factory.slug] = factory  # type: ignore[attr-defined]


def get_adapter(slug: str) -> PlatformAdapter:
    try:
        return _ADAPTER_FACTORIES[slug]()
    except KeyError as exc:
        available = ", ".join(available_platforms()) or "none"
        raise UnknownPlatform(f"unknown platform '{slug}' (available: {available})") from exc


def available_platforms() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTER_FACTORIES))


register_adapter(InstagramAdapter)
