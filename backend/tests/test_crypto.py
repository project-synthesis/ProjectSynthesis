"""Security hardening tests — PR 2: crypto utility."""

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken


class TestCryptoUtility:
    """W5: PBKDF2 key derivation and context separation."""

    def test_derive_fernet_returns_valid_fernet(self):
        from app.utils.crypto import derive_fernet
        f = derive_fernet("test-secret", "test-context-v1")
        encrypted = f.encrypt(b"hello")
        assert f.decrypt(encrypted) == b"hello"

    def test_different_contexts_produce_different_keys(self):
        from app.utils.crypto import derive_fernet
        f1 = derive_fernet("same-secret", "context-a-v1")
        f2 = derive_fernet("same-secret", "context-b-v1")
        encrypted = f1.encrypt(b"data")
        with pytest.raises(InvalidToken):
            f2.decrypt(encrypted)

    def test_legacy_migration_transparently_reencrypts(self):
        from app.utils.crypto import decrypt_with_migration, derive_fernet

        secret = "test-secret-key"
        context = "test-context-v1"

        # Encrypt with legacy SHA256 method
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        legacy_fernet = Fernet(legacy_key)
        legacy_ciphertext = legacy_fernet.encrypt(b"my-api-key")

        persisted = {}
        def persist_fn(new_ciphertext: bytes):
            persisted["data"] = new_ciphertext

        plaintext = decrypt_with_migration(legacy_ciphertext, secret, context, persist_fn)
        assert plaintext == b"my-api-key"
        assert "data" in persisted

        # Re-encrypted ciphertext should work with new KDF
        new_fernet = derive_fernet(secret, context)
        assert new_fernet.decrypt(persisted["data"]) == b"my-api-key"

    def test_decrypt_with_migration_new_kdf_first(self):
        from app.utils.crypto import decrypt_with_migration, derive_fernet

        secret = "test-secret"
        context = "ctx-v1"
        f = derive_fernet(secret, context)
        ciphertext = f.encrypt(b"data")

        persisted = {}
        plaintext = decrypt_with_migration(ciphertext, secret, context, lambda c: persisted.update(data=c))
        assert plaintext == b"data"
        assert "data" not in persisted  # No migration needed

    def test_both_fail_raises_invalid_token(self):
        from app.utils.crypto import decrypt_with_migration
        with pytest.raises(InvalidToken):
            decrypt_with_migration(b"garbage", "secret", "ctx-v1")
