"""RED tests for unified qualifier view (AC-1..AC-6) + R7 hygiene.

Pinned by spec §3.6 of
docs/superpowers/specs/2026-06-12-v0.4.38-sub-domain-telemetry-design.md.
"""

from __future__ import annotations

import pytest

from app.models import Optimization, PromptCluster
from app.services.taxonomy.sub_domain_readiness import (
    CascadeResult,  # noqa: F401 — contract probe; presence verified by import.
    UnifiedQualifierView,
    compute_qualifier_cascade,
    compute_sub_domain_emergence,
    compute_unified_qualifier_view,
)
from app.utils.text_cleanup import normalize_sub_domain_label

pytestmark = pytest.mark.asyncio


async def _mk_domain(db, label: str = "backend") -> PromptCluster:
    node = PromptCluster(
        id=f"dom-{label}",
        label=label,
        state="domain",
        centroid_embedding=None,
    )
    db.add(node)
    await db.flush()
    return node


async def _mk_child_cluster(db, parent: PromptCluster, suffix: str) -> PromptCluster:
    node = PromptCluster(
        id=f"c-{parent.id}-{suffix}",
        label=f"child-{suffix}",
        state="active",
        parent_id=parent.id,
        centroid_embedding=None,
    )
    db.add(node)
    await db.flush()
    return node


async def _mk_opt(
    db,
    cluster: PromptCluster,
    *,
    domain_raw: str | None = None,
    intent_label: str | None = None,
    raw_prompt: str = "x",
) -> Optimization:
    opt = Optimization(
        raw_prompt=raw_prompt,
        optimized_prompt=raw_prompt,
        cluster_id=cluster.id,
        domain_raw=domain_raw,
        intent_label=intent_label,
    )
    db.add(opt)
    await db.flush()
    return opt


async def test_ac1_vocab_present_view_equals_cascade(db):
    """AC-1: vocab-present fixture → view.qualifier_counts identical to cascade; source='cascade'."""
    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {
        "generated_qualifiers": {"observability": ["tracing", "metrics", "logs"]},
    }
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    await _mk_opt(db, c1, domain_raw="backend: observability")
    await _mk_opt(db, c2, domain_raw="backend: observability")

    cascade = await compute_qualifier_cascade(db, domain)
    view = await compute_unified_qualifier_view(db, domain, cascade=cascade)

    assert view.source == "cascade"
    assert view.qualifier_counts == cascade.qualifier_counts
    assert view.total_opts == cascade.total_opts


async def test_ac2_literal_fallback_when_cascade_admits_nothing(db):
    """AC-2: empty vocab + no TF-IDF admits → literal_fallback; counts equal normalized literal tally."""
    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {}  # no vocab, no signal_keywords
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    await _mk_opt(db, c1, domain_raw="backend: embedding")
    await _mk_opt(db, c2, domain_raw="backend: embedding-correctness")

    view = await compute_unified_qualifier_view(db, domain)

    assert view.source == "literal_fallback"
    # Normalised under normalize_sub_domain_label
    expected = {
        normalize_sub_domain_label("embedding"): 1,
        normalize_sub_domain_label("embedding-correctness"): 1,
    }
    assert view.qualifier_counts == expected


async def test_ac2_trigger_precision_vocab_empty_but_tfidf_admits(db):
    """AC-2 trigger precision: vocab empty but signal_keywords admit one qualifier → still 'cascade'."""
    domain = await _mk_domain(db, "backend")
    # No generated_qualifiers; signal_keywords supplies the qualifier vocab
    domain.cluster_metadata = {
        "signal_keywords": [["embedding", 0.9]],
    }
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    await _mk_opt(db, c1, domain_raw="backend: embedding")
    await _mk_opt(db, c2, domain_raw="backend: embedding", raw_prompt="embedding cache invalidation")

    view = await compute_unified_qualifier_view(db, domain)

    assert view.source == "cascade", (
        "TF-IDF admitted qualifier — cascade is useful even with empty generated_qualifiers"
    )


