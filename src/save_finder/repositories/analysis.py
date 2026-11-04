"""Persist and load analysis artifacts: transcripts and item embeddings."""

from __future__ import annotations

import struct
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from save_finder.models import Embedding, Item, Transcript
from save_finder.providers.base import TranscriptResult


def save_transcript(session: Session, item_id: int, result: TranscriptResult) -> Transcript:
    """Insert or replace the transcript of one item."""
    row = session.scalar(select(Transcript).where(Transcript.item_id == item_id))
    segments = [
        {"start": segment.start, "end": segment.end, "text": segment.text}
        for segment in result.segments
    ]
    if row is None:
        row = Transcript(
            item_id=item_id,
            language=result.language,
            text=result.text,
            segments=segments,
            model=result.model,
        )
        session.add(row)
    else:
        row.language = result.language
        row.text = result.text
        row.segments = segments
        row.model = result.model
    session.flush()
    return row


def save_embedding(
    session: Session,
    item_id: int,
    vector: Sequence[float],
    *,
    model: str,
) -> Embedding:
    """Insert or replace the aggregated visual embedding of one item."""
    if not vector:
        raise ValueError("embedding vector must not be empty")
    row = session.scalar(select(Embedding).where(Embedding.item_id == item_id))
    packed = pack_vector(vector)
    if row is None:
        row = Embedding(
            item_id=item_id,
            model=model,
            dimension=len(vector),
            vector=packed,
        )
        session.add(row)
    else:
        row.model = model
        row.dimension = len(vector)
        row.vector = packed
    session.flush()
    return row


def load_transcript_text(item: Item) -> str | None:
    transcript = item.transcript
    if transcript is None or not transcript.text.strip():
        return None
    return transcript.text


def load_embedding(item: Item) -> list[float] | None:
    embedding = item.embedding
    if embedding is None:
        return None
    return unpack_vector(embedding.vector, embedding.dimension)


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(payload: bytes, dimension: int) -> list[float]:
    if dimension <= 0:
        return []
    expected = dimension * 4
    if len(payload) != expected:
        raise ValueError(f"embedding size mismatch: {len(payload)} != {expected}")
    return list(struct.unpack(f"<{dimension}f", payload))


def aggregate_vectors(vectors: Sequence[Sequence[float]]) -> list[float]:
    """Average piece embeddings and L2-normalize the result (plan D16)."""
    usable = [vector for vector in vectors if vector]
    if not usable:
        return []
    dimension = len(usable[0])
    if any(len(vector) != dimension for vector in usable):
        raise ValueError("embeddings have different dimensions")
    sums = [0.0] * dimension
    for vector in usable:
        for index, value in enumerate(vector):
            sums[index] += float(value)
    count = len(usable)
    mean = [value / count for value in sums]
    norm = sum(value * value for value in mean) ** 0.5
    if norm == 0:
        return mean
    return [value / norm for value in mean]
