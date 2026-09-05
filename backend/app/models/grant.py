import enum

from sqlalchemy import Column, DateTime, Enum as SQLEnum, Integer, String

from ..db.base import Base


class GrantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class Grant(Base):
    __tablename__ = "grants"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False)
    purpose = Column(String, nullable=False)
    resource_id = Column(String, nullable=False, index=True)
    status = Column(SQLEnum(GrantStatus), nullable=False, default=GrantStatus.ACTIVE)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