async def test_ac3_residue_isolated_from_qualifier_counts(db):
    """AC-3: vocab-unknown literal lands in residue, not qualifier_counts; literal_members records both variants.

    Dependency note: this test relies on Source-2 (intent_label) admission to
    land the literals ``embedding`` and ``embedding-correctness`` under the
    vocab GROUP KEY ``"embeddings"``. Source 1 admits only when the literal
    itself matches a vocab GROUP KEY (here {``"embeddings"``}) — the literal
    ``embedding`` (singular) is NOT in that set, so Source 1 misses. Source 2
    (``DomainSignalLoader.find_best_qualifier``) scans each group's KEYWORD
    LIST: keyword ``"embedding"`` lives inside the ``"embeddings"`` group's
    keyword list, so an intent_label containing that keyword maps the opt to
    group ``"embeddings"`` (best-hits ≥ ``SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS=1``).
    The C1-GREEN.1 forensic block records the parsed literal under that group
    key whenever a qualifier is recorded and ``domain_raw`` parses (no Source 1
    gate on the recording), so ``literal_members["embeddings"]`` gets both
    ``embedding`` and ``embedding-correctness`` populated.

    Without ``intent_label``, Source 2 would skip, the cascade would admit
    nothing, ``view.source`` would flip to ``"literal_fallback"``, and the
    test's ``view.source == "cascade"`` assertion would FAIL.
    """
    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {
        "generated_qualifiers": {"embeddings": ["embedding", "embedding-correctness"]},
    }
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    c3 = await _mk_child_cluster(db, domain, "3")
    # c1/c2 carry intent_label so Source 2 admits via vocab group "embeddings".
    await _mk_opt(
        db, c1,
        domain_raw="backend: embedding",
        intent_label="embedding work",
    )
    await _mk_opt(
        db, c2,
        domain_raw="backend: embedding-correctness",
        intent_label="embedding work",
    )
    # c3 has no intent_label — Source 1/2/3 all miss; literal hits residue.
    await _mk_opt(db, c3, domain_raw="backend: unknownqualifier")

    view = await compute_unified_qualifier_view(db, domain)

    # Source 2 admitted "embeddings" for c1+c2 → cascade non-empty → "cascade".
    assert view.source == "cascade"
    # Cascade group is recorded with count=2 (c1 + c2).
    assert "embeddings" in view.qualifier_counts
    assert view.qualifier_counts["embeddings"] == 2
    # Residue carries the vocab-unknown literal — keyed by normalized label.
    assert "unknownqualifier" in view.residue_counts
    assert view.residue_counts["unknownqualifier"] == 1
    assert "unknownqualifier" not in view.qualifier_counts
    # Literal members records both raw variants under the GROUP KEY because
    # Source 2 recorded "embeddings" for each opt while domain_raw parsed to
    # the respective literal.
    assert "embedding" in view.literal_members.get("embeddings", {})
    assert "embedding-correctness" in view.literal_members.get("embeddings", {})


async def test_ac4_headline_parity_repro_group_aggregation(db):
    """AC-4 (headline): group aggregates to ≈0.45 consistency but every literal ≤0.25.

    Pins the cycle-15→17 regression: readiness sees the group above threshold AND
    rebuild dry-run proposes it at 0.30 / 0.35 / 0.38 thresholds.

    Per spec §5a — default threshold at N=40 is `max(0.40, 0.60 − 0.004*40) = 0.44`.
    A 0.45-group clears it. N=40 below.

    How the cascade aggregates literals into the ``embeddings`` group:
    Source 1 (parse_domain) on ``backend: embedding`` looks up the literal
    ``embedding`` against ``known_qualifiers`` = ``generated_qualifiers.keys()``
    (= {"embeddings"}) PLUS lowercased ``signal_keywords`` lemmas. The literal
    ``embedding`` is NOT in either set, so Source 1 misses. Source 2 then runs
    ``DomainSignalLoader.find_best_qualifier(intent_lower, generated_qualifiers)``
    which scans each group's KEYWORD list — ``embedding`` IS in
    ``generated_qualifiers["embeddings"]``, so an intent_label containing the
    literal aggregates into the ``embeddings`` group with `dominant_source="intent_label"`.
    Fixture sets ``intent_label`` accordingly. ``find_best_qualifier`` requires
    hits >= ``SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS=1`` per ``_constants.py``.
    """
    from app.services.taxonomy.engine import TaxonomyEngine

    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {
        "generated_qualifiers": {"embeddings": ["embedding", "embedding-correctness"]},
    }
    # Three clusters: two carry the qualifier (breadth=2 satisfies
    # SUB_DOMAIN_MIN_CLUSTER_BREADTH), one is noise.
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    c3 = await _mk_child_cluster(db, domain, "3")
    for _ in range(9):
        await _mk_opt(
            db, c1,
            domain_raw="backend: embedding",
            intent_label="embedding pipeline build",
        )
    for _ in range(9):
        await _mk_opt(
            db, c2,
            domain_raw="backend: embedding-correctness",
            intent_label="embedding-correctness audit",
        )
    for i in range(22):
        await _mk_opt(
            db, c3,
            domain_raw=f"backend: noise-{i}",
            intent_label=f"noise topic {i}",
        )

    report = await compute_sub_domain_emergence(db, domain)
    assert report.top_candidate is not None
    assert report.top_candidate.qualifier == "embeddings"
    assert abs(report.top_candidate.consistency - 0.45) < 0.01

    # rebuild dry-run at the default threshold AND at 0.30 / 0.35 / 0.38 proposes "embeddings"
    engine = TaxonomyEngine()
    for override in (None, 0.30, 0.35, 0.38):
        result = await engine.rebuild_sub_domains(
            db, domain.id,
            min_consistency_override=override,
            dry_run=True,
        )
        assert "embeddings" in result["proposed"], (
            f"override={override} should propose 'embeddings' "
            f"(pre-change code returns proposed=[])"
        )
        assert result["qualifier_source"] == "cascade"


