from datetime import datetime, timedelta, timezone


class Clock:
    """Single source of truth for "now" across the app.

    Business logic must always call clock.now() instead of datetime.now()
    directly. In production the offset stays zero and this behaves like
    the real clock. In demo/dev mode, advance() lets us simulate minutes
    or hours passing (e.g. a grant expiring) within seconds, without
    changing any business logic.
    """

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self._offset

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> datetime:
        self._offset += timedelta(seconds=seconds, minutes=minutes, hours=hours)
        return self.now()

    def reset(self) -> None:
        self._offset = timedelta(0)


clock = Clock()
