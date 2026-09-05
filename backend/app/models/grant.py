import enum

from sqlalchemy import JSON, Column, Enum as SQLEnum, ForeignKey, Integer, String

from ..db.base import Base
from ..db.types import UTCDateTime
from .enums import AllowedOperation


class GrantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


def _all_operations() -> list[str]:
    return [op.value for op in AllowedOperation]


class Grant(Base):
    """PurposeSeal's PurposeGrant: why an actor may use a specific,
    already-existing protected DataAsset, for how long, and for which
    operations. `asset_id` is the authoritative reference to that asset —
    there is no free-text resource identifier alongside it, to avoid two
    sources of truth for "what this grant is about."
    """

    __tablename__ = "grants"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False)
    purpose = Column(String, nullable=False)
    asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=False, index=True)
    allowed_operations = Column(JSON, nullable=False, default=_all_operations)
    status = Column(SQLEnum(GrantStatus), nullable=False, default=GrantStatus.ACTIVE)
    created_at = Column(UTCDateTime, nullable=False)
    expires_at = Column(UTCDateTime, nullable=False)
