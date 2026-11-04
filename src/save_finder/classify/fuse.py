"""Fusing text and visual signals into taxonomy tags.

Text comes from the caption plus the transcript and is classified by a local
LLM. Visual signals come from a persisted CLIP embedding compared against the
English descriptions of broad tags. The closed taxonomy and the one-broad rule
are enforced here, never in the provider.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from save_finder.classify.taxonomy import Taxonomy
from save_finder.models import Item
from save_finder.providers.base import LLMProvider, VisionProvider
from save_finder.repositories.analysis import load_embedding, load_transcript_text

logger = logging.getLogger(__name__)

VISUAL_MIN_SIMILARITY = 0.22
TEXT_MIN_CONFIDENCE = 0.45
MAX_SPECIFIC_TAGS = 6
MAX_TEXT_CHARS = 6000
FALLBACK_CONFIDENCE = 0.6


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    item_id: int
    tags: tuple[str, ...]  # tag slugs emitted
    confidences: tuple[float, ...]
    evidence: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _Selection:
    broad: str | None
    specific: tuple[str, ...]
    confidence: float
    evidence: str
    error: str | None = None


def classify_item(
    item: Item,
    taxonomy: Taxonomy,
    *,
    caption_text: str | None = None,
    thumb_path: Path | None = None,
    transcript_text: str | None = None,
    embedding: Sequence[float] | None = None,
    vision: VisionProvider | None = None,
    llm: LLMProvider | None = None,
) -> ClassificationResult:
    """Emit taxonomy tags for one item using the available signals.

    ``embedding`` is the persisted item vector; when both providers are omitted
    the conservative keyword heuristic keeps the command usable. The closed
    taxonomy (no tag outside ``taxonomy.yaml``) and parent propagation are
    enforced regardless of provider output.
    """
    caption = (caption_text if caption_text is not None else item.caption) or ""
    transcript = transcript_text or ""
    text = _join_text(caption, transcript)

    text_selection: _Selection | None = None
    visual_selection: _Selection | None = None
    heuristic_selection: _Selection | None = None
    errors: list[str] = []

    if text and llm is not None:
        text_selection = _classify_text(text, taxonomy, llm)
        if text_selection.error:
            errors.append(text_selection.error)
    if embedding and vision is not None:
        visual_selection = _classify_visual(embedding, taxonomy, vision)
        if visual_selection.error:
            errors.append(visual_selection.error)
    # Always available as a deterministic fallback when a provider is missing,
    # fails, or returns an invalid tag.
    heuristic_selection = _heuristic_selection(text, taxonomy)

    selection = _fuse(
        text_selection,
        visual_selection,
        heuristic_selection,
        has_transcript=bool(transcript),
    )
    if selection is None:
        error = "; ".join(errors) if errors else None
        return ClassificationResult(
            item_id=item.id,
            tags=("outros",),
            confidences=(0.0,),
            evidence="no usable signal",
            error=error,
        )

    slugs = _normalize_selection(taxonomy, selection.broad, selection.specific)
    if not slugs:
        slugs = ("outros",)
    confidence = _clamp(selection.confidence)
    confidences = tuple(
        confidence if slug == slugs[0] else max(0.1, round(confidence * 0.9, 4)) for slug in slugs
    )
    return ClassificationResult(
        item_id=item.id,
        tags=slugs,
        confidences=confidences,
        evidence=selection.evidence,
        error="; ".join(errors) if errors else selection.error,
    )


def classify_persisted_item(
    item: Item,
    taxonomy: Taxonomy,
    *,
    vision: VisionProvider | None = None,
    llm: LLMProvider | None = None,
) -> ClassificationResult:
    """Reclassify using only caption, transcript and embedding from the database.

    This path never touches the network or the original media, which is what
    makes offline reclassification reproducible after the Fase 4 acceptance.
    """
    try:
        embedding = load_embedding(item)
    except ValueError as exc:
        logger.warning("invalid stored embedding item=%s: %s", item.external_id, exc)
        embedding = None
    return classify_item(
        item,
        taxonomy,
        caption_text=item.caption,
        transcript_text=load_transcript_text(item),
        embedding=embedding,
        vision=vision,
        llm=llm,
    )


def _fuse(
    text: _Selection | None,
    visual: _Selection | None,
    heuristic: _Selection | None,
    *,
    has_transcript: bool,
) -> _Selection | None:
    """Choose a signal with text precedence when there is spoken content."""
    text_valid = (
        text is not None and text.broad is not None and text.confidence >= TEXT_MIN_CONFIDENCE
    )
    visual_valid = (
        visual is not None
        and visual.broad is not None
        and visual.confidence >= VISUAL_MIN_SIMILARITY
    )

    if text_valid and text is not None:
        if text.broad != "outros":
            if visual_valid and visual is not None and visual.broad == text.broad:
                return _Selection(
                    broad=text.broad,
                    specific=text.specific,
                    confidence=max(text.confidence, visual.confidence),
                    evidence=f"text+visual; {text.evidence}; {visual.evidence}",
                )
            return text
        # A caption-only post that the LLM cannot place may still be helped by
        # the visual signal. With a transcript, the LLM gets the final word.
        if not has_transcript and visual_valid:
            return visual
        return text

    if visual_valid and visual is not None:
        specific = ()
        if text is not None and text.broad == visual.broad:
            specific = text.specific
        return _Selection(
            broad=visual.broad,
            specific=specific,
            confidence=visual.confidence,
            evidence=visual.evidence,
        )

    if text is not None and text.broad is not None:
        return text
    if heuristic is not None and heuristic.broad is not None:
        return heuristic
    return None


def _classify_text(text: str, taxonomy: Taxonomy, llm: LLMProvider) -> _Selection:
    system = (
        "You classify saved social-media posts into a closed taxonomy. "
        "Select exactly one broad tag and only specific tags that belong to it. "
        "Never invent tags. Base the decision on meaning, not on keyword matching. "
        "The evidence must justify the selected broad tag; if the content does not fit "
        'any broad tag, choose "outros". Never select a tag that the evidence says '
        "does not apply. "
        'Answer with a single JSON object: {"broad": "slug", "specific": ["slug"], '
        '"confidence": 0.0, "evidence": "short reason"}. '
        "Content may be in any language; keep it as is.\n"
        "Examples:\n"
        '- Post about cooking a cake: {"broad": "comida-e-bebida", '
        '"specific": ["receitas"], "confidence": 0.9, '
        '"evidence": "The post teaches a cake recipe."}\n'
        '- Post about hand-sewing tools and techniques: {"broad": "arte-e-design", '
        '"specific": ["artesanato"], "confidence": 0.85, '
        '"evidence": "The video explains sewing tools and hand-sewing techniques, '
        'a craft."}\n'
        '- Post about a new phone: {"broad": "tecnologia", '
        '"specific": ["gadgets"], "confidence": 0.9, '
        '"evidence": "The post reviews a smartphone."}\n'
        '- Post about an office meeting: {"broad": "trabalho-e-carreira", '
        '"specific": [], "confidence": 0.8, '
        '"evidence": "The post shows a workplace meeting."}'
    )
    prompt = _build_prompt(text, taxonomy)
    try:
        raw = llm.complete(system=system, prompt=prompt)
    except Exception as exc:
        logger.warning("LLM classification failed: %s", exc)
        return _Selection(None, (), 0.0, "", f"llm: {exc}")

    parsed = _parse_llm_response(raw)
    if parsed is None:
        logger.warning("LLM returned an unparsable response")
        return _Selection(None, (), 0.0, "", "llm: invalid JSON response")

    broad, specific, confidence, evidence = parsed
    normalized = _normalize_selection(taxonomy, broad, specific)
    if normalized:
        normalized_broad: str | None = normalized[0]
        normalized_specific = normalized[1:]
    else:
        normalized_broad = None
        normalized_specific = ()
    return _Selection(
        broad=normalized_broad,
        specific=normalized_specific,
        confidence=confidence,
        evidence=f"llm: {evidence or 'classified from caption/transcript'}",
    )


def _classify_visual(
    embedding: Sequence[float],
    taxonomy: Taxonomy,
    vision: VisionProvider,
) -> _Selection:
    broad_tags = taxonomy.broad
    descriptions = [tag.visual_description or tag.name for tag in broad_tags]
    try:
        text_vectors = vision.embed_texts(descriptions)
    except Exception as exc:
        logger.warning("visual taxonomy embedding failed: %s", exc)
        return _Selection(None, (), 0.0, "", f"vision: {exc}")

    if len(text_vectors) != len(broad_tags):
        return _Selection(None, (), 0.0, "", "vision: unexpected embedding count")

    scored = [
        (tag.slug, _cosine(embedding, vector))
        for tag, vector in zip(broad_tags, text_vectors, strict=True)
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    best_slug, best_score = scored[0]
    return _Selection(
        broad=best_slug,
        specific=(),
        confidence=best_score,
        evidence=f"vision: {best_slug} cosine={best_score:.3f}",
    )


def _build_prompt(text: str, taxonomy: Taxonomy) -> str:
    broad_lines = [
        f"- {tag.slug}: {tag.name} — {tag.visual_description or ''}".rstrip()
        for tag in taxonomy.broad
    ]
    specific_lines = [
        f"- {tag.slug} (parent: {tag.parent}): {tag.name}" for tag in taxonomy.specific
    ]
    content = text.strip()[:MAX_TEXT_CHARS]
    return (
        "BROAD TAGS (pick exactly one):\n"
        + "\n".join(broad_lines)
        + "\n\nSPECIFIC TAGS (optional, only under the selected broad):\n"
        + "\n".join(specific_lines)
        + "\n\nPOST CONTENT:\n"
        + (content or "(empty)")
        + "\n\nReturn only the JSON object."
    )


def _parse_llm_response(raw: str) -> tuple[str | None, list[str], float, str] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    broad_value = payload.get("broad") or payload.get("parent") or payload.get("category")
    broad = broad_value.strip() if isinstance(broad_value, str) else None

    specific_value = payload.get("specific", payload.get("specifics", []))
    if isinstance(specific_value, str):
        specific = [specific_value]
    elif isinstance(specific_value, list):
        specific = [value.strip() for value in specific_value if isinstance(value, str)]
    else:
        specific = []

    confidence_value = payload.get("confidence", FALLBACK_CONFIDENCE)
    try:
        confidence = _clamp(float(confidence_value))
    except (TypeError, ValueError):
        confidence = FALLBACK_CONFIDENCE

    evidence_value = payload.get("evidence", payload.get("reason", ""))
    evidence = evidence_value.strip() if isinstance(evidence_value, str) else ""
    return broad, specific, confidence, evidence


def _normalize_selection(
    taxonomy: Taxonomy,
    broad: str | None,
    specific: Sequence[str],
) -> tuple[str, ...]:
    """Return one broad slug followed by valid specifics under it."""
    selected = broad.strip() if isinstance(broad, str) else None
    if selected is not None and selected not in taxonomy:
        selected = None
    if selected is not None and not taxonomy.get(selected).is_broad:
        selected = taxonomy.get(selected).parent

    valid_specific: list[str] = []
    for slug in specific:
        candidate = slug.strip()
        if candidate not in taxonomy:
            continue
        definition = taxonomy.get(candidate)
        if definition.is_broad:
            continue
        parent = definition.parent
        if selected is None and parent is not None:
            selected = parent
        if parent == selected and candidate not in valid_specific:
            valid_specific.append(candidate)

    if selected is None or selected not in taxonomy or not taxonomy.get(selected).is_broad:
        return ()
    if selected == "outros":
        return ("outros",)

    valid_specific.sort()
    return (selected, *valid_specific[:MAX_SPECIFIC_TAGS])


def _heuristic_selection(text: str, taxonomy: Taxonomy) -> _Selection:
    """Conservative keyword fallback used when no AI provider is configured."""
    haystack = text.casefold()
    candidates: list[str] = []
    for tag in (*taxonomy.broad, *taxonomy.specific):
        keywords = [
            tag.slug.replace("-", " "),
            tag.name.casefold(),
            *(tag.visual_description or "").casefold().split(),
        ]
        if any(keyword in haystack for keyword in keywords if keyword):
            candidates.append(tag.slug)

    broad: str | None = None
    for slug in candidates:
        definition = taxonomy.get(slug)
        if definition.is_broad:
            broad = definition.slug
            break
        if definition.parent is not None:
            broad = definition.parent
            break

    if broad is None:
        return _Selection("outros", (), FALLBACK_CONFIDENCE, "heuristic: no match")

    specific = tuple(
        sorted(
            slug
            for slug in candidates
            if slug in taxonomy
            and not taxonomy.get(slug).is_broad
            and taxonomy.get(slug).parent == broad
        )
    )[:MAX_SPECIFIC_TAGS]
    return _Selection(broad, specific, FALLBACK_CONFIDENCE, "heuristic + taxonomy match")


def _join_text(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(float(value), 4)))
