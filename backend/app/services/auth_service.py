from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import ConflictError, UnauthorizedError
from ..core.security import hash_password, verify_password
from ..models.user import User
from ..schemas.auth import UserRegister


def register_user(db: Session, payload: UserRegister) -> User:
    """Creates a new login identity. Open to anyone for any role
    (including ADMIN/COMPLIANCE_OFFICER) -- a real deployment would
    gate who can self-assign the higher-privilege roles; this is a
    deliberate hackathon-scope simplification, documented in
    docs/PROJECT_PROGRESS.md.
    """
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing is not None:
        raise ConflictError(f"Username '{payload.username}' is already taken.", error_code="username_taken")

    password_hash, salt = hash_password(payload.password)
    user = User(
        username=payload.username,
        password_hash=password_hash,
        password_salt=salt,
        role=payload.role,
        created_at=clock.now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash, user.password_salt):
        raise UnauthorizedError("Invalid username or password.", error_code="invalid_credentials")
    return user
