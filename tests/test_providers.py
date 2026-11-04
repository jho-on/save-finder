"""Provider factories stay lazy and validate configuration names."""

from __future__ import annotations

from dataclasses import replace

import pytest

from save_finder.config import ProviderConfig, ProvidersConfig, Settings
from save_finder.errors import ClassifierError
from save_finder.providers.factory import build_asr, build_llm, build_vision


def test_default_providers_come_from_settings(settings: Settings) -> None:
    asr = build_asr(settings)
    vision = build_vision(settings)
    llm = build_llm(settings)

    assert asr is not None and asr.name == "faster-whisper:small"
    assert vision is not None and vision.name == "open-clip:ViT-B-32"
    assert llm is not None and llm.name == "ollama:qwen3:8b"


def test_disabled_provider_returns_none(settings: Settings) -> None:
    providers = replace(
        settings.providers,
        llm=ProviderConfig(name="disabled"),
    )
    disabled = replace(settings, providers=providers)

    assert build_llm(disabled) is None


def test_unknown_provider_is_rejected(settings: Settings) -> None:
    providers = replace(
        settings.providers,
        asr=ProviderConfig(name="not-a-provider"),
    )
    unknown = replace(settings, providers=providers)

    with pytest.raises(ClassifierError, match="unknown ASR provider"):
        build_asr(unknown)


def test_provider_config_is_complete(settings: Settings) -> None:
    assert isinstance(settings.providers, ProvidersConfig)
    assert settings.providers.vision.options["pretrained"] == "laion2b_s34b_b79k"
    assert settings.providers.llm.options["model"] == "qwen3:8b"
    assert settings.providers.llm.options["think"] is False
    assert settings.providers.llm.options["num_predict"] == 256
