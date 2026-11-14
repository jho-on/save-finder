from __future__ import annotations

from pathlib import Path

import pytest

from save_finder.config import ensure_directories, load_settings, project_root


def test_default_settings_resolve_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "SAVE_FINDER_CONFIG",
        "SAVE_FINDER_DB_URL",
        "SAVE_FINDER_DATA_DIR",
        "SAVE_FINDER_TAXONOMY",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = load_settings()
    root = project_root()

    assert settings.root == root
    assert settings.database.url == f"sqlite:///{root / 'data' / 'save-finder.db'}"
    assert settings.paths.data == root / "data"
    assert settings.paths.thumbs == root / "data" / "thumbs"
    assert settings.paths.tmp == root / "data" / "tmp"
    assert settings.taxonomy_path == root / "config" / "taxonomy.yaml"
    assert settings.providers.asr.name == "faster-whisper"
    assert settings.providers.vision.options["model"] == "ViT-B-32"
    assert settings.pipeline.download_timeout == 180
    assert settings.pipeline.max_retries == 2
    assert settings.pipeline.retry_backoff_seconds == 1.0
    assert settings.pipeline.request_jitter_seconds == 0.5
    assert settings.import_config.max_archive_mb == 200
    assert settings.import_config.max_uncompressed_mb == 256
    assert settings.import_config.max_files == 200
    assert settings.web.port == 8000


def test_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "custom-data"
    monkeypatch.setenv("SAVE_FINDER_DATA_DIR", str(data))
    monkeypatch.setenv("SAVE_FINDER_DB_URL", f"sqlite:///{tmp_path / 'custom.db'}")

    settings = load_settings()
    assert settings.paths.data == data
    assert settings.paths.thumbs == data / "thumbs"
    assert settings.paths.tmp == data / "tmp"
    assert settings.database.url == f"sqlite:///{tmp_path / 'custom.db'}"


def test_ensure_directories(settings) -> None:
    ensure_directories(settings)
    assert settings.paths.data.is_dir()
    assert settings.paths.thumbs.is_dir()
    assert settings.paths.tmp.is_dir()
    assert Path(settings.database.url.removeprefix("sqlite:///")).parent.is_dir()


def test_missing_settings_file(tmp_path: Path) -> None:
    from save_finder.errors import ConfigError

    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.toml")
