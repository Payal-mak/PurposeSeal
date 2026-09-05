from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import ConflictError, UnauthorizedError
from ..core.security import hash_password, verify_password
from ..models.user import User
from ..schemas.auth import UserRegister


def _username_taken(db: Session, username: str) -> bool:
    return db.query(User).filter(User.username == username).first() is not None


def register_user(db: Session, payload: UserRegister) -> User:
    """Creates a new login identity. Open to anyone for any role
    (including ADMIN/COMPLIANCE_OFFICER) -- a real deployment would
    gate who can self-assign the higher-privilege roles; this is a
    deliberate hackathon-scope simplification, documented in
    docs/PROJECT_PROGRESS.md.
    """
    if _username_taken(db, payload.username):
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

    try:
        db.commit()
    except IntegrityError:
        # The pre-check above and this insert aren't atomic -- two
        # near-simultaneous registrations for the same username can both
        # pass the check before either commits. Rather than a raw,
        # unhandled IntegrityError surfacing as a 500, the database's own
        # unique constraint on `username` is the real safety net here,
        # and its failure is translated back to the same 409 a
        # sequential duplicate would get.
        db.rollback()
        raise ConflictError(f"Username '{payload.username}' is already taken.", error_code="username_taken") from None

    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash, user.password_salt):
        raise UnauthorizedError("Invalid username or password.", error_code="invalid_credentials")
    return user
