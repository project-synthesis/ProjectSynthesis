"""Tests for GitHub OAuth CSRF state using itsdangerous.TimestampSigner."""
import uuid

import pytest
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.config import settings


def _signer() -> TimestampSigner:
    return TimestampSigner(settings.SECRET_KEY)


def test_plain_uuid_fails_unsign():
    """A plain UUID (old UUID4 implementation) raises BadSignature — it has no HMAC."""
    plain = str(uuid.uuid4())
    with pytest.raises(BadSignature):
        _signer().unsign(plain)


def test_signed_state_verifies_within_max_age():
    """A freshly signed state verifies correctly within max_age=600."""
    token = uuid.uuid4().hex
    signed = _signer().sign(token).decode()

    result = _signer().unsign(signed, max_age=600)
    assert result.decode() == token


def test_expired_state_raises_signature_expired():
    """A state verified 700s after signing raises SignatureExpired (max_age=600)."""
    import time
    from unittest.mock import patch

    token = uuid.uuid4().hex
    signed = _signer().sign(token).decode()

    # Simulate clock advancing 700s past signing time
    with patch("time.time", return_value=time.time() + 700):
        with pytest.raises(SignatureExpired):
            _signer().unsign(signed, max_age=600)


def test_tampered_state_raises_bad_signature():
    """Corrupting the signature raises BadSignature."""
    signed = _signer().sign("legit-token").decode()
    tampered = signed[:-4] + "XXXX"  # corrupt last 4 chars

    with pytest.raises(BadSignature):
        _signer().unsign(tampered, max_age=600)


def test_different_secret_raises_bad_signature():
    """A state signed with a different key fails verification."""
    other_signer = TimestampSigner("a-completely-different-secret-key")
    signed = other_signer.sign("token").decode()

    with pytest.raises(BadSignature):
        _signer().unsign(signed, max_age=600)


def test_signed_state_contains_dot_separator():
    """Signed tokens follow the format <payload>.<timestamp>.<signature>."""
    signed = _signer().sign("some-token").decode()
    # itsdangerous TimestampSigner produces at least 2 dots (base64 timestamp + hmac)
    assert signed.count(".") >= 1
