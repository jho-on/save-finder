"""Typed access to ``config/settings.toml``.

Settings are loaded from an explicit path (or the default repository path)
and never mutated at runtime. Environment variables only override the few
values needed by tests and by alternate deployments.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from save_finder.errors import ConfigError

DEFAULT_CONFIG_PATH = "config/settings.toml"


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    url: str


@dataclass(frozen=True, slots=True)
class PathsConfig:
    data: Path
    thumbs: Path
    tmp: Path


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    download_timeout: int = 180
    analyze_timeout: int = 300
    max_concurrency: int = 3
    min_free_disk_gb: float = 2.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    request_jitter_seconds: float = 0.5


@dataclass(frozen=True, slots=True)
class ImportConfig:
    """Limits for official export archives."""

    max_archive_mb: int = 200
    max_uncompressed_mb: int = 256
    max_files: int = 200

    @property
    def max_archive_bytes(self) -> int:
        return self.max_archive_mb * 1024 * 1024

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.max_uncompressed_mb * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProvidersConfig:
    asr: ProviderConfig
    vision: ProviderConfig
    llm: ProviderConfig


@dataclass(frozen=True, slots=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    database: DatabaseConfig
    paths: PathsConfig
    taxonomy_path: Path
    pipeline: PipelineConfig
    import_config: ImportConfig
    providers: ProvidersConfig
    web: WebConfig
    app_name: str = "SaveFinder"


def project_root() -> Path:
    """Return the repository root, honouring ``SAVE_FINDER_ROOT`` if set."""
    override = os.environ.get("SAVE_FINDER_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    # src/save_finder/config.py -> src/save_finder -> src -> repository root
    return Path(__file__).resolve().parents[2]


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _resolve_database_url(root: Path, url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        raw = url[len(prefix) :]
        if raw == ":memory:":
            return url
        return f"sqlite:///{_resolve_path(root, raw)}"
    return url


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"settings section [{name}] must be a table")
    return value


def _provider(raw: dict[str, Any], name: str) -> ProviderConfig:
    section = _section(raw, name)
    provider_name = section.get("name", name)
    options = {key: value for key, value in section.items() if key != "name"}
    return ProviderConfig(name=str(provider_name), options=options)


def load_settings(config_path: Path | str | None = None) -> Settings:
    """Load and validate settings.

    Resolution order for the file: explicit argument, ``SAVE_FINDER_CONFIG``,
    ``config/settings.toml`` below the project root.
    """
    root = project_root()
    if config_path is None:
        config_path = os.environ.get("SAVE_FINDER_CONFIG", DEFAULT_CONFIG_PATH)
    path = _resolve_path(root, config_path)

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"settings file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid settings file {path}: {exc}") from exc

    database = _section(raw, "database")
    paths = _section(raw, "paths")
    taxonomy = _section(raw, "taxonomy")
    pipeline = _section(raw, "pipeline")
    import_section = _section(raw, "import")
    web = _section(raw, "web")
    providers_raw = _section(raw, "providers")

    data_override = os.environ.get("SAVE_FINDER_DATA_DIR")
    if data_override:
        data_dir = data_override
        thumbs = Path(data_dir) / "thumbs"
        tmp = Path(data_dir) / "tmp"
    else:
        data_dir = str(paths.get("data", "data"))
        thumbs = paths.get("thumbs", Path(data_dir) / "thumbs")
        tmp = paths.get("tmp", Path(data_dir) / "tmp")

    database_url = os.environ.get(
        "SAVE_FINDER_DB_URL",
        str(database.get("url", f"sqlite:///{Path(data_dir) / 'save-finder.db'}")),
    )
    taxonomy_path = os.environ.get(
        "SAVE_FINDER_TAXONOMY",
        str(taxonomy.get("path", "config/taxonomy.yaml")),
    )

    try:
        pipeline_config = PipelineConfig(
            download_timeout=int(pipeline.get("download_timeout", 180)),
            analyze_timeout=int(pipeline.get("analyze_timeout", 300)),
            max_concurrency=int(pipeline.get("max_concurrency", 3)),
            min_free_disk_gb=float(pipeline.get("min_free_disk_gb", 2.0)),
            max_retries=int(pipeline.get("max_retries", 2)),
            retry_backoff_seconds=float(pipeline.get("retry_backoff_seconds", 1.0)),
            request_jitter_seconds=float(pipeline.get("request_jitter_seconds", 0.5)),
        )
        web_config = WebConfig(
            host=str(web.get("host", "127.0.0.1")),
            port=int(web.get("port", 8000)),
        )
        import_config = ImportConfig(
            max_archive_mb=int(import_section.get("max_archive_mb", 200)),
            max_uncompressed_mb=int(import_section.get("max_uncompressed_mb", 256)),
            max_files=int(import_section.get("max_files", 200)),
        )
        providers = ProvidersConfig(
            asr=_provider(providers_raw, "asr"),
            vision=_provider(providers_raw, "vision"),
            llm=_provider(providers_raw, "llm"),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid settings value in {path}: {exc}") from exc

    return Settings(
        root=root,
        app_name=str(_section(raw, "app").get("name", "SaveFinder")),
        database=DatabaseConfig(url=_resolve_database_url(root, database_url)),
        paths=PathsConfig(
            data=_resolve_path(root, data_dir),
            thumbs=_resolve_path(root, thumbs),
            tmp=_resolve_path(root, tmp),
        ),
        taxonomy_path=_resolve_path(root, taxonomy_path),
        pipeline=pipeline_config,
        import_config=import_config,
        providers=providers,
        web=web_config,
    )


def ensure_directories(settings: Settings) -> None:
    """Create the local data directories and the SQLite parent directory."""
    for directory in (settings.paths.data, settings.paths.thumbs, settings.paths.tmp):
        directory.mkdir(parents=True, exist_ok=True)

    url = settings.database.url
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        raise ConfigError(f"SQLite URL must be absolute after loading settings: {url}")
    if url.startswith("sqlite:////"):
        database_file = Path(url[len(prefix) :])
        database_file.parent.mkdir(parents=True, exist_ok=True)
