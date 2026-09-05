from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A DateTime that is always timezone-aware UTC on the Python side.

    SQLite has no native timezone-aware datetime type — `DateTime(timezone=True)`
    stores whatever naive value SQLAlchemy hands it and returns naive
    values back, silently dropping tzinfo. Since `clock.now()` always
    returns tz-aware UTC, comparing a value read back from the database
    (naive) against `clock.now()` (aware) raises `TypeError`. This type
    normalizes both directions so every DateTime column round-trips as
    tz-aware UTC regardless of backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
