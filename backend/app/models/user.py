import enum

from sqlalchemy import Column, Enum as SQLEnum, Integer, String

from ..db.base import Base
from ..db.types import UTCDateTime


class UserRole(str, enum.Enum):
    """Who someone is inside PurposeSeal, for the purpose of deciding
    which *kinds* of operations they may attempt at all (role-based
    authorization). This is deliberately independent of PURPOSE
    authorization: the policy engine (services/policy_service.py)
    decides whether a specific data use is within its authorized
    purpose/expiry/operation, and knows nothing about roles. A user
    logging in successfully as a RESEARCHER proves who they are and
    that they may attempt researcher-level operations -- it proves
    nothing about whether any specific purpose grant covers what
    they're about to do with a specific asset. See docs/PROJECT_PROGRESS.md
    ("Actor Roles and Minimal Authentication") for the full explanation.
    """

    RESEARCHER = "RESEARCHER"
    CLINICIAN = "CLINICIAN"
    ANALYST = "ANALYST"
    COMPLIANCE_OFFICER = "COMPLIANCE_OFFICER"
    ADMIN = "ADMIN"


class User(Base):
    """A PurposeSeal login identity. Intentionally minimal: this table
    answers "who is this caller, and what role do they hold" and
    nothing else. It is not referenced by Grant/DataAsset/AuditLog via
    foreign key -- those still use free-text `subject`/`actor` strings,
    unchanged, since rewiring the whole domain model to reference User
    is out of scope for "minimal authentication" and would blur the
    authentication/purpose-authorization boundary this feature is
    explicitly meant to keep separate. Authentication only affects
    *how* an `actor` string gets set at the API boundary (see
    api/deps.py, api/retrievals.py, api/copies.py, api/uses.py) --
    never how the policy engine evaluates it.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False)
    created_at = Column(UTCDateTime, nullable=False)
