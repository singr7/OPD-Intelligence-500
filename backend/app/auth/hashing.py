"""Argon2 hashing for OTP codes, refresh handles, and staff passwords.

Argon2id via argon2-cffi directly — passlib is unmaintained and its bcrypt
backend breaks against bcrypt 4.x.

OTPs are 6 digits: a leaked hash of one is brute-forceable regardless of the KDF,
so the real defences are the short TTL, the attempt cap, and single use
(`app.auth.otp`). Hashing them keeps a database leak from yielding *live* codes.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_secret(secret: str) -> str:
    return _hasher.hash(secret)


def verify_secret(secret: str, hashed: str) -> bool:
    """Constant-time verify. Returns False rather than raising on a mismatch, so
    callers can't accidentally leak the reason via an exception path."""
    try:
        return _hasher.verify(hashed, secret)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
