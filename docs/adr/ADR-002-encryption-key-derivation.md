# ADR-002: Encryption Key Derivation

**Status:** Accepted
**Date:** 2026-03-25

## Context

Fernet encryption for GitHub tokens and API keys used `hashlib.sha256(secret).digest()` for key derivation — a single hash iteration with no salting. While functional (SECRET_KEY is already high-entropy), this is not a proper KDF and uses the same derived key for all credential types.

## Decision

Switch to PBKDF2-SHA256 (600K iterations per OWASP 2024) with context-specific static salts:

- `synthesis-github-token-v1` for GitHub tokens
- `synthesis-api-credential-v1` for API keys

Shared utility at `backend/app/utils/crypto.py` with `derive_fernet()` (cached per secret+context) and `decrypt_with_migration()` for transparent legacy migration.

## Alternatives Considered

1. **Argon2** — superior KDF but requires `argon2-cffi` C extension. PBKDF2 is available via `cryptography` (already a dependency). Deferred.
2. **Separate SECRET_KEY per credential type** — overkill. Static salts achieve key separation with a single secret.
3. **No migration** — rejected: would invalidate all existing encrypted credentials on upgrade.

## Consequences

- ~200-500ms latency per key derivation (mitigated by `@lru_cache`)
- Transparent migration: first decrypt after upgrade triggers lazy re-encryption
- Context separation: compromising one credential type's Fernet key does not expose the other
- Static salts are acceptable because SECRET_KEY is already high-entropy random (not a password)

## Implementation status

**Shipped.**

- `backend/app/utils/crypto.py`: `derive_fernet()` (line 25, cached per secret+context), `_derive_legacy_fernet()` (line 45, legacy SHA-256 path), `decrypt_with_migration()` (line 51, transparent re-encrypt on first decrypt)
- GitHub token encryption uses context `"synthesis-github-token-v1"`; API credential encryption uses `"synthesis-api-credential-v1"`
- Lazy re-encryption: `decrypt_with_migration()` tries modern KDF, falls back to legacy on token mismatch, returns the plaintext so callers can re-save under the new key

No open migration gaps.
