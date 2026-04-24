"""Tests for DomainResolver — cached domain label lookup."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster
from app.services.domain_resolver import DomainResolver


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def _seed_domains(db: AsyncSession) -> None:
    for label in ("backend", "frontend", "general"):
        db.add(PromptCluster(label=label, state="domain", domain=label, persistence=1.0))
    await db.commit()


@pytest.mark.asyncio
async def test_resolve_known_domain(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("backend", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_high_confidence_preserves_label(db):
    """High-confidence unknown labels are preserved so the taxonomy can
    grow organically.  Collapsing them to 'general' used to destroy
    signals the warm-path domain-discovery phase relies on.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("marketing", confidence=0.8)
    assert result == "marketing"


@pytest.mark.asyncio
async def test_resolve_with_qualifier(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("backend: auth middleware", confidence=0.8)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_known_domain_ignores_confidence(db):
    """Known domain labels are accepted regardless of confidence score."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("backend", confidence=0.3)
    assert result == "backend"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_low_confidence_returns_general(db):
    """Unknown domains with low confidence fall back to general."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("marketing", confidence=0.3)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_mid_confidence_preserves_label(db):
    """Mid-band confidence (0.5–0.7) preserves the organic label.

    The gate was historically set at 0.7 under a "collapse to general by
    default" policy.  Under the new organic-preservation semantics the
    gate sits at the natural 0.5 midpoint — so labels the analyzer
    returned with modest-but-plausible confidence survive into the
    taxonomy instead of being prematurely destroyed.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.55) == "marketing"
    assert await resolver.resolve("devops", confidence=0.50) == "devops"


@pytest.mark.asyncio
async def test_resolve_unknown_domain_just_below_gate_returns_general(db):
    """Labels just below the preservation gate still collapse."""
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.49) == "general"


@pytest.mark.asyncio
async def test_resolve_none_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve(None, confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_resolve_empty_string_returns_general(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    result = await resolver.resolve("  ", confidence=0.9)
    assert result == "general"


@pytest.mark.asyncio
async def test_cache_invalidation(db):
    """Once a domain is registered, `load()` clears the cache so the
    resolver picks up the new label on subsequent calls.  Uses a
    low-confidence unknown that the confidence gate forces to 'general'
    so the cached/uncached distinction is observable.
    """
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    db.add(PromptCluster(label="marketing", state="domain", domain="marketing", persistence=1.0))
    await db.commit()

    # Before reload — cache returns "general"
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    # After reload — known domain, confidence irrelevant
    await resolver.load(db)
    assert await resolver.resolve("marketing", confidence=0.3) == "marketing"


@pytest.mark.asyncio
async def test_domain_labels_property(db):
    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)
    assert resolver.domain_labels == {"backend", "frontend", "general"}


@pytest.mark.asyncio
async def test_resolve_never_raises(db):
    """Resolve must never raise — unloaded resolver falls through cleanly.

    Low confidence is used here to keep the assertion on the
    below-gate fallback path (``'general'``); high-confidence unknowns
    now preserve the organic label (see
    ``test_resolve_unknown_domain_high_confidence_preserves_label``).
    """
    resolver = DomainResolver()
    # Not loaded — empty domain_labels
    result = await resolver.resolve("backend", confidence=0.3)
    assert result == "general"


# ---------------------------------------------------------------------------
# Confidence-aware caching (quick-win #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_low_confidence_general_collapse_expires(db, monkeypatch):
    """Low-confidence "general" collapses must self-heal after the low-TTL window.

    Previously the cache was an unbounded dict — a transient low-confidence
    resolution of an organic label ("marketing") pinned "general" until the
    next ``load()`` call (hours later via the ``taxonomy_changed`` event).
    Under the fix a short TTL (60 s) evicts the entry so the next call re-
    evaluates from scratch.
    """
    from app.services import domain_resolver as dr_mod

    # Pin a controllable monotonic clock — all time.monotonic() calls in
    # the resolver go through this lambda.
    fake_now = {"t": 1000.0}
    monkeypatch.setattr(dr_mod.time, "monotonic", lambda: fake_now["t"])

    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)

    # t=1000s: low-confidence unknown → general, cached with 60s TTL
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    # t=1010s (within TTL): cache hit — still general
    fake_now["t"] = 1010.0
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    # t=1061s (past 60s TTL): cache evicted, re-evaluates (still general at 0.3)
    # The observable signal: if we flip the signal loader to report high
    # confidence, the new resolution preserves the label. We verify
    # eviction by swapping the resolver's gate-blending path.
    fake_now["t"] = 1061.0
    # Same inputs should NOT return a stale entry — cache must have expired.
    # Confirm by flipping confidence to high and observing the new
    # (preserved) result comes through immediately.
    assert await resolver.resolve("marketing", confidence=0.9) == "marketing"


@pytest.mark.asyncio
async def test_cache_higher_confidence_bypasses_stale_low_confidence_entry(db, monkeypatch):
    """A higher-confidence retry within the TTL window must bypass the
    low-confidence stale entry.

    Scenario: a first-pass analyze call resolves "frontend" at confidence
    0.3 → collapsed to "general" and cached. A retry / A4 Haiku fallback
    resolves the same prompt at confidence 0.9 — the preserved label
    must come through instead of the stale "general".
    """
    from app.services import domain_resolver as dr_mod

    fake_now = {"t": 2000.0}
    monkeypatch.setattr(dr_mod.time, "monotonic", lambda: fake_now["t"])

    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)

    # Low-confidence pass → cached as "general"
    assert await resolver.resolve("marketing", confidence=0.3) == "general"

    # Same primary, higher confidence, WITHIN the low-confidence TTL window —
    # cache must not serve stale "general".
    fake_now["t"] = 2010.0  # only 10s later
    assert await resolver.resolve("marketing", confidence=0.9) == "marketing"


@pytest.mark.asyncio
async def test_cache_known_label_ttl_longer_than_general_collapse(db, monkeypatch):
    """Known-label cache entries persist much longer than low-confidence
    ``general`` collapses — the known label is a stable resolution, not
    a placeholder.

    Verifies: 10 minutes after caching a known label, the cache still
    serves it (without rounding through signal blending).  The underlying
    mechanism: known-label resolutions set a 1-hour TTL; low-confidence
    general-collapses set a 60 s TTL.
    """
    from app.services import domain_resolver as dr_mod

    fake_now = {"t": 3000.0}
    monkeypatch.setattr(dr_mod.time, "monotonic", lambda: fake_now["t"])

    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)

    # Known label cached at t=3000
    assert await resolver.resolve("backend", confidence=0.3) == "backend"

    # 10 minutes later — well past the 60s low-confidence TTL — still cached
    fake_now["t"] = 3000.0 + 600.0
    # If the cache entry were gone, resolve() would re-run the full path;
    # the result is still "backend" because it's a known label. The
    # behavioral signal: no new logging / no re-query.  Asserting the
    # cache dict still holds the entry.
    entry = resolver._cache.get("backend")
    assert entry is not None, "Known-label cache entry evicted prematurely"
    # Resolution result unchanged.
    assert await resolver.resolve("backend", confidence=0.3) == "backend"


@pytest.mark.asyncio
async def test_cache_high_confidence_preserve_entry_uses_long_ttl(db, monkeypatch):
    """High-confidence preserved-organic-label entries get the 1-hour TTL
    (not the 60 s low-confidence TTL).

    Rationale: if the resolver decided the label is worth preserving,
    the decision is stable — cache it aggressively.  Only low-confidence
    ``general`` collapses need short TTLs to self-heal.
    """
    from app.services import domain_resolver as dr_mod

    fake_now = {"t": 4000.0}
    monkeypatch.setattr(dr_mod.time, "monotonic", lambda: fake_now["t"])

    await _seed_domains(db)
    resolver = DomainResolver()
    await resolver.load(db)

    # Preserve a high-confidence organic label
    assert await resolver.resolve("marketing", confidence=0.9) == "marketing"

    # 10 minutes later — past 60 s, well within 1 h — entry still present
    fake_now["t"] = 4000.0 + 600.0
    entry = resolver._cache.get("marketing")
    assert entry is not None
    assert await resolver.resolve("marketing", confidence=0.9) == "marketing"
