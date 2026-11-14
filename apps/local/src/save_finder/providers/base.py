"""Provider contracts.

Implementations (local or remote) live behind these protocols so the pipeline
never imports a concrete ML library directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    language: str | None
    text: str
    segments: tuple[TranscriptSegment, ...]
    model: str


class ASRProvider(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        """Transcribe audio, detecting the language automatically."""
        ...


class VisionProvider(Protocol):
    name: str

    def embed_images(self, image_paths: Sequence[Path]) -> list[list[float]]:
        """Return one embedding per image."""
        ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed tag descriptions in the same space as the images."""
        ...


class LLMProvider(Protocol):
    name: str

    def complete(self, *, system: str, prompt: str) -> str:
        """Return the model's raw text response."""
        ...
