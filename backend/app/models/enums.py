import enum


class AllowedOperation(str, enum.Enum):
    """What an actor is permitted to do with data retrieved under a grant.

    Shared between Grant (what it authorizes) and, later, the policy
    engine (what an attempted use is checked against) — kept in its own
    module rather than tucked inside grant.py so it isn't seen as
    grant-only.
    """

    VIEW = "VIEW"
    ANALYZE = "ANALYZE"
    COPY = "COPY"
    EXPORT = "EXPORT"
