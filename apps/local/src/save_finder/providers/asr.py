"""Local speech-to-text provider backed by faster-whisper.

The ML dependency is optional and imported lazily, so the rest of the project
can be imported and tested without the ``asr`` extra installed.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from save_finder.errors import ClassifierError
from save_finder.providers.base import TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)


class FasterWhisperASR:
    """Transcribe audio locally, detecting the language automatically."""

    def __init__(
        self,
        *,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        beam_size: int = 5,
        vad_filter: bool = True,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.name = f"faster-whisper:{model}"
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ClassifierError(
                    "faster-whisper is not installed; install the 'asr' extra"
                ) from exc
            try:
                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except Exception as exc:
                raise ClassifierError(f"could not load faster-whisper model: {exc}") from exc
            return self._model

    def transcribe(self, audio_path: Path) -> TranscriptResult:
        if not audio_path.exists():
            raise ClassifierError(f"audio file not found: {audio_path}")

        model = self._load()
        try:
            raw_segments, info = model.transcribe(
                str(audio_path),
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            segments: list[TranscriptSegment] = []
            for segment in raw_segments:
                text = str(segment.text).strip()
                if not text:
                    continue
                segments.append(
                    TranscriptSegment(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=text,
                    )
                )
        except Exception as exc:
            raise ClassifierError(f"transcription failed: {exc}") from exc

        text = " ".join(segment.text for segment in segments).strip()
        language = getattr(info, "language", None)
        logger.debug(
            "transcribed %s language=%s segments=%d chars=%d",
            audio_path.name,
            language,
            len(segments),
            len(text),
        )
        return TranscriptResult(
            language=language,
            text=text,
            segments=tuple(segments),
            model=self.name,
        )
