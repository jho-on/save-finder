"""Database engine, sessions and migration entry point."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from save_finder.config import Settings
from save_finder.errors import DatabaseError


def make_engine(settings: Settings, *, echo: bool = False) -> Engine:
    """Create the SQLAlchemy engine with SQLite-safe defaults."""
    connect_args: dict[str, object] = {}
    if settings.database.url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        settings.database.url,
        echo=echo,
        future=True,
        connect_args=connect_args,
    )

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on failure and always close."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(settings: Settings) -> Config:
    config = Config(str(settings.root / "alembic.ini"))
    config.set_main_option("script_location", str(settings.root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database.url)
    return config


def upgrade_database(settings: Settings) -> None:
    """Apply every pending migration."""
    try:
        command.upgrade(alembic_config(settings), "head")
    except Exception as exc:  # Alembic raises several unrelated types
        raise DatabaseError(f"could not migrate database: {exc}") from exc
