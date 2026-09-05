import enum

from sqlalchemy import JSON, Column, DateTime, Enum as SQLEnum, Integer, String

from ..db.base import Base
from .enums import AllowedOperation


class GrantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


def _all_operations() -> list[str]:
    return [op.value for op in AllowedOperation]


class Grant(Base):
    """PurposeSeal's PurposeGrant: why an actor may use a resource, for how
    long, and for which operations."""

    __tablename__ = "grants"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False)
    purpose = Column(String, nullable=False)
    resource_id = Column(String, nullable=False, index=True)
    allowed_operations = Column(JSON, nullable=False, default=_all_operations)
    status = Column(SQLEnum(GrantStatus), nullable=False, default=GrantStatus.ACTIVE)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
