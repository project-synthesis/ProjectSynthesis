"""Tests for domain + sub-domain readiness analytics.

Covers:
- The extracted three-source cascade primitive
- Sub-domain emergence tiers (ready / warming / inert) and adaptive threshold
- Domain stability guards + dissolution risk
- Engine ↔ readiness parity (same inputs ⇒ same decision)
- TTL cache invalidation on member-count change

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy._constants import (
    DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
    DOMAIN_DISSOLUTION_MEMBER_CEILING,
    DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
    SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
    SUB_DOMAIN_QUALIFIER_SCALE_RATE,
)
from app.services.taxonomy.cluster_meta import write_meta

EMBEDDING_DIM = 384


def _emb(seed: int) -> bytes:
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-9
    return vec.tobytes()


def _make_domain(
    label: str,
    *,
    generated_qualifiers: dict[str, list[str]] | None = None,
    signal_keywords: list[list] | None = None,
    age_hours: float = 72.0,
    parent_id: str | None = None,
) -> PromptCluster:
    meta_kwargs: dict = {"source": "discovered"}
    if generated_qualifiers is not None:
        meta_kwargs["generated_qualifiers"] = generated_qualifiers
    if signal_keywords is not None:
        meta_kwargs["signal_keywords"] = signal_keywords
    return PromptCluster(
        label=label,
        state="domain",
        domain=label,
        task_type="general",
        parent_id=parent_id,
        persistence=1.0,
        color_hex="#aabbcc",
        centroid_embedding=_emb(hash(label) % 2**31),
        cluster_metadata=write_meta(None, **meta_kwargs),
        created_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(hours=age_hours)
        ),
    )


def _make_cluster(label: str, parent_id: str, *, domain: str, member_count: int = 5) -> PromptCluster:
    return PromptCluster(
        label=label,
        state="active",
        domain=domain,
        parent_id=parent_id,
        member_count=member_count,
        centroid_embedding=_emb(hash(label) % 2**31),
    )


def _make_opt(
    cluster_id: str,
    *,
    domain: str = "backend",
    domain_raw: str | None = None,
    intent_label: str | None = None,
    raw_prompt: str = "sample prompt",
) -> Optimization:
    return Optimization(
        raw_prompt=raw_prompt,
        cluster_id=cluster_id,
        domain=domain,
        domain_raw=domain_raw,
        intent_label=intent_label,
        overall_score=7.0,
        status="completed",
    )


async def _seed_domain_with_opts(
    db,
    domain_label: str,
    opts: list[dict],
    *,
    generated_qualifiers: dict[str, list[str]] | None = None,
    signal_keywords: list[list] | None = None,
    age_hours: float = 72.0,
    cluster_count: int = 2,
) -> PromptCluster:
    """Seed a domain, N child clusters, and opts spread across them."""
    domain = _make_domain(
        domain_label,
        generated_qualifiers=generated_qualifiers,
        signal_keywords=signal_keywords,
        age_hours=age_hours,
    )
    db.add(domain)
    await db.flush()

    clusters: list[PromptCluster] = []
    for i in range(cluster_count):
        c = _make_cluster(f"{domain_label}-c{i}", parent_id=domain.id, domain=domain_label)
        db.add(c)
        clusters.append(c)
    await db.flush()

    for i, opt_kwargs in enumerate(opts):
        target_cluster = clusters[i % len(clusters)]
        db.add(_make_opt(target_cluster.id, domain=domain_label, **opt_kwargs))

    await db.flush()
    return domain


# ---------------------------------------------------------------------------
# Cascade primitive
# ---------------------------------------------------------------------------


class TestCascadePrimitive:
    @pytest.mark.asyncio
    async def test_empty_domain_produces_no_candidates(self, db):
        from app.services.taxonomy.sub_domain_readiness import compute_qualifier_cascade

        domain = await _seed_domain_with_opts(db, "backend", opts=[])
        result = await compute_qualifier_cascade(db, domain)

        assert result.total_opts == 0
        assert result.qualifier_counts == {}
        assert result.source_breakdown == {
            "domain_raw": 0,
            "intent_label": 0,
            "tf_idf": 0,
        }

    @pytest.mark.asyncio
    async def test_source_1_domain_raw_dominates(self, db):
        from app.services.taxonomy.sub_domain_readiness import compute_qualifier_cascade

        # 8 opts all tagged `backend: auth`
        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth", "jwt", "login"]},
        )
        result = await compute_qualifier_cascade(db, domain)

        assert result.total_opts == 8
        assert result.qualifier_counts.get("auth", 0) == 8
        assert result.source_breakdown["domain_raw"] == 8
        assert result.source_breakdown["intent_label"] == 0
        assert result.source_breakdown["tf_idf"] == 0
        assert result.per_qualifier_sources["auth"]["domain_raw"] == 8

    @pytest.mark.asyncio
    async def test_source_2_intent_label_fallback(self, db):
        """When domain_raw lacks a qualifier, intent_label vocabulary matches."""
        from app.services.taxonomy.sub_domain_readiness import compute_qualifier_cascade

        opts = [
            {"domain_raw": "backend", "intent_label": "set up oauth login flow"},
            {"domain_raw": "backend", "intent_label": "add jwt tokens to backend"},
            {"domain_raw": "backend", "intent_label": "refresh token endpoint"},
        ]
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth", "jwt", "token", "login"]},
        )
        result = await compute_qualifier_cascade(db, domain)

        assert result.total_opts == 3
        assert result.qualifier_counts.get("auth", 0) >= 2
        assert result.source_breakdown["intent_label"] >= 2
        assert result.source_breakdown["domain_raw"] == 0

    @pytest.mark.asyncio
    async def test_source_3_tf_idf_fallback(self, db):
        """When neither domain_raw nor intent_label match, TF-IDF catches it."""
        from app.services.taxonomy.sub_domain_readiness import compute_qualifier_cascade

        opts = [
            {
                "domain_raw": "backend",
                "intent_label": "general task",
                "raw_prompt": "I need help building a kubernetes deployment with kubernetes pods",
            },
        ] * 6
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            signal_keywords=[["kubernetes", 1.2]],
        )
        result = await compute_qualifier_cascade(db, domain)

        assert result.total_opts == 6
        assert result.source_breakdown["tf_idf"] >= 3

    @pytest.mark.asyncio
    async def test_dominant_source_tiebreak(self, db):
        """When the same qualifier scores via multiple sources, dominant = highest count."""
        from app.services.taxonomy.sub_domain_readiness import compute_qualifier_cascade

        opts = [{"domain_raw": "backend: auth"}] * 5 + [
            {"domain_raw": "backend", "intent_label": "oauth token exchange"},
            {"domain_raw": "backend", "intent_label": "jwt login endpoint"},
        ]
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth", "jwt", "token", "login"]},
        )
        result = await compute_qualifier_cascade(db, domain)
        dominant = result.dominant_source_for("auth")
        assert dominant == "domain_raw"


# ---------------------------------------------------------------------------
# Sub-domain emergence
# ---------------------------------------------------------------------------


class TestSubDomainEmergence:
    @pytest.mark.asyncio
    async def test_empty_domain_is_inert(self, db):
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        domain = await _seed_domain_with_opts(db, "backend", opts=[])
        report = await compute_sub_domain_emergence(db, domain)

        assert report.tier == "inert"
        assert report.ready is False
        assert report.blocked_reason == "no_candidates"
        assert report.top_candidate is None
        assert report.gap_to_threshold is None
        assert report.min_member_count == SUB_DOMAIN_QUALIFIER_MIN_MEMBERS

    @pytest.mark.asyncio
    async def test_strong_qualifier_is_ready(self, db):
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth", "jwt", "login"]},
            cluster_count=2,
        )
        report = await compute_sub_domain_emergence(db, domain)

        assert report.ready is True
        assert report.tier == "ready"
        assert report.top_candidate is not None
        assert report.top_candidate.qualifier == "auth"
        assert report.top_candidate.dominant_source == "domain_raw"
        assert report.gap_to_threshold is not None and report.gap_to_threshold <= 0
        assert report.blocked_reason in (None, "none")

    @pytest.mark.asyncio
    async def test_close_but_below_threshold_is_warming(self, db):
        """8 of 16 opts match → consistency 50%, threshold ~0.54 → warming."""
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        opts = [{"domain_raw": "backend: auth"}] * 8 + [
            {"domain_raw": "backend"} for _ in range(8)
        ]
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth", "jwt"]},
            cluster_count=2,
        )
        report = await compute_sub_domain_emergence(db, domain)

        assert report.ready is False
        assert report.tier in ("warming", "inert")
        assert report.top_candidate is not None
        assert report.top_candidate.qualifier == "auth"
        assert report.gap_to_threshold is not None and report.gap_to_threshold > 0

    @pytest.mark.asyncio
    async def test_insufficient_members_blocks(self, db):
        """Top qualifier count < MIN_MEMBERS → blocked_reason='insufficient_members'."""
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        opts = [{"domain_raw": "backend: auth"}] * 3 + [{"domain_raw": "backend"}] * 3
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        report = await compute_sub_domain_emergence(db, domain)

        assert report.ready is False
        assert report.blocked_reason in ("insufficient_members", "below_threshold")

    @pytest.mark.asyncio
    async def test_adaptive_threshold_small_domain(self, db):
        """5-member domain → threshold = max(0.40, 0.60 - 0.004*5) = 0.58."""
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        opts = [{"domain_raw": "backend: auth"}] * 5
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        report = await compute_sub_domain_emergence(db, domain)

        expected = max(
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
            - SUB_DOMAIN_QUALIFIER_SCALE_RATE * 5,
        )
        assert report.threshold == pytest.approx(expected, abs=1e-6)
        assert "0.58" in report.threshold_formula or "0.580" in report.threshold_formula

    @pytest.mark.asyncio
    async def test_adaptive_threshold_large_domain_floors(self, db):
        """100-member domain → threshold floors at 0.40."""
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        opts = [{"domain_raw": "backend: auth"}] * 100
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        report = await compute_sub_domain_emergence(db, domain)
        assert report.threshold == pytest.approx(SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW)

    @pytest.mark.asyncio
    async def test_single_cluster_breadth_blocks_ready(self, db):
        """A qualifier concentrated in ONE cluster is blocked — needs >= 2."""
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        # Put all matching opts in one cluster (cluster_count=2 but only opts[0..N-1] go round-robin).
        # Construct so all auth opts land in cluster 0.
        domain = _make_domain(
            "backend",
            generated_qualifiers={"auth": ["oauth"]},
            age_hours=72.0,
        )
        db.add(domain)
        await db.flush()
        c0 = _make_cluster("c0", parent_id=domain.id, domain="backend")
        c1 = _make_cluster("c1", parent_id=domain.id, domain="backend")
        db.add_all([c0, c1])
        await db.flush()
        for _ in range(7):
            db.add(_make_opt(c0.id, domain="backend", domain_raw="backend: auth"))
        await db.flush()

        report = await compute_sub_domain_emergence(db, domain)
        # Even though consistency is 100%, breadth=1 disqualifies emergence.
        assert report.ready is False
        assert report.blocked_reason == "single_cluster"
        assert report.top_candidate is not None
        assert report.top_candidate.cluster_breadth == 1


# ---------------------------------------------------------------------------
# Domain stability
# ---------------------------------------------------------------------------


class TestDomainStability:
    @pytest.mark.asyncio
    async def test_general_domain_is_healthy_and_protected(self, db):
        from app.services.taxonomy.sub_domain_readiness import compute_domain_stability

        opts = [{"domain_raw": "general"}] * 6
        domain = await _seed_domain_with_opts(db, "general", opts)
        report = await compute_domain_stability(db, domain)

        assert report.guards.general_protected is True
        assert report.would_dissolve is False
        assert report.tier in ("healthy", "guarded")

    @pytest.mark.asyncio
    async def test_eroded_domain_is_critical(self, db):
        """<15% consistency, no sub-domains, age >= 48h, members <= 5 → critical."""
        from app.services.taxonomy.sub_domain_readiness import compute_domain_stability

        # 5 members, all with wrong domain_raw → consistency 0
        opts = [{"domain_raw": "frontend"}] * 5
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            age_hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 10,
            cluster_count=2,
        )
        # Clusters have member_count=5 each but opt count is what matters for
        # consistency; member_count for ceiling check comes from child cluster count.
        report = await compute_domain_stability(db, domain)

        assert report.tier == "critical"
        assert report.guards.consistency_above_floor is False
        assert report.guards.general_protected is False
        assert report.guards.has_sub_domain_anchor is False
        assert report.would_dissolve is True
        assert report.consistency < DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR

    @pytest.mark.asyncio
    async def test_young_domain_is_guarded_not_critical(self, db):
        """Young domain with bad consistency but age < threshold → guarded, not would_dissolve."""
        from app.services.taxonomy.sub_domain_readiness import compute_domain_stability

        opts = [{"domain_raw": "frontend"}] * 5
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            age_hours=2.0,
            cluster_count=2,
        )
        report = await compute_domain_stability(db, domain)

        assert report.would_dissolve is False
        assert report.guards.age_eligible is False

    @pytest.mark.asyncio
    async def test_sub_domain_anchor_blocks_dissolution(self, db):
        from app.services.taxonomy.sub_domain_readiness import compute_domain_stability

        opts = [{"domain_raw": "frontend"}] * 5
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            age_hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 10,
            cluster_count=2,
        )
        # Attach a child sub-domain node
        sub = _make_domain("auth", parent_id=domain.id)
        db.add(sub)
        await db.flush()

        report = await compute_domain_stability(db, domain)

        assert report.guards.has_sub_domain_anchor is True
        assert report.sub_domain_count == 1
        assert report.would_dissolve is False

    @pytest.mark.asyncio
    async def test_member_ceiling_protects(self, db):
        """Large domains (> ceiling) aren't dissolved on low consistency alone."""
        from app.services.taxonomy.sub_domain_readiness import compute_domain_stability

        # Plenty of opts with wrong domain_raw; many child clusters for ceiling
        opts = [{"domain_raw": "frontend"}] * 20
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            age_hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS + 10,
            cluster_count=DOMAIN_DISSOLUTION_MEMBER_CEILING + 3,
        )
        report = await compute_domain_stability(db, domain)

        assert report.guards.above_member_ceiling is True
        assert report.would_dissolve is False


