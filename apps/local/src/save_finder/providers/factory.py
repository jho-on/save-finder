"""Provider factories driven by ``config/settings.toml``.

Instances are cached per process because loading whisper or CLIP is expensive.
Tests inject fakes directly instead of going through these factories.
"""

from __future__ import annotations

import threading
from typing import Any, TypeVar

from save_finder.config import ProviderConfig, Settings
from save_finder.errors import ClassifierError
from save_finder.providers.base import ASRProvider, LLMProvider, VisionProvider

T = TypeVar("T")

_CACHE: dict[tuple[str, str, tuple[tuple[str, str], ...]], object] = {}
_LOCK = threading.Lock()

_DISABLED = {"", "none", "disabled", "off"}


def _key(kind: str, config: ProviderConfig) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    options = tuple(sorted((str(key), str(value)) for key, value in config.options.items()))
    return (kind, config.name, options)


def _cached(kind: str, config: ProviderConfig, builder: Any) -> Any:
    key = _key(kind, config)
    with _LOCK:
        existing = _CACHE.get(key)
        if existing is not None:
            return existing
        instance = builder()
        _CACHE[key] = instance
        return instance


def build_asr(settings: Settings) -> ASRProvider | None:
    config = settings.providers.asr
    if config.name.casefold() in _DISABLED:
        return None

    def _build() -> ASRProvider:
        if config.name in {"faster-whisper", "faster_whisper"}:
            from save_finder.providers.asr import FasterWhisperASR

            return FasterWhisperASR(
                model=str(config.options.get("model", "small")),
                device=str(config.options.get("device", "auto")),
                compute_type=str(config.options.get("compute_type", "int8")),
                beam_size=int(config.options.get("beam_size", 5)),
                vad_filter=bool(config.options.get("vad_filter", True)),
            )
        raise ClassifierError(f"unknown ASR provider: {config.name}")

    return _cached("asr", config, _build)


def build_vision(settings: Settings) -> VisionProvider | None:
    config = settings.providers.vision
    if config.name.casefold() in _DISABLED:
        return None

    def _build() -> VisionProvider:
        if config.name in {"open-clip", "open_clip", "openclip"}:
            from save_finder.providers.vision import DEFAULT_PRETRAINED, OpenClipVision

            return OpenClipVision(
                model=str(config.options.get("model", "ViT-B-32")),
                pretrained=str(config.options.get("pretrained", DEFAULT_PRETRAINED)),
                device=str(config.options.get("device", "auto")),
                batch_size=int(config.options.get("batch_size", 8)),
            )
        raise ClassifierError(f"unknown vision provider: {config.name}")

    return _cached("vision", config, _build)


def build_llm(settings: Settings) -> LLMProvider | None:
    config = settings.providers.llm
    if config.name.casefold() in _DISABLED:
        return None

    def _build() -> LLMProvider:
        if config.name == "ollama":
            from save_finder.providers.llm import OllamaLLM

            host = config.options.get("host")
            return OllamaLLM(
                model=str(config.options.get("model", "qwen3:8b")),
                host=str(host) if host else None,
                temperature=float(config.options.get("temperature", 0.0)),
                num_ctx=int(config.options.get("num_ctx", 8192)),
                num_predict=int(config.options.get("num_predict", 256)),
                timeout=float(config.options.get("timeout", 90.0)),
                json_mode=bool(config.options.get("json_mode", True)),
                think=bool(config.options.get("think", False)),
                keep_alive=str(config.options.get("keep_alive", "10m")),
            )
        raise ClassifierError(f"unknown LLM provider: {config.name}")

    return _cached("llm", config, _build)
