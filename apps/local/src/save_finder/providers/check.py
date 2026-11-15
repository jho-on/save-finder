"""Availability checks for the local AI providers.

These checks are intentionally cheap: they never download model weights or
run inference. They answer "is this provider usable right now?" so the CLI
(``save-finder doctor``) and the web panel can tell the user what to install.
"""

from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass
from typing import Any

from save_finder.config import ProviderConfig, Settings

_DISABLED = {"", "none", "disabled", "off"}

_INSTALL_EXTRA = {
    "asr": "uv sync --extra asr",
    "vision": "uv sync --extra vision",
    "llm": "uv sync --extra llm",
}


@dataclass
class ProviderStatus:
    """Result of one provider availability check."""

    kind: str
    label: str
    configured: str
    ok: bool
    status: str
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _disabled(config: ProviderConfig) -> bool:
    return config.name.casefold() in _DISABLED


def _check_asr(config: ProviderConfig) -> ProviderStatus:
    label = "Transcrição (ASR)"
    if _disabled(config):
        return ProviderStatus(
            "asr", label, config.name, True, "disabled", "Provider desabilitado na configuração"
        )
    if not _module_available("faster_whisper"):
        return ProviderStatus(
            "asr",
            label,
            config.name,
            False,
            "missing_dependency",
            "faster-whisper não está instalado",
            _INSTALL_EXTRA["asr"],
        )
    model = str(config.options.get("model", "small"))
    return ProviderStatus(
        "asr",
        label,
        config.name,
        True,
        "ok",
        f"faster-whisper instalado (modelo '{model}' é baixado no primeiro uso)",
    )


def _check_vision(config: ProviderConfig) -> ProviderStatus:
    label = "Visão (embeddings)"
    if _disabled(config):
        return ProviderStatus(
            "vision", label, config.name, True, "disabled", "Provider desabilitado na configuração"
        )
    missing = [pkg for pkg in ("open_clip", "torch") if not _module_available(pkg)]
    if missing:
        return ProviderStatus(
            "vision",
            label,
            config.name,
            False,
            "missing_dependency",
            f"Pacotes ausentes: {', '.join(missing)}",
            _INSTALL_EXTRA["vision"],
        )
    model = str(config.options.get("model", "ViT-B-32"))
    return ProviderStatus(
        "vision",
        label,
        config.name,
        True,
        "ok",
        f"open-clip instalado (modelo '{model}' é baixado no primeiro uso)",
    )


def _check_llm(config: ProviderConfig) -> ProviderStatus:
    label = "LLM (classificação)"
    if _disabled(config):
        return ProviderStatus(
            "llm", label, config.name, True, "disabled", "Provider desabilitado na configuração"
        )
    if not _module_available("ollama"):
        return ProviderStatus(
            "llm",
            label,
            config.name,
            False,
            "missing_dependency",
            "Cliente ollama não está instalado",
            _INSTALL_EXTRA["llm"],
        )
    model = str(config.options.get("model", "qwen3:8b"))
    host = config.options.get("host")
    try:
        from ollama import Client

        client = Client(host=str(host) if host else None, timeout=3.0)
        response = client.list()
        entries = getattr(response, "models", None)
        if entries is None and isinstance(response, dict):
            entries = response.get("models", [])
        available = set()
        for entry in entries or []:
            name = getattr(entry, "model", None)
            if name is None and isinstance(entry, dict):
                name = entry.get("model") or entry.get("name")
            if name:
                available.add(str(name))
    except Exception:
        return ProviderStatus(
            "llm",
            label,
            config.name,
            False,
            "unavailable",
            "Servidor Ollama fora do ar ou inalcançável",
            "Inicie o Ollama (ex.: 'ollama serve') e tente novamente",
        )
    if model not in available and f"{model}:latest" not in available:
        return ProviderStatus(
            "llm",
            label,
            config.name,
            False,
            "model_missing",
            f"Modelo '{model}' não está baixado no Ollama",
            f"ollama pull {model}",
        )
    return ProviderStatus(
        "llm", label, config.name, True, "ok", f"Ollama no ar com o modelo '{model}'"
    )


def check_providers(settings: Settings) -> list[ProviderStatus]:
    """Check every configured provider and return one status per kind."""
    return [
        _check_asr(settings.providers.asr),
        _check_vision(settings.providers.vision),
        _check_llm(settings.providers.llm),
    ]
