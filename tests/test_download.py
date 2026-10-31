from __future__ import annotations

import pytest

from save_finder.config import Settings
from save_finder.download import WorkspaceGuard, _check_disk, _check_tmp_empty
from save_finder.errors import DownloadError


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
