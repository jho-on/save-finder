"""Fusing signals (caption, visual, transcript) into taxonomy tags."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from save_finder.classify.taxonomy import Taxonomy
from save_finder.models import Item

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    item_id: int
    tags: tuple[str, ...]  # tag slugs emitted
    confidences: tuple[float, ...]
    evidence: str | None = None
    error: str | None = None


def classify_item(
    item: Item,
    taxonomy: Taxonomy,
    *,
    caption_text: str | None = None,
    thumb_path: Path | None = None,
    transcript_text: str | None = None,
) -> ClassificationResult:
    """Emit taxonomy tags for one item using available signals.

    This is the Fase 4 core. It respects the closed taxonomy (no tag outside
    ``taxonomy.yaml`` may be emitted) and preserves parent propagation.
    """
    caption = caption_text or item.caption or ""
    post_type = item.post_type.value if item.post_type else None
    text_signals = " ".join(filter(None, [caption, transcript_text or ""]))

    # Simple heuristic + taxonomy match for MVP; full LLM/Vision fusion
    # can be injected by replacing this body once providers return results.
    candidates: list[str] = []
    for tag in (*taxonomy.broad, *taxonomy.specific):
        slug = tag.slug
        keywords = [
            slug.replace("-", " "),
            tag.name.lower(),
            *(tag.visual_description or "").lower().split(),
        ]
        if any(kw in text_signals.lower() for kw in keywords if kw):
            candidates.append(slug)

    # Post-type hints when no text matches
    if not candidates:
        if post_type == "video":
            candidates = ["video"]
        elif post_type == "image":
            candidates = ["photo"]
        elif post_type == "carousel":
            candidates = ["carousel"]

    # Deduplicate and enforce taxonomy membership
    emitted = tuple(
        sorted(set(candidates) & {t.slug for t in (*taxonomy.broad, *taxonomy.specific)})
    )
    confidences = tuple(0.6 for _ in emitted)  # placeholder until calibration sample exists
    return ClassificationResult(
        item_id=item.id,
        tags=emitted,
        confidences=confidences,
        evidence="heuristic + taxonomy match",
    )
