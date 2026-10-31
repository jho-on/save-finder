"""Import orchestration shared by the CLI and the web upload endpoint.

An official export normally arrives as a ZIP. Only the JSON payloads below a
``saved`` folder are extracted, into ``data/tmp``, and the extraction is always
removed when parsing finishes. Media files inside the archive are ignored.
"""

from __future__ import annotations

import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy.orm import Session

from save_finder.config import ImportConfig
from save_finder.errors import ExportImportError
from save_finder.platforms.registry import get_adapter
from save_finder.repositories.items import import_saved_items

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImportResult:
    platform: str
    new: int
    existing: int
    ignored: int
    unreadable: int

    @property
    def total(self) -> int:
        return self.new + self.existing


def import_export(
    session: Session,
    *,
    platform: str,
    path: Path,
    tmp_dir: Path,
    limits: ImportConfig,
) -> ImportResult:
    """Parse an export (directory, JSON file or official ZIP) and persist it."""
    adapter = get_adapter(platform)
    with materialize_export(path, tmp_dir=tmp_dir, limits=limits) as source:
        items = list(adapter.parse_export(source))

    stats = import_saved_items(session, items)
    return ImportResult(
        platform=platform,
        new=stats.new,
        existing=stats.existing,
        ignored=len(getattr(adapter, "skipped_urls", []))
        + int(getattr(adapter, "duplicate_count", 0)),
        unreadable=len(getattr(adapter, "unreadable_files", [])),
    )


@contextmanager
def materialize_export(
    path: Path,
    *,
    tmp_dir: Path,
    limits: ImportConfig,
) -> Iterator[Path]:
    """Yield a path the adapter can parse, extracting ZIPs safely."""
    path = Path(path)
    if not path.exists():
        raise ExportImportError(f"export path does not exist: {path}")
    if not path.is_file():
        yield path
        return

    is_zip = path.suffix.casefold() == ".zip" or zipfile.is_zipfile(path)
    if not is_zip:
        yield path
        return

    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="savefinder-import-", dir=tmp_dir) as raw:
        extraction = Path(raw)
        _extract_saved_json(path, extraction, limits)
        yield extraction


def copy_upload(source: BinaryIO, dest: Path, *, max_bytes: int) -> None:
    """Copy an upload to disk with a hard size limit, removing partial files."""
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as output:
            while chunk := source.read(CHUNK_SIZE):
                total += len(chunk)
                if total > max_bytes:
                    raise ExportImportError(
                        f"archive exceeds the {max_bytes // (1024 * 1024)} MB limit"
                    )
                output.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    if total == 0:
        dest.unlink(missing_ok=True)
        raise ExportImportError("uploaded archive is empty")


def _extract_saved_json(archive_path: Path, dest: Path, limits: ImportConfig) -> None:
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ExportImportError(f"invalid ZIP archive: {archive_path}") from exc

    with archive:
        members = [info for info in archive.infolist() if _is_saved_json(info)]
        if not members:
            raise ExportImportError(
                "no saved JSON found in the archive; use the official export ZIP "
                "or point to the extracted saved folder"
            )
        if len(members) > limits.max_files:
            raise ExportImportError(
                f"archive contains {len(members)} saved JSON files; limit is {limits.max_files}"
            )

        total = 0
        for info in members:
            _validate_member(info, limits)
            parts = _safe_parts(info.filename)
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest.resolve()):
                raise ExportImportError(f"unsafe path in archive: {info.filename}")

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, target.open("wb") as output:
                    while chunk := source.read(CHUNK_SIZE):
                        total += len(chunk)
                        if total > limits.max_uncompressed_bytes:
                            raise ExportImportError(
                                "archive exceeds the "
                                f"{limits.max_uncompressed_mb} MB uncompressed limit"
                            )
                        output.write(chunk)
            except RuntimeError as exc:  # encrypted member
                raise ExportImportError(f"cannot read archive member: {info.filename}") from exc


def _is_saved_json(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    parts = PurePosixPath(info.filename.replace("\\", "/")).parts
    if not parts or "__MACOSX" in parts:
        return False
    if not parts[-1].casefold().endswith(".json"):
        return False
    filename = parts[-1].casefold()
    if any(part.casefold() == "saved" for part in parts[:-1]):
        return True
    return filename in {"saved_posts.json", "saved_collections.json"}


def _validate_member(info: zipfile.ZipInfo, limits: ImportConfig) -> None:
    if info.flag_bits & 0x1:
        raise ExportImportError(f"encrypted archive member: {info.filename}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ExportImportError(f"symlink in archive: {info.filename}")
    if info.file_size > limits.max_uncompressed_bytes:
        raise ExportImportError(f"archive member is too large: {info.filename}")


def _safe_parts(filename: str) -> tuple[str, ...]:
    pure = PurePosixPath(filename.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts:
        raise ExportImportError(f"unsafe path in archive: {filename}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ExportImportError(f"unsafe path in archive: {filename}")
    return pure.parts
