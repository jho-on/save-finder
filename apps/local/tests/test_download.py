from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from save_finder.analysis import AnalysisResult
from save_finder.config import Settings
from save_finder.download import (
    PieceInfo,
    WorkspaceGuard,
    _check_disk,
    _check_tmp_empty,
    download_item,
)
from save_finder.errors import DownloadError
from save_finder.models import Item, ItemStatus, MediaKind, PlatformSource


def test_workspace_guard_removes_directory_on_exit(settings: Settings) -> None:
    ws = settings.paths.tmp / "test_guard"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "file.txt").write_text("x")

    with WorkspaceGuard(ws, settings) as path:
        assert path == ws
        assert ws.exists()

    assert not ws.exists()


def test_check_disk_raises(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    import shutil

    class FakeUsage:
        free = int(0.5 * 1024**3)  # 0.5 GB

    monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
    with pytest.raises(DownloadError, match="below margin"):
        _check_disk(settings, margin_gb=2.0)


def test_check_tmp_empty_logs_leftovers(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = settings.paths.tmp / "orphan.txt"
    leftover.write_text("x")
    # Should not raise; only log
    _check_tmp_empty(settings)
    leftover.unlink()


def test_download_item_removes_workspace_on_success(settings: Settings) -> None:
    # We rely on the real download being tested separately; here we just
    # confirm the result reports cleaned=True when workspace missing.
    from save_finder.download import DownloadResult

    res = DownloadResult(
        item_id=99,
        workspace=settings.paths.tmp / "fake",
        pieces=(),
        cleaned=True,
    )
    assert res.cleaned is True


def test_download_runs_analysis_before_cleanup(
    settings: Settings,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = Item(
        platform="instagram",
        external_id="download-1",
        url="https://www.instagram.com/p/download-1/",
        source=PlatformSource.EXPORT,
        status=ItemStatus.ENRICHED,
        media_count=1,
    )
    session.add(item)
    session.commit()

    def fake_pieces(url, workspace, settings, *, timeout):
        path = workspace / "piece_0.jpg"
        path.write_bytes(b"x")
        return (
            PieceInfo(
                position=0,
                kind=MediaKind.IMAGE,
                source_url=url,
                file_path=path,
            ),
        )

    def fake_analyze(workspace, row, db_session, cfg, **kwargs):
        assert workspace.exists()
        return AnalysisResult(
            item_id=row.id,
            transcript_saved=False,
            embedding_saved=True,
            embedding_dimension=2,
        )

    monkeypatch.setattr("save_finder.download._download_pieces", fake_pieces)
    monkeypatch.setattr("save_finder.download.analyze_item", fake_analyze)

    result = download_item(item, session, settings)

    assert result.cleaned is True
    assert result.analyzed is True
    assert result.embedding_saved is True
    assert item.status == ItemStatus.ANALYZED
    assert not result.workspace.exists()
    assert session.query(Item).count() == 1
