import enum

from sqlalchemy import Column, Enum as SQLEnum, ForeignKey, Integer, String

from ..db.base import Base
from ..db.types import UTCDateTime


class RemediationStatus(str, enum.Enum):
    COMPLIANCE_REVIEW_REQUIRED = "COMPLIANCE_REVIEW_REQUIRED"


class Remediation(Base):
    """A persisted record of a purpose-lifecycle violation's required
    follow-up, created the moment the policy engine quarantines an
    asset (see policy_service._deny).

    Kept as its own table rather than folded into AuditLog: it has a
    genuinely mutable `status` that a future review workflow would
    update in place, unlike AuditLog rows, which are a permanent,
    append-only record of what happened and are never rewritten or
    deleted. This row is exactly the persistent, explicit remediation
    state the project's audit rules require -- it is never deleted.
    """

    __tablename__ = "remediations"

    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=False, index=True)
    grant_id = Column(Integer, ForeignKey("grants.id"), nullable=True, index=True)
    reason_code = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    corrective_action = Column(String, nullable=False)
    status = Column(SQLEnum(RemediationStatus), nullable=False, default=RemediationStatus.COMPLIANCE_REVIEW_REQUIRED)
    created_at = Column(UTCDateTime, nullable=False)