# ---------------------------------------------------------------------------
# Unified readiness report
# ---------------------------------------------------------------------------


class TestDomainReadinessReport:
    @pytest.mark.asyncio
    async def test_report_includes_both_sections(self, db):
        from app.services.taxonomy.sub_domain_readiness import compute_domain_readiness

        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        report = await compute_domain_readiness(db, domain)

        assert report.domain_id == domain.id
        assert report.domain_label == "backend"
        assert report.stability is not None
        assert report.emergence is not None
        assert report.emergence.ready is True
        assert report.computed_at is not None


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class TestReadinessCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_snapshot(self, db):
        from app.services.taxonomy import sub_domain_readiness as srv

        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        srv.clear_cache()

        r1 = await srv.compute_domain_readiness(db, domain)
        r2 = await srv.compute_domain_readiness(db, domain)
        # Cached hit ⇒ identical computed_at timestamp
        assert r1.computed_at == r2.computed_at

    @pytest.mark.asyncio
    async def test_fresh_bypasses_cache(self, db):
        from app.services.taxonomy import sub_domain_readiness as srv

        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        srv.clear_cache()

        r1 = await srv.compute_domain_readiness(db, domain)
        r2 = await srv.compute_domain_readiness(db, domain, fresh=True)
        assert r2.computed_at >= r1.computed_at

    @pytest.mark.asyncio
    async def test_cache_keyed_by_member_count(self, db):
        """Adding an optimization changes the cache key, forcing recomputation."""
        from app.services.taxonomy import sub_domain_readiness as srv

        opts = [{"domain_raw": "backend: auth"}] * 8
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            opts,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        srv.clear_cache()

        r1 = await srv.compute_domain_readiness(db, domain)
        # Add one more opt → new count → new cache key
        from sqlalchemy import select
        clusters_q = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == domain.id)
        )
        first_cluster = next(iter(clusters_q.scalars()))
        db.add(_make_opt(first_cluster.id, domain="backend", domain_raw="backend: auth"))
        await db.flush()

        r2 = await srv.compute_domain_readiness(db, domain)
        assert r2.emergence.total_opts == r1.emergence.total_opts + 1


