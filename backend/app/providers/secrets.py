"""Encryption for the vendor credentials the admin console stores (S-GL.1).

This is the first secret this codebase keeps in its own database, and the reason
it is allowed to is doc 12 §4: on the box, enabling a vendor means editing `.env`
and restarting, which is not something a hospital administrator can do when the
Exotel number finally arrives. The alternative — a plaintext column — would mean a
database dump is a set of live vendor credentials, and a nightly backup is a copy
of them.

**Fernet, from `cryptography`.** AES-128-CBC with an HMAC-SHA256 tag and a
timestamp, in one reviewed primitive. Nothing here composes its own construction;
the only judgement calls this module makes are about the key.

**The key is not in the database.** `SECRETS_KEY` holds a Fernet key, so a stolen
dump is ciphertext. When it is unset — every box today, including the pilot's —
one is *derived* from `JWT_SECRET` via HKDF so the feature works without a new
deployment step. That derivation is a convenience with a real cost, and it is
recorded on every row: `key_id` is a fingerprint of the key that encrypted the
value, so rotating `JWT_SECRET` produces an honest "these credentials were
encrypted with a different key, set them again" instead of a decrypt that fails as
though nothing were stored. Setting `SECRETS_KEY` explicitly decouples the two and
is the right end state (see HANDOFF).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Domain separation for the derived key: the bytes that encrypt a vendor token
#: must not be the bytes that sign a JWT, even when both come from one secret.
_HKDF_INFO = b"opd.provider_secrets.v1"


class SecretUnreadable(Exception):
    """Stored ciphertext will not decrypt under the current key.

    Raised, never swallowed into "not configured": an operator who rotated a key
    needs to know the credentials are *there and unreadable*, which is a different
    problem from never having entered them, and has a different fix.
    """


def _key_bytes(settings: Settings) -> bytes:
    """The Fernet key: explicit if configured, HKDF-derived from `JWT_SECRET` if not."""
    explicit = settings.secrets_key.strip()
    if explicit:
        return explicit.encode()
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        settings.jwt_secret.encode()
    )
    return base64.urlsafe_b64encode(derived)


def key_id(settings: Settings | None = None) -> str:
    """A short fingerprint of the active key — stored beside each ciphertext.

    A hash of the key, not the key: it identifies which key was used without being
    usable to reconstruct it, so it is safe next to the value it describes and
    safe to show in a console.
    """
    settings = settings or get_settings()
    return hashlib.sha256(_key_bytes(settings)).hexdigest()[:16]


def _fernet(settings: Settings) -> Fernet:
    try:
        return Fernet(_key_bytes(settings))
    except (ValueError, TypeError) as exc:
        raise SecretUnreadable(
            "SECRETS_KEY is not a valid Fernet key — generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"`'
        ) from exc


def encrypt(values: dict[str, Any], settings: Settings | None = None) -> tuple[str, str]:
    """A credential mapping → `(ciphertext, key_id)`."""
    settings = settings or get_settings()
    token = _fernet(settings).encrypt(json.dumps(values, sort_keys=True).encode())
    return token.decode(), key_id(settings)


def decrypt(
    ciphertext: str, stored_key_id: str = "", settings: Settings | None = None
) -> dict[str, Any]:
    """`ciphertext` → the credential mapping, or `SecretUnreadable`.

    The key check comes first so the common operational mistake — the key changed
    — is reported as itself rather than as a generic decryption failure.
    """
    settings = settings or get_settings()
    current = key_id(settings)
    if stored_key_id and stored_key_id != current:
        raise SecretUnreadable(
            f"stored under key {stored_key_id}, current key is {current} — "
            "these credentials must be entered again"
        )
    try:
        return json.loads(_fernet(settings).decrypt(ciphertext.encode()).decode())
    except (InvalidToken, ValueError) as exc:
        raise SecretUnreadable("stored credentials could not be decrypted") from exc


def using_a_derived_key(settings: Settings | None = None) -> bool:
    """True when no `SECRETS_KEY` is set and the key comes from `JWT_SECRET`.

    Surfaced in the console, because it means the two secrets are coupled:
    rotating the JWT secret makes every stored credential unreadable.
    """
    settings = settings or get_settings()
    return not settings.secrets_key.strip()
