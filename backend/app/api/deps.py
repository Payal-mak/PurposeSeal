"""Authentication/authorization dependencies.

Deliberately separate from services/policy_service.py: this module
only ever answers "who is this caller, and are they allowed to attempt
this *kind* of operation" (authentication + role-based authorization).
It never touches purpose/grant/expiry logic, and the policy engine
never imports from here -- a caller passing `require_role(...)` proves
nothing about whether any specific data use is within its authorized
purpose. See docs/PROJECT_PROGRESS.md ("Actor Roles and Minimal
Authentication") for the full explanation of this boundary.
"""

from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from ..core.errors import ForbiddenError, UnauthorizedError
from ..core.security import InvalidTokenError, decode_access_token
from ..db.session import get_db
from ..models.user import User, UserRole


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def get_current_user_optional(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolves the caller's identity from a Bearer token if one is
    present and valid; returns None otherwise without raising. Used by
    endpoints where authentication is optional but, when present, must
    override any identity claimed in the request body -- see
    api/retrievals.py, api/copies.py, api/uses.py, and the "identity
    cannot be spoofed" design decision in the docs.
    """
    token = _extract_bearer_token(authorization)
    if token is None:
        return None
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        return None
    return db.query(User).filter(User.username == payload["sub"]).first()


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the caller's identity, raising 401 if no valid token was
    presented. Used by endpoints that require authentication outright."""
    token = _extract_bearer_token(authorization)
    if token is None:
        raise UnauthorizedError("Authentication required.", error_code="not_authenticated")
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise UnauthorizedError("Invalid or expired token.", error_code="invalid_token") from None
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if user is None:
        raise UnauthorizedError("User no longer exists.", error_code="invalid_token")
    return user


def require_role(*allowed_roles: UserRole):
    """Dependency factory: 403s unless the authenticated user's role is
    one of `allowed_roles`. Pure role-based AUTHORIZATION -- "is this
    user the *kind* of actor allowed to attempt this operation at all,"
    never "is this specific data use within its authorized purpose,"
    which remains entirely the policy engine's job.
    """

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise ForbiddenError(
                f"Role '{user.role.value}' is not permitted to perform this operation.",
                error_code="role_not_permitted",
            )
        return user

    return _dependency
