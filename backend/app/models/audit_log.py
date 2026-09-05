from sqlalchemy import Column, Integer, String, Text

from ..db.base import Base
from ..db.types import UTCDateTime


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False, index=True)
    details = Column(Text, nullable=True)
    created_at = Column(UTCDateTime, nullable=False)
