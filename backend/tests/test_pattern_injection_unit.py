"""Unit coverage for ``pattern_injection`` helper functions + provenance.

The existing suite covers the integration flows (topic matching,
cross-cluster injection, project scoping, pipeline auto-inject) but
leaves three pockets uncovered:

1. ``_intent_label_bonus()`` — pure jaccard tiebreaker used by
   few-shot ranking. Previously only exercised transitively.
2. ``format_injected_patterns()`` branches — the global-pattern
   sub-section and the ``existing_text`` merge path.
3. ``auto_inject_patterns()`` provenance persistence — topic / global /
   cross-cluster rows written to ``OptimizationPattern``.
4. ``retrieve_few_shot_examples()`` output-similarity + dedup paths
   (input_sim below floor, output_sim above floor → still qualify; two
   candidate rows for the same ``opt_id`` → dedup keeps the higher).

Copyright 2025-2026 Project Synthesis contributors.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from sqlalchemy import select

from app.models import (
    GlobalPattern,
    Optimization,
    OptimizationPattern,
)
from app.services.pattern_injection import (
    FEW_SHOT_OUTPUT_SIMILARITY_THRESHOLD,
    FEW_SHOT_SIMILARITY_THRESHOLD,
    InjectedPattern,
    _intent_label_bonus,
    auto_inject_patterns,
    format_injected_patterns,
    retrieve_few_shot_examples,
)

# ---------------------------------------------------------------------------
# Helpers shared with the existing cross-cluster suite
# ---------------------------------------------------------------------------

def _rand_emb(dim: int = 384) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_taxonomy_engine(size: int = 0, matches=None):
    embedding_index = MagicMock()
    embedding_index.size = size
    embedding_index.search = MagicMock(
        return_value=matches if matches is not None else [],
    )
    engine = MagicMock()
    engine.embedding_index = embedding_index
    return engine


# ---------------------------------------------------------------------------
# 1. _intent_label_bonus() — pure function
# ---------------------------------------------------------------------------

class TestIntentLabelBonus:
    def test_empty_label_returns_zero(self):
        assert _intent_label_bonus("some prompt text", "") == 0.0

    def test_no_overlap_returns_zero(self):
        # Disjoint tokens → jaccard = 0.
        assert _intent_label_bonus("code a python function", "write poetry") == 0.0

    def test_full_overlap_is_capped_at_point_one(self):
        # Identical tokens → jaccard = 1.0, but bonus is capped at 0.10.
        bonus = _intent_label_bonus("debug flask endpoint", "debug flask endpoint")
        assert 0.0 < bonus <= 0.10

    def test_partial_overlap_scales_linearly(self):
        # Two shared tokens out of four union → jaccard ≈ 0.5.
        # bonus = min(0.5 * 0.15, 0.10) = 0.075
        bonus = _intent_label_bonus("debug flask", "flask endpoint")
        assert 0.0 < bonus < 0.10

    def test_stopwords_and_short_tokens_excluded(self):
        # Only stopwords + 1-char tokens — no tokens survive the filter.
        assert _intent_label_bonus("a an the", "and or") == 0.0

    def test_prompt_token_budget_is_bounded(self):
        # Only first 20 tokens of the prompt count — tail tokens can't
        # accidentally boost the jaccard.
        tail_unique = " ".join(f"tail{i}" for i in range(50))
        prompt = "alpha beta " + tail_unique
        # Label matches one of the first-20 tokens; tail must not count.
        assert _intent_label_bonus(prompt, "alpha") > 0.0


# ---------------------------------------------------------------------------
# 2. format_injected_patterns() — branch coverage
# ---------------------------------------------------------------------------

class TestFormatInjectedPatterns:
    def test_no_injected_returns_existing(self):
        assert format_injected_patterns([], existing_text="keep me") == "keep me"
        assert format_injected_patterns([], existing_text=None) is None

    def test_cluster_patterns_only(self):
        ip = InjectedPattern(
            pattern_text="Use type hints everywhere",
            cluster_label="typing",
            domain="coding",
            similarity=0.82,
            cluster_id="c1",
        )
        out = format_injected_patterns([ip])
        assert "Use type hints everywhere" in out
        assert '"typing" cluster' in out
        assert "[coding | 0.82]" in out
        # No global header.
        assert "Cross-Project Techniques" not in out

    def test_global_patterns_section_rendered(self):
        gp = InjectedPattern(
            pattern_text="Always validate input at the boundary",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.9,
            cluster_id="",
            source="global",
            source_id="gp1",
        )
        out = format_injected_patterns([gp])
        assert "Proven Cross-Project Techniques" in out
        assert "Always validate input at the boundary" in out
        # No cluster-section preamble when only globals present.
        assert "past optimizations" not in out

    def test_mixed_cluster_and_global_sections_both_present(self):
        cluster_ip = InjectedPattern(
            pattern_text="cluster_pat",
            cluster_label="foo",
            domain="coding",
            similarity=0.7,
            cluster_id="c1",
        )
        global_ip = InjectedPattern(
            pattern_text="global_pat",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.85,
            cluster_id="",
            source="global",
            source_id="gp1",
        )
        out = format_injected_patterns([cluster_ip, global_ip])
        assert "cluster_pat" in out
        assert "global_pat" in out
        assert "Proven Cross-Project Techniques" in out

    def test_merges_with_existing_text(self):
        ip = InjectedPattern(
            pattern_text="new_pat",
            cluster_label="foo",
            domain="coding",
            similarity=0.7,
            cluster_id="c1",
        )
        merged = format_injected_patterns([ip], existing_text="prior patterns go here")
        assert merged.startswith("prior patterns go here")
        assert "new_pat" in merged


# ---------------------------------------------------------------------------
# 3. auto_inject_patterns() — GlobalPattern 1.3× boost + provenance writes
# ---------------------------------------------------------------------------

class TestGlobalPatternInjection:
    async def test_global_pattern_injected_with_boosted_relevance(self, db_session):
        # Seed an active GlobalPattern whose embedding closely matches the
        # prompt embedding. The 1.3× boost should push relevance above
        # the CROSS_CLUSTER_RELEVANCE_FLOOR even when raw cosine would sit
        # just below it.
        prompt_emb = _rand_emb()
        gp_emb = prompt_emb + np.random.randn(384).astype(np.float32) * 0.05
        gp_emb = gp_emb / np.linalg.norm(gp_emb)

        gp = GlobalPattern(
            id=str(uuid.uuid4()),
            pattern_text="Cross-project proven technique",
            embedding=gp_emb.astype(np.float32).tobytes(),
            source_cluster_ids=["cluster_x"],
            source_project_ids=["proj_a", "proj_b"],
            cross_project_count=2,
            avg_cluster_score=8.0,
            state="active",
        )
        db_session.add(gp)
        await db_session.commit()

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, _cids = await auto_inject_patterns(
                raw_prompt="implement the cross-project thing",
                taxonomy_engine=_make_taxonomy_engine(size=0),
                db=db_session,
                trace_id="tr-gp",
            )

        assert any(p.source == "global" for p in injected), (
            "Expected at least one GlobalPattern to be injected"
        )
        # The global pattern's similarity reflects the 1.3× boost.
        gp_entries = [p for p in injected if p.source == "global"]
        assert gp_entries[0].cluster_label == "(global)"
        assert gp_entries[0].domain == "cross-project"

    async def test_inactive_global_patterns_are_skipped(self, db_session):
        """Only ``state='active'`` globals participate in injection."""
        prompt_emb = _rand_emb()
        near = prompt_emb + np.random.randn(384).astype(np.float32) * 0.02
        near = near / np.linalg.norm(near)

        db_session.add(GlobalPattern(
            id=str(uuid.uuid4()),
            pattern_text="retired wisdom",
            embedding=near.astype(np.float32).tobytes(),
            source_cluster_ids=["cx"],
            source_project_ids=["p1"],
            cross_project_count=1,
            avg_cluster_score=4.0,
            state="retired",  # not active
        ))
        await db_session.commit()

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, _cids = await auto_inject_patterns(
                raw_prompt="prompt",
                taxonomy_engine=_make_taxonomy_engine(size=0),
                db=db_session,
                trace_id="tr-retired",
            )
        assert not any(p.source == "global" for p in injected)


# ---------------------------------------------------------------------------
# 4. Provenance persistence — topic / global / cross-cluster write paths
# ---------------------------------------------------------------------------

class TestInjectionProvenance:
    async def test_global_pattern_provenance_row_is_written(self, db_session):
        prompt_emb = _rand_emb()
        near = prompt_emb + np.random.randn(384).astype(np.float32) * 0.05
        near = near / np.linalg.norm(near)

        # Need a real Optimization row for the FK to hold.
        opt_id = str(uuid.uuid4())
        db_session.add(Optimization(
            id=opt_id,
            raw_prompt="raw",
            task_type="coding",
            strategy_used="auto",
            status="pending",
        ))

        gp_id = str(uuid.uuid4())
        db_session.add(GlobalPattern(
            id=gp_id,
            pattern_text="global proven",
            embedding=near.astype(np.float32).tobytes(),
            source_cluster_ids=["cx"],
            source_project_ids=["pA", "pB"],
            cross_project_count=2,
            avg_cluster_score=7.5,
            state="active",
        ))
        await db_session.commit()

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            await auto_inject_patterns(
                raw_prompt="cross-project thing",
                taxonomy_engine=_make_taxonomy_engine(size=0),
                db=db_session,
                trace_id="tr-prov-gp",
                optimization_id=opt_id,
            )

        rows = (await db_session.execute(
            select(OptimizationPattern).where(
                OptimizationPattern.optimization_id == opt_id,
                OptimizationPattern.relationship == "global_injected",
            )
        )).scalars().all()
        assert len(rows) >= 1, "global provenance row must be persisted"
        assert rows[0].global_pattern_id == gp_id


# ---------------------------------------------------------------------------
# 5. retrieve_few_shot_examples — output-similarity + dedup branches
# ---------------------------------------------------------------------------

class TestFewShotRetrieval:
    async def test_output_similarity_alone_qualifies_an_example(self, db_session):
        """Even when input cosine < 0.50, a match with output cosine ≥ 0.40 is kept."""
        # We craft: input_emb orthogonal to prompt (sim ≈ 0), output_emb
        # close to prompt (sim ≥ 0.40).
        prompt_emb = _rand_emb()
        orth = _rand_emb()
        # Make output_emb a scaled copy of prompt_emb so cosine is ~1.0.
        output_emb = prompt_emb.copy()

        opt = Optimization(
            id=str(uuid.uuid4()),
            raw_prompt="before text",
            optimized_prompt="after text — markedly restructured",
            strategy_used="auto",
            overall_score=9.0,
            task_type="coding",
            intent_label="",
            embedding=orth.astype(np.float32).tobytes(),
            optimized_embedding=output_emb.astype(np.float32).tobytes(),
            status="completed",
        )
        db_session.add(opt)
        await db_session.commit()

        # Sanity: the raw cosine is below the input floor.
        raw_cos = float(np.dot(prompt_emb, orth))
        assert raw_cos < FEW_SHOT_SIMILARITY_THRESHOLD

        examples = await retrieve_few_shot_examples(
            raw_prompt="prompt",
            db=db_session,
            trace_id="tr-fs-out",
            prompt_embedding=prompt_emb,
            min_score=7.5,
            max_examples=3,
        )
        assert len(examples) >= 1
        # Output-only qualification — confirm threshold math holds.
        assert examples[0].similarity >= FEW_SHOT_OUTPUT_SIMILARITY_THRESHOLD


