from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from save_finder.cli import app


def test_cli_import_twice(settings, instagram_export: Path) -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["import", "instagram", str(instagram_export)])
    assert first.exit_code == 0, first.output
    assert "New" in first.stdout
    assert "4" in first.stdout

    second = runner.invoke(app, ["import", "instagram", str(instagram_export)])
    assert second.exit_code == 0, second.output
    assert "Existing" in second.stdout


def test_cli_import_zip(settings, instagram_zip: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["import", "instagram", str(instagram_zip)])

    assert result.exit_code == 0, result.output
    assert "New" in result.stdout
    assert "4" in result.stdout
    assert "Ignored" in result.stdout
    assert "2" in result.stdout
    assert list(settings.paths.tmp.iterdir()) == []


def test_cli_rejects_unknown_platform(settings, instagram_export: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["import", "youtube", str(instagram_export)])
    assert result.exit_code == 1
    assert "unknown platform" in result.stdout
