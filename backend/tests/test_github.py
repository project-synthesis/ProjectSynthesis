"""Tests for GitHubService — Fernet token encryption and OAuth URL building."""

import pytest
from cryptography.fernet import InvalidToken

from app.services.github_service import GitHubService

_SECRET = "a-sufficiently-long-secret-key-for-testing-purposes"


@pytest.fixture
def svc() -> GitHubService:
    return GitHubService(secret_key=_SECRET, client_id="test_client_id", client_secret="test_client_secret")


def test_encrypt_decrypt_roundtrip(svc: GitHubService) -> None:
    token = "ghp_abc123testtoken"
    encrypted = svc.encrypt_token(token)
    assert svc.decrypt_token(encrypted) == token


def test_different_tokens_produce_different_ciphertext(svc: GitHubService) -> None:
    enc1 = svc.encrypt_token("token_one")
    enc2 = svc.encrypt_token("token_two")
    assert enc1 != enc2


def test_decrypt_invalid_raises(svc: GitHubService) -> None:
    with pytest.raises(Exception):
        svc.decrypt_token(b"this-is-not-valid-fernet-ciphertext")


def test_build_oauth_url(svc: GitHubService) -> None:
    url = svc.build_oauth_url(state="random_state_value")
    assert "test_client_id" in url
    assert "random_state_value" in url
    assert url.startswith("https://github.com/login/oauth/authorize")