# ---------------------------------------------------------------------------
# Batch + parity
# ---------------------------------------------------------------------------


class TestBatchAndParity:
    @pytest.mark.asyncio
    async def test_all_domains_returned_sorted(self, db):
        from app.services.taxonomy.sub_domain_readiness import (
            compute_all_domain_readiness,
        )

        await _seed_domain_with_opts(
            db,
            "backend",
            [{"domain_raw": "backend: auth"}] * 8,
            generated_qualifiers={"auth": ["oauth"]},
            cluster_count=2,
        )
        await _seed_domain_with_opts(db, "frontend", opts=[])

        reports = await compute_all_domain_readiness(db)
        labels = {r.domain_label for r in reports}
        assert labels == {"backend", "frontend"}

    @pytest.mark.asyncio
    async def test_engine_promotion_matches_primitive_ready(self, db, mock_provider):
        """End-to-end parity: if the primitive says a qualifier is READY,
        ``engine._propose_sub_domains()`` must promote the same qualifier.

        Guards against future drift — the cascade primitive and the engine
        mutation path must see the same world and reach the same decision.
        """
        from unittest.mock import AsyncMock, patch

        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.sub_domain_readiness import (
            compute_sub_domain_emergence,
        )

        # Seed a domain with two clusters and a dominant "auth" qualifier
        # via Source 1 (domain_raw).  Eight opts — above MIN_MEMBERS, easily
        # above the adaptive threshold, spread across both clusters so the
        # cluster_breadth >= 2 gate passes.
        domain = await _seed_domain_with_opts(
            db,
            "backend",
            [{"domain_raw": "backend: auth"}] * 8,
            generated_qualifiers={"auth": ["oauth", "jwt"]},
            cluster_count=2,
            age_hours=72.0,
        )
        await db.commit()

        # Primitive reports ready
        emergence = await compute_sub_domain_emergence(db, domain)
        assert emergence.ready is True, emergence.blocked_reason
        assert emergence.top_candidate is not None
        assert emergence.top_candidate.qualifier == "auth"

        # Engine promotes the same qualifier
        engine = TaxonomyEngine(
            embedding_service=AsyncMock(), provider=mock_provider,
        )
        with patch(
            "app.services.taxonomy.labeling.generate_qualifier_vocabulary",
            AsyncMock(return_value={"auth": ["oauth", "jwt"]}),
        ):
            created = await engine._propose_sub_domains(db)
        assert "auth" in created, (
            f"primitive reported ready but engine created {created!r}"
        )


