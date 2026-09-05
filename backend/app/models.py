import enum

from sqlalchemy import Column, DateTime, Enum as SQLEnum, Integer, String, Text

from .database import Base


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


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False, index=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
