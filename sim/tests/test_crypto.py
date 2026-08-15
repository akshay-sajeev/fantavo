"""Tests for sim.api.crypto -- reversible encryption for per-user ESPN
credentials at rest (Auth Phase B). Unlike auth_view's argon2id password
hashing, this must be reversible: the recurring re-ingest job
(sim/api/reingest.py) decrypts stored credentials with nobody present."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sim.api.crypto import CredentialEncryptionError, decrypt_credential, encrypt_credential


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from sim.api.crypto import _get_fernet
    _get_fernet.cache_clear()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_roundtrips_to_the_original_value() -> None:
    plaintext = "a-real-espn-s2-cookie-value"
    ciphertext = encrypt_credential(plaintext)
    assert decrypt_credential(ciphertext) == plaintext


def test_encrypt_credential_output_does_not_contain_the_plaintext() -> None:
    plaintext = "a-very-specific-secret-cookie-xyz"
    ciphertext = encrypt_credential(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


def test_decrypt_credential_rejects_tampered_ciphertext() -> None:
    ciphertext = encrypt_credential("some-value")
    tampered = ciphertext[:-1] + (b"\x00" if ciphertext[-1:] != b"\x00" else b"\x01")
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(tampered)


def test_missing_key_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(CredentialEncryptionError, match="CREDENTIAL_ENCRYPTION_KEY"):
        encrypt_credential("anything")