# ---------------------------------------------------------------------------
# R4: shared per-opt sub-domain matcher primitive
# ---------------------------------------------------------------------------


class TestMatchOptToSubDomainVocab:
    """R4 (audit 2026-04-27): pure-function unit tests for the shared
    per-opt sub-domain matcher.

    The function under test is to be added to ``sub_domain_readiness.py``
    as ``match_opt_to_sub_domain_vocab`` returning a
    ``SubDomainMatchResult`` dataclass.  It mirrors the v0.4.7 inline
    matching cascade from ``engine._reevaluate_sub_domains`` verbatim
    (Source 1 / 2 / 2b legacy / 3) so the engine can delegate to it
    without behavior drift.

    These tests fail today with ``ImportError`` — the function and
    dataclass do not yet exist.  GREEN adds them and refactors the
    engine to consume the primitive.

    Acceptance criteria: AC-R4-1.
    """

    def test_source_1_exact_label_match(self):
        """Source 1: ``domain_raw`` qualifier equals the sub-domain label
        (the v0.4.6 exact-equality clause that the v0.4.7 fix kept as a
        fast-path).  All vocab sets are empty so a hit through this path
        proves the label-identity branch is wired.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            SubDomainMatchResult,
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw="backend: embedding-health",
            intent_label=None,
            raw_prompt=None,
            sub_qualifier="embedding-health",
            sub_vocab_groups=set(),
            sub_vocab_terms=set(),
            sub_vocab_tokens=set(),
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert isinstance(result, SubDomainMatchResult)
        assert result.matched is True
        assert result.source == "domain_raw"
        assert result.matched_value == "embedding-health"

    def test_source_1_vocab_group_match(self):
        """Source 1: ``domain_raw`` qualifier hits a vocab group name
        (the v0.4.7 fix's primary path — sub-domain creation aggregates
        multiple vocab groups so a child whose qualifier IS a group name
        is topically consistent).
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw="backend: observability",
            intent_label=None,
            raw_prompt=None,
            sub_qualifier="instrumentation",
            sub_vocab_groups={"observability"},
            sub_vocab_terms=set(),
            sub_vocab_tokens=set(),
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert result.matched is True
        assert result.source == "domain_raw"

    def test_source_1_vocab_term_match(self):
        """Source 1: ``domain_raw`` qualifier hits a flattened vocab
        term (any leaf value across all groups).  Term-level hits are
        narrower than group hits but still legitimate sub-domain
        membership signals.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw="backend: tracing",
            intent_label=None,
            raw_prompt=None,
            sub_qualifier="instrumentation",
            sub_vocab_groups=set(),
            sub_vocab_terms={"tracing", "monitoring"},
            sub_vocab_tokens=set(),
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert result.matched is True
        assert result.source == "domain_raw"

    def test_source_1_token_overlap_match(self):
        """Source 1: ``domain_raw`` qualifier shares a tokenized chunk
        (≥4 chars after splitting on space/hyphen/underscore) with the
        sub-domain's vocab tokens.  Lets ``backend: cache-eviction``
        match a sub-domain whose tokens include ``cache``.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw="backend: cache-eviction",
            intent_label=None,
            raw_prompt=None,
            sub_qualifier="instrumentation",
            sub_vocab_groups=set(),
            sub_vocab_terms=set(),
            sub_vocab_tokens={"cache"},
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert result.matched is True
        assert result.source == "domain_raw"

    def test_source_2_intent_label_token_match(self):
        """Source 2: ``intent_label`` tokens intersect ``sub_vocab_tokens``
        — the modern replacement for the legacy substring scan.  Lets
        ``"Cache Eviction Policy Audit"`` match a sub-domain whose tokens
        contain ``cache``.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw=None,
            intent_label="Cache Eviction Policy Audit",
            raw_prompt=None,
            sub_qualifier="instrumentation",
            sub_vocab_groups=set(),
            sub_vocab_terms=set(),
            sub_vocab_tokens={"cache"},
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert result.matched is True
        assert result.source == "intent_label"

    def test_source_3_dynamic_keyword_match(self):
        """Source 3: TF-IDF dynamic keyword whose normalised form equals
        the sub-domain qualifier and meets the per-weight min-hits gate.
        At weight 0.9 the gate is 1 hit, so a single occurrence in the
        ``raw_prompt`` is sufficient.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw=None,
            intent_label=None,
            raw_prompt=(
                "audit the asyncio race condition in our handler with "
                "asyncio.gather"
            ),
            sub_qualifier="asyncio",
            sub_vocab_groups=set(),
            sub_vocab_terms=set(),
            sub_vocab_tokens=set(),
            sub_keywords_legacy=[],
            dynamic_keywords=[("asyncio", 0.9)],
        )

        assert result.matched is True
        assert result.source == "tf_idf"
        assert result.matched_value == "asyncio"

    def test_all_sources_miss_returns_unmatched_with_reason(self):
        """All four sources miss: ``domain_raw`` qualifier doesn't equal
        the sub-domain label, isn't in any vocab set, has no token
        overlap; ``intent_label`` shares no tokens with vocab tokens
        (and ``sub_keywords_legacy`` is empty so 2b is vacuous);
        ``raw_prompt`` carries no dynamic-keyword hit.  Expect
        ``matched=False``, ``source=None``, and a non-empty diagnostic
        ``reason`` so R5's forensic telemetry can render it.
        """
        from app.services.taxonomy.sub_domain_readiness import (
            match_opt_to_sub_domain_vocab,
        )

        result = match_opt_to_sub_domain_vocab(
            domain_raw="frontend: forms",
            intent_label="Validate Login Form",
            raw_prompt="this is a frontend prompt",
            sub_qualifier="instrumentation",
            sub_vocab_groups=set(),
            sub_vocab_terms=set(),
            sub_vocab_tokens=set(),
            sub_keywords_legacy=[],
            dynamic_keywords=[],
        )

        assert result.matched is False
        assert result.source is None
        assert isinstance(result.reason, str) and result.reason, (
            f"reason must be a non-empty string when matched=False; "
            f"got: {result.reason!r}"
        )
