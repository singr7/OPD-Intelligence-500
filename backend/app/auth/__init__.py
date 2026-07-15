"""Auth: phone-OTP login, JWT issue/verify, RBAC guards (doc 02 §7)."""

from app.auth.hashing import hash_secret, verify_secret
from app.auth.otp import OtpError, OtpInvalid, OtpRateLimited, request_otp, verify_otp
from app.auth.rbac import (
    CLINICAL_ROLES,
    STAFF_ROLES,
    Principal,
    current_principal,
    require_admin,
    require_clinical,
    require_doctor,
    require_roles,
    require_staff,
)
from app.auth.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "hash_secret",
    "verify_secret",
    "OtpError",
    "OtpInvalid",
    "OtpRateLimited",
    "request_otp",
    "verify_otp",
    "Principal",
    "current_principal",
    "require_roles",
    "require_staff",
    "require_clinical",
    "require_admin",
    "require_doctor",
    "STAFF_ROLES",
    "CLINICAL_ROLES",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]