async def test_ac5_empty_vocab_rebuild_falls_back_to_literals(db):
    """AC-5 recovery regression: empty-vocab fixture → rebuild proposes from literals."""
    from app.services.taxonomy.engine import TaxonomyEngine

    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {}
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    for _ in range(6):
        await _mk_opt(db, c1, domain_raw="backend: embedding")
    for _ in range(6):
        await _mk_opt(db, c2, domain_raw="backend: embedding")

    engine = TaxonomyEngine()
    result = await engine.rebuild_sub_domains(
        db, domain.id, min_consistency_override=0.40, dry_run=True,
    )
    assert "embedding" in result["proposed"]
    assert result["qualifier_source"] == "literal_fallback"


async def test_ac6_residue_recovery_vocab_scoped_away(db):
    """AC-6: vocab present but scoped away from dominant literal → rebuild proposes via residue.

    Fixture must satisfy TWO structural constraints simultaneously:
      1. ``view.source == "cascade"`` (so the residue-merge branch executes
         inside ``_rebuild_sub_domains_impl`` — only the cascade branch merges
         residue; the literal_fallback branch synthesises counts from residue
         directly and never proposes a separate ``literal_residue`` source).
      2. The dominant out-of-vocab literal has ``cluster_breadth >= 2`` (gate
         at ``engine.py:3292`` with ``SUB_DOMAIN_MIN_CLUSTER_BREADTH=2``);
         otherwise it survives consistency but is dropped on breadth and
         ``proposed`` stays empty.

    To satisfy (1), c1 carries ``intent_label="tracing telemetry"`` so Source 2
    admits the vocab group ``"observability"`` (keyword ``"tracing"`` in the
    group's keyword list). To satisfy (2), the dominant literal
    ``"rate-limiting"`` is split across TWO clusters (c2 + c3) so its breadth
    reaches 2 against the gate.

    Trace:
      * c1 (1 opt, ``domain_raw="backend: tracing"``, intent_label set):
        Source 1 misses (``tracing`` not a vocab GROUP KEY); Source 2 admits
        ``"observability"`` via keyword hit. Cascade records {"observability": 1}.
      * c2 (3 opts, ``domain_raw="backend: rate-limiting"``, no intent_label):
        all sources miss. Residue records ``"rate-limiting": 3``, cluster_ids={c2}.
      * c3 (3 opts, ``domain_raw="backend: rate-limiting"``, no intent_label):
        residue grows to ``"rate-limiting": 6``, cluster_ids={c2, c3}.

    ``view.source = "cascade"``. Rebuild merges residue → ``qualifier_counts =
    {"observability": 1, "rate-limiting": 6}``. ``total_opts = 7``. Threshold
    formula at N=7 is ``max(0.40, 0.60 - 0.004*7) = 0.572``. The test uses
    ``min_consistency_override=0.40`` so threshold = 0.40.
      * "rate-limiting": 6/7 = 0.857 >= 0.40; cluster_breadth = 2 >= 2 → proposed.
      * "observability": 1/7 = 0.143 < 0.40 → dropped (below threshold).
    """
    from app.services.taxonomy.engine import TaxonomyEngine

    domain = await _mk_domain(db, "backend")
    # Vocab covers a different topic; the dominant literal is NOT in vocab.
    domain.cluster_metadata = {
        "generated_qualifiers": {"observability": ["tracing", "metrics"]},
    }
    c1 = await _mk_child_cluster(db, domain, "1")
    c2 = await _mk_child_cluster(db, domain, "2")
    c3 = await _mk_child_cluster(db, domain, "3")
    # c1: 1 opt with intent_label so Source 2 admits "observability" → cascade non-empty.
    await _mk_opt(
        db, c1,
        domain_raw="backend: tracing",
        intent_label="tracing telemetry",
    )
    # c2 + c3: 3 opts each carrying the dominant out-of-vocab literal so its
    # cluster_breadth = 2 satisfies SUB_DOMAIN_MIN_CLUSTER_BREADTH.
    for _ in range(3):
        await _mk_opt(db, c2, domain_raw="backend: rate-limiting")
    for _ in range(3):
        await _mk_opt(db, c3, domain_raw="backend: rate-limiting")

    # View-level sanity checks (run separately from the rebuild call so the
    # test pins both the view shape AND the rebuild behaviour).
    view = await compute_unified_qualifier_view(db, domain)
    # cascade branch — needed so rebuild executes the residue merge.
    assert view.source == "cascade"
    # The dominant out-of-vocab literal lives in residue with count=6.
    assert "rate-limiting" in view.residue_counts
    assert view.residue_counts["rate-limiting"] == 6

    engine = TaxonomyEngine()
    result = await engine.rebuild_sub_domains(
        db, domain.id, min_consistency_override=0.40, dry_run=True,
    )
    # Residue qualifier crosses the threshold + breadth gate and is proposed.
    assert "rate-limiting" in result["proposed"]
    # The cascade-admitted group does NOT propose — its 1/7 ≈ 0.143
    # consistency is below the override floor of 0.40 AND its cluster_breadth
    # is 1 (only c1 carries the intent_label admit) below
    # SUB_DOMAIN_MIN_CLUSTER_BREADTH=2. Both gates would individually exclude
    # it; ensure rebuild's `proposed` reflects that.
    assert "observability" not in result["proposed"]
    # The unified view's source surfaces on the response.
    assert result["qualifier_source"] == "cascade"
    # event/proposal source breakdown is checked in the lifecycle suite (AC-7);
    # spot-check the response side here.
    # Note: event-level proposal_sources["rate-limiting"] == "literal_residue"
    # is asserted inside the C1-RED.2 lifecycle suite
    # (TestRebuildEventAdditivity.test_ac7_residue_proposal_source_labelled_literal_residue).


