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

    # Business rule: every item gets exactly one broad tag, plus the specific
    # tags that belong to it. Specific tags never appear without their parent.
    valid = [slug for slug in candidates if slug in taxonomy]
    selected_broad: str | None = None
    for slug in valid:
        tag = taxonomy.get(slug)
        if tag.is_broad:
            selected_broad = tag.slug
            break
        if tag.parent is not None:
            selected_broad = tag.parent
            break

    if selected_broad is None:
        emitted = ("outros",)
    else:
        emitted = tuple(
            sorted(
                {selected_broad}
                | {
                    taxonomy.get(slug).slug
                    for slug in valid
                    if not taxonomy.get(slug).is_broad
                    and taxonomy.get(slug).parent == selected_broad
                }
            )
        )
    confidences = tuple(0.6 for _ in emitted)  # placeholder until calibration sample exists
    return ClassificationResult(
        item_id=item.id,
        tags=emitted,
        confidences=confidences,
        evidence="heuristic + taxonomy match",
    )
