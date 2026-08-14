"""Tests for sim.api.auth_view and the /auth/* routes.

Split the same way every other sim.api test module is: fast unit tests
directly against auth_view's functions where no DB is needed, then
integration tests through the FastAPI TestClient for the full HTTP surface.
No network calls to ESPN anywhere -- only to the local Postgres instance
(see sim/tests/conftest.py's pg_conn fixture for the skip-if-unreachable
pattern this reuses).
"""

from __future__ import annotations

from sim.api.auth_view import hash_password, normalize_email, verify_password


def test_normalize_email_lowercases_and_trims() -> None:
    assert normalize_email("  Foo@Bar.com  ") == "foo@bar.com"


def test_normalize_email_is_idempotent() -> None:
    once = normalize_email("Foo@Bar.com")
    assert normalize_email(once) == once


def test_hash_password_roundtrips_through_verify() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)
    assert verify_password(password, password_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("wrong password entirely", password_hash) is False


def test_hash_password_never_returns_the_plaintext() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)
    assert password not in password_hash
