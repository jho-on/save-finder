"""SaveFinder command line interface."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session

from save_finder import __version__
from save_finder.classify.taxonomy import Taxonomy
from save_finder.config import Settings, ensure_directories, load_settings
from save_finder.db import make_engine, make_session_factory, session_scope, upgrade_database
from save_finder.download import download_item
from save_finder.enrich import enrich_items
from save_finder.errors import ConfigError, SaveFinderError
from save_finder.importer import import_export
from save_finder.platforms.registry import get_adapter
from save_finder.repositories.items import database_stats
from save_finder.repositories.tags import sync_taxonomy

app = typer.Typer(
    help="Local library and classifier for media saved on social networks.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
taxonomy_app = typer.Typer(help="Taxonomy commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(taxonomy_app, name="taxonomy")

console = Console()


@app.callback()
def main_callback(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@contextmanager
def _session_scope(settings: Settings) -> Iterator[Session]:
    engine = make_engine(settings)
    try:
        with session_scope(make_session_factory(engine)) as session:
            yield session
    finally:
        engine.dispose()


def _load_taxonomy(settings: Settings) -> Taxonomy:
    try:
        return Taxonomy.load(settings.taxonomy_path)
    except SaveFinderError as exc:
        console.print(f"[red]Taxonomy error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@db_app.command("init")
def db_init() -> None:
    """Create directories, apply migrations and seed the taxonomy."""
    settings = _settings()
    ensure_directories(settings)
    try:
        upgrade_database(settings)
    except SaveFinderError as exc:
        console.print(f"[red]Database error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    taxonomy = _load_taxonomy(settings)
    with _session_scope(settings) as session:
        created, updated = sync_taxonomy(session, taxonomy)

    console.print(f"Database ready at [cyan]{settings.database.url}[/cyan]")
    console.print(
        f"Taxonomy v{taxonomy.version}: {len(taxonomy.broad)} broad, "
        f"{len(taxonomy.specific)} specific ({created} created, {updated} updated)"
    )


@app.command("import")
def import_export_command(
    platform: Annotated[str, typer.Argument(help="Platform slug, e.g. instagram.")],
    path: Annotated[
        Path,
        typer.Argument(
            help="Official export ZIP, extracted folder or a single JSON file.",
            exists=True,
            readable=True,
            file_okay=True,
            dir_okay=True,
        ),
    ],
) -> None:
    """Import an official export, idempotently."""
    settings = _settings()
    try:
        get_adapter(platform)
    except SaveFinderError as exc:
        console.print(f"[red]Platform error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        with _session_scope(settings) as session:
            result = import_export(
                session,
                platform=platform,
                path=path,
                tmp_dir=settings.paths.tmp,
                limits=settings.import_config,
            )
    except SaveFinderError as exc:
        console.print(f"[red]Import error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Import — {platform}")
    table.add_column("Result")
    table.add_column("Count", justify="right")
    table.add_row("New", str(result.new))
    table.add_row("Existing", str(result.existing))
    table.add_row("Ignored (unsupported URL / duplicate)", str(result.ignored))
    if result.unreadable:
        table.add_row("Unreadable files", str(result.unreadable))
    console.print(table)


@app.command("enrich")
def enrich_command(
    limit: Annotated[int, typer.Option(help="Maximum items to enrich.")] = 30,
    platform: Annotated[
        str | None, typer.Option(help="Only enrich items from this platform.")
    ] = None,
    retry_errors: Annotated[
        bool, typer.Option("--retry-errors", help="Also retry items left in error.")
    ] = False,
) -> None:
    """Fetch public metadata and thumbnails for pending items."""
    settings = _settings()
    ensure_directories(settings)
    if platform is not None:
        try:
            get_adapter(platform)
        except SaveFinderError as exc:
            console.print(f"[red]Platform error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    try:
        with _session_scope(settings) as session:
            report = enrich_items(
                session,
                settings,
                limit=limit,
                platform=platform,
                retry_errors=retry_errors,
            )
    except SaveFinderError as exc:
        console.print(f"[red]Enrichment error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not report.total:
        console.print("No pending items to enrich.")
        return

    table = Table(title="Enrichment")
    table.add_column("Item")
    table.add_column("Status")
    table.add_column("Type")
    table.add_column("Pieces", justify="right")
    table.add_column("Thumb")
    table.add_column("ms", justify="right")
    table.add_column("Error", overflow="fold")
    for outcome in report.outcomes:
        table.add_row(
            outcome.external_id,
            outcome.status,
            outcome.post_type or "—",
            str(outcome.media_count),
            "yes" if outcome.thumbnail_saved else "no",
            str(outcome.elapsed_ms),
            outcome.error or "",
        )
    console.print(table)

    summary = ", ".join(f"{status}: {count}" for status, count in sorted(report.counts.items()))
    console.print(f"Enriched {report.total} item(s) — {summary}")


@app.command("download")
def download_command(
    limit: Annotated[int, typer.Option(help="Maximum items to download.")] = 3,
    platform: Annotated[
        str | None, typer.Option(help="Only download items from this platform.")
    ] = None,
) -> None:
    """Download media pieces for enriched items (Fase 3)."""
    settings = _settings()
    ensure_directories(settings)
    try:
        with _session_scope(settings) as session:
            from sqlalchemy import select

            from save_finder.models import Item

            items = session.scalars(
                select(Item).where(Item.status == "enriched").limit(limit)
            ).all()
            results = []
            for item in items:
                res = download_item(
                    item, session, settings, timeout=settings.pipeline.download_timeout
                )
                results.append((item.external_id, res.pieces, res.cleaned, res.error))
            table = Table(title="Download (Fase 3)")
            table.add_column("Item")
            table.add_column("Pieces")
            table.add_column("Clean")
            table.add_column("Error")
            for ext, pieces, cleaned, err in results:
                table.add_row(
                    ext,
                    str(len(pieces)),
                    "yes" if cleaned else "no",
                    err or "",
                )
            console.print(table)
    except SaveFinderError as exc:
        console.print(f"[red]Download error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("stats")
def stats() -> None:
    """Show database counts by status and platform."""
    settings = _settings()
    with _session_scope(settings) as session:
        result = database_stats(session)

    table = Table(title="SaveFinder")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Items", str(result.total))
    table.add_row("Tags", str(result.tags))
    table.add_row("Database", settings.database.url)
    for platform, count in sorted(result.by_platform.items()):
        table.add_row(f"Platform: {platform}", str(count))
    for status, count in sorted(result.by_status.items()):
        table.add_row(f"Status: {status}", str(count))
    console.print(table)


@taxonomy_app.command("validate")
def taxonomy_validate() -> None:
    """Validate config/taxonomy.yaml against the closed-taxonomy rules."""
    settings = _settings()
    taxonomy = _load_taxonomy(settings)
    console.print(
        f"Taxonomy v{taxonomy.version} OK — {len(taxonomy.broad)} broad, "
        f"{len(taxonomy.specific)} specific, {len(taxonomy)} total."
    )


@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    reload: Annotated[bool, typer.Option(help="Reload on code changes.")] = False,
) -> None:
    """Start the local web API."""
    import uvicorn

    from save_finder.web.app import create_app

    settings = _settings()
    ensure_directories(settings)
    uvicorn.run(
        create_app(settings),
        host=host or settings.web.host,
        port=port or settings.web.port,
        reload=reload,
    )


@app.command("version")
def version() -> None:
    """Print the installed version."""
    console.print(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
