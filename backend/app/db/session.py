from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from .base import Base

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def enable_sqlite_foreign_keys(target_engine) -> None:
    """SQLite ignores foreign key constraints unless told otherwise per
    connection. Without this, an invalid parent/root/origin-grant
    reference on DataAsset would silently succeed instead of failing."""

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


if settings.database_url.startswith("sqlite"):
    enable_sqlite_foreign_keys(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Models must be imported before this runs so
    they are registered on Base.metadata."""
    from .. import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
