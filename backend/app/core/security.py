"""Password hashing and access tokens, standard-library only.

This project has zero crypto dependencies today (see backend/requirements.txt),
and a hand-rolled HS256 JWT is a simple, well-understood construction
(header.payload.signature, all base64url, HMAC-SHA256 signed) that the
standard library already covers correctly (`hmac.compare_digest` for
constant-time signature verification; `hashlib.pbkdf2_hmac` -- a NIST-
recommended KDF -- for password hashing). Adding a third-party JWT or
password-hashing library wasn't judged to earn its place for a
hackathon MVP's login/token needs; a real production deployment might
reasonably prefer one (and would definitely want a non-default
`PURPOSESEAL_SECRET_KEY`).
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from typing import Any

from .clock import clock
from .config import get_settings

# OWASP's current PBKDF2-SHA256 guidance is 600,000+ iterations; this is
# deliberately lower (still a legitimate, commonly-cited baseline) so a
# test suite that registers/logs in many users stays fast. A real
# deployment handling real credentials should raise this.
_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Returns (hash_hex, salt_hex). Pass an existing salt to verify a
    password against a stored hash; omit it to hash a new password."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class InvalidTokenError(Exception):
    pass


def create_access_token(*, subject: str, role: str, expires_minutes: float = 60) -> str:
    """Issues a signed access token identifying `subject` (a username)
    and their `role` at issuance time. Timestamps use the project's
    simulated `clock`, the same single source of "now" as every other
    feature -- a demo scenario advancing simulated time doesn't produce
    surprising token-expiry behavior, and expiry is just as
    deterministic/testable as everything else here.
    """
    now = clock.now()
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    signing_input = f"{_b64url_encode(json.dumps(header).encode())}.{_b64url_encode(json.dumps(payload).encode())}"
    signature = hmac.new(get_settings().secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Verifies signature and expiry, returning the token's payload
    (`sub`, `role`, `iat`, `exp`). Raises InvalidTokenError for any
    malformed, tampered-with, or expired token."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise InvalidTokenError("Malformed token") from None

    signing_input = f"{header_b64}.{payload_b64}"
    expected_signature = hmac.new(
        get_settings().secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64url_encode(expected_signature), signature_b64):
        raise InvalidTokenError("Invalid signature")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidTokenError("Malformed token payload") from exc

    if payload.get("exp") is not None and clock.now().timestamp() > payload["exp"]:
        raise InvalidTokenError("Token expired")

    return payload
