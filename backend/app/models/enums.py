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


class DerivationType(str, enum.Enum):
    """Whether a new child asset is an exact copy of its parent's content
    or a transformed derivative — decides which audit event is written
    (COPY_CREATED vs DERIVED_ASSET_CREATED) and which fingerprinting
    strategy applies. Not persisted on DataAsset itself; it only ever
    exists as a request-time classification, since everything it implies
    (parent/root/origin linkage) is already captured by DataAsset's
    existing columns.
    """

    COPY = "COPY"
    DERIVED = "DERIVED"
