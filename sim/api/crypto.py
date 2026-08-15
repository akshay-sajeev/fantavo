"""Symmetric encryption for per-user ESPN credentials at rest (Auth Phase B).

Reversible, unlike auth_view's argon2id password hashing -- the recurring
re-ingest job (sim/api/reingest.py) must decrypt a user's stored espn_s2/
SWID with nobody present, so a one-way hash cannot be used here. Fernet
(AES-128-CBC + HMAC, from the `cryptography` package) is authenticated
(tampered ciphertext fails to decrypt rather than silently returning
garbage), and there is no need for asymmetric encryption -- only this same
server process ever decrypts what it encrypts.

CREDENTIAL_ENCRYPTION_KEY is read from `.env` via sim.api.env's loader (same
convention as GEMINI_API_KEY -- see sim.api.analyst_view._get_client for the
identical lazy/memoized pattern this mirrors), never stored in the
database, never logged.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from sim.api.env import load_dotenv_once


class CredentialEncryptionError(ValueError):
    """CREDENTIAL_ENCRYPTION_KEY is missing/malformed, or a ciphertext
    failed to decrypt (wrong key, or corrupted/tampered data). Never
    includes the key or the credential itself in its message."""


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Lazy, memoized -- constructed on first real use, not at import time,
    so importing this module never requires CREDENTIAL_ENCRYPTION_KEY to be
    set (tests set it via the _fake_key autouse fixture instead)."""
    load_dotenv_once()
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set -- copy .env.example to .env "
            "and fill it in. (This message never includes the key itself.)"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def encrypt_credential(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_credential(ciphertext: bytes) -> str:
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "stored credential could not be decrypted -- wrong key or corrupted data"
        ) from exc