async def test_ac1_view_handles_no_cascade_kwarg(db):
    """Compute view without passing a precomputed cascade."""
    domain = await _mk_domain(db, "backend")
    c1 = await _mk_child_cluster(db, domain, "1")
    await _mk_opt(db, c1, domain_raw="backend: embedding")
    view = await compute_unified_qualifier_view(db, domain)
    assert isinstance(view, UnifiedQualifierView)
    assert view.total_opts == 1


async def test_underscore_literal_residue_collision_normalizes_onto_vocab_group(db):
    """Spec §3.2 nuance: residue keys use full normalize_sub_domain_label.

    A literal like ``embedding_health`` (underscore) misses Source 1 against
    vocab group ``embedding-health`` (Source 1 only space→hyphen, not
    underscore), so it must normalise into the same residue/vocab key on the
    residue side so rebuild's merge is by-sum.
    """
    domain = await _mk_domain(db, "backend")
    domain.cluster_metadata = {
        "generated_qualifiers": {"embedding-health": ["health", "monitoring"]},
    }
    c1 = await _mk_child_cluster(db, domain, "1")
    # underscore variant — Source 1 lookup is space→hyphen only, misses vocab.
    await _mk_opt(db, c1, domain_raw="backend: embedding_health")

    view = await compute_unified_qualifier_view(db, domain)
    # The literal normalises to 'embedding-health' on the residue side.
    assert "embedding-health" in view.residue_counts or "embedding-health" in view.qualifier_counts
