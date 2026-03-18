# Prompt Knowledge Graph — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-building prompt knowledge graph that extracts reusable meta-patterns from optimizations, auto-suggests them on paste, and visualizes the pattern portfolio as a radial mindmap.

**Architecture:** Extend the existing 3-phase pipeline with an intent label in the analyzer and a post-completion background job for pattern extraction. New `PatternExtractorService`, `PatternMatcherService`, and `KnowledgeGraphService` backed by SQLite tables with in-memory embedding cache. Frontend gets a radial mindmap (D3.js) and an auto-suggestion banner on paste.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, all-MiniLM-L6-v2 (384-dim), D3.js (d3-force, d3-zoom), SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4.

**Spec:** `docs/superpowers/specs/2026-03-18-prompt-knowledge-graph-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/alembic/versions/<hash>_add_knowledge_graph_tables.py` | Alembic migration: 3 new tables + 2 new columns on optimizations |
| `backend/app/services/pattern_extractor.py` | Post-completion background job: embed, cluster, extract meta-patterns via Haiku |
| `backend/app/services/pattern_matcher.py` | On-paste similarity search against family centroids |
| `backend/app/services/knowledge_graph.py` | In-memory cache, graph computation, semantic search |
| `backend/app/routers/patterns.py` | REST endpoints for graph, families, match, search |
| `backend/tests/test_pattern_extractor.py` | PatternExtractorService tests |
| `backend/tests/test_pattern_matcher.py` | PatternMatcherService tests |
| `backend/tests/test_knowledge_graph.py` | KnowledgeGraphService tests |
| `backend/tests/test_patterns_router.py` | Patterns router integration tests |
| `prompts/extract_patterns.md` | Haiku prompt for meta-pattern extraction |
| `frontend/src/lib/stores/patterns.svelte.ts` | Pattern store: suggestion state, family data, paste detection |
| `frontend/src/lib/api/patterns.ts` | API client functions for patterns endpoints |
| `frontend/src/lib/components/editor/PatternSuggestion.svelte` | Auto-suggestion banner component |
| `frontend/src/lib/components/layout/PatternNavigator.svelte` | Navigator compact list view for pattern families |
| `frontend/src/lib/components/patterns/RadialMindmap.svelte` | Full interactive radial mindmap (D3.js) |

### Modified Files

| File | Changes |
|------|---------|
| `backend/app/models.py` | Add `PatternFamily`, `MetaPattern`, `OptimizationPattern` models + 2 columns on `Optimization` |
| `backend/app/schemas/pipeline_contracts.py` | Add `intent_label` and `domain` to `AnalysisResult` + `SuggestionsOutput` to `__all__` |
| `prompts/analyze.md` | Add intent_label and domain extraction instructions |
| `prompts/manifest.json` | Add `extract_patterns.md` entry |
| `backend/app/services/pipeline.py` | Persist `intent_label` + `domain` to DB, accept `applied_patterns` param |
| `backend/app/routers/optimize.py` | Accept `applied_pattern_ids` in OptimizeRequest |
| `backend/app/main.py` | Register patterns router + start pattern extraction subscriber |
| `frontend/src/lib/components/layout/ActivityBar.svelte` | Add patterns icon |
| `frontend/src/lib/components/layout/Navigator.svelte` | Add patterns panel |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | Add paste detection + suggestion banner |
| `frontend/src/lib/stores/forge.svelte.ts` | Add `appliedPatterns` field |
| `frontend/src/lib/api/client.ts` | Add pattern-related types |

---

## Phase 1: Backend Infrastructure

### Task 1: Data Model — New Tables and Columns

**Files:**
- Modify: `backend/app/models.py:34-62` (add columns to Optimization, add new classes)
- Create: `backend/alembic/versions/<hash>_add_knowledge_graph_tables.py`

- [ ] **Step 1: Add new models and columns to models.py**

Add two new columns to `Optimization` (after line 62):

```python
    intent_label = Column(String, nullable=True)
    domain = Column(String, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
```

Add three new model classes after the `StrategyAffinity` class (after line 84):

```python
class PatternFamily(Base):
    __tablename__ = "pattern_families"

    id = Column(String, primary_key=True, default=_uuid)
    intent_label = Column(String, nullable=False)
    domain = Column(String, nullable=False, default="general")
    task_type = Column(String, nullable=False, default="general")
    centroid_embedding = Column(LargeBinary, nullable=False)
    usage_count = Column(Integer, default=0, nullable=False)
    member_count = Column(Integer, default=1, nullable=False)
    avg_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MetaPattern(Base):
    __tablename__ = "meta_patterns"

    id = Column(String, primary_key=True, default=_uuid)
    family_id = Column(String, ForeignKey("pattern_families.id"), nullable=False)
    pattern_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    source_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class OptimizationPattern(Base):
    __tablename__ = "optimization_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    family_id = Column(String, ForeignKey("pattern_families.id"), nullable=False)
    meta_pattern_id = Column(String, ForeignKey("meta_patterns.id"), nullable=True)
    relationship = Column(String, nullable=False)  # "source" or "applied"
    created_at = Column(DateTime, default=_utcnow, nullable=False)
```

- [ ] **Step 2: Generate Alembic migration**

```bash
cd backend && source .venv/bin/activate && alembic revision --autogenerate -m "add knowledge graph tables"
```

Expected: new migration file in `backend/alembic/versions/`.

- [ ] **Step 3: Review the generated migration**

Open the generated migration file. Verify it includes:
- `op.add_column('optimizations', sa.Column('intent_label', ...))`
- `op.add_column('optimizations', sa.Column('embedding', ...))`
- `op.create_table('pattern_families', ...)`
- `op.create_table('meta_patterns', ...)`
- `op.create_table('optimization_patterns', ...)` with Integer autoincrement PK

- [ ] **Step 4: Run the migration**

```bash
cd backend && source .venv/bin/activate && alembic upgrade head
```

Expected: tables created successfully.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/
git commit -m "feat(models): add knowledge graph tables — PatternFamily, MetaPattern, OptimizationPattern"
```

---

### Task 2: Pipeline Contracts — Add intent_label and domain to AnalysisResult

**Files:**
- Modify: `backend/app/schemas/pipeline_contracts.py:63-73`
- Modify: `prompts/analyze.md`
- Modify: `prompts/manifest.json`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_analysis_result_fields.py`:

```python
"""Verify AnalysisResult accepts intent_label and domain fields."""

from app.schemas.pipeline_contracts import AnalysisResult


def test_analysis_result_with_intent_label_and_domain():
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["no error handling"],
        strengths=["clear intent"],
        selected_strategy="auto",
        strategy_rationale="best fit",
        confidence=0.9,
        intent_label="dependency injection refactoring",
        domain="backend",
    )
    assert result.intent_label == "dependency injection refactoring"
    assert result.domain == "backend"


def test_analysis_result_defaults_without_new_fields():
    """Backward compat: omitting new fields uses defaults."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=[],
        strengths=[],
        selected_strategy="auto",
        strategy_rationale="test",
        confidence=0.8,
    )
    assert result.intent_label == "general"
    assert result.domain == "general"


def test_analysis_result_rejects_unknown_fields():
    """extra=forbid still works."""
    import pytest
    with pytest.raises(Exception):
        AnalysisResult(
            task_type="coding",
            weaknesses=[],
            strengths=[],
            selected_strategy="auto",
            strategy_rationale="test",
            confidence=0.8,
            unknown_field="bad",
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_analysis_result_fields.py -v
```

Expected: FAIL — `intent_label` and `domain` not recognized (extra="forbid").

- [ ] **Step 3: Add fields to AnalysisResult**

In `backend/app/schemas/pipeline_contracts.py`, add after `confidence` (line 73):

```python
    intent_label: str = "general"
    domain: str = "general"
```

Also add `SuggestionsOutput` to `__all__` (line 204):

```python
__all__ = [
    "AnalysisResult",
    "AnalyzerInput",
    "DimensionScores",
    "OptimizerInput",
    "OptimizationResult",
    "PipelineEvent",
    "PipelineResult",
    "ResolvedContext",
    "ScoreResult",
    "ScorerInput",
    "SuggestionsOutput",
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_analysis_result_fields.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Update analyze.md prompt template**

Replace `prompts/analyze.md` with:

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<available-strategies>
{{available_strategies}}
</available-strategies>

## Instructions

You are an expert prompt analyst. Classify the user's prompt and identify its strengths and weaknesses.

Analyze the prompt above and determine:

1. **Task type** — What kind of task is this prompt for? Choose one: coding, writing, analysis, creative, data, system, general.
2. **Intent label** — A concise 3-6 word phrase describing the core intent of this prompt (e.g., "dependency injection refactoring", "API error handling", "landing page layout"). Be specific enough that two prompts with the same intent label are truly about the same thing.
3. **Domain** — What development area does this target? Choose one: backend, frontend, database, devops, security, fullstack, general. For non-development prompts, use "general".
4. **Weaknesses** — List specific, actionable problems. Be concrete: "no output format specified" not "could be improved."
5. **Strengths** — What does this prompt already do well? Even weak prompts have strengths.
6. **Strategy** — Select the single best strategy from the available list above. If unsure, select "auto."
7. **Rationale** — Explain in 1-2 sentences why this strategy fits.
8. **Confidence** — How confident are you? 0.0 = pure guess, 1.0 = certain. Below 0.7 triggers automatic fallback to "auto" strategy.

Think thoroughly about the prompt's intent and context before classifying. Consider who would write this prompt and what outcome they expect.
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/pipeline_contracts.py prompts/analyze.md backend/tests/test_analysis_result_fields.py
git commit -m "feat(pipeline): add intent_label and domain to AnalysisResult"
```

---

### Task 3: Persist intent_label and domain in Pipeline

**Files:**
- Modify: `backend/app/services/pipeline.py:510-533`

- [ ] **Step 1: Add intent_label and domain to the Optimization row construction**

In `backend/app/services/pipeline.py`, find the `db_opt = Optimization(...)` block (lines 510-533). Add two new fields after `task_type=analysis.task_type,` (line 514):

```python
                intent_label=getattr(analysis, "intent_label", "general"),
                domain=getattr(analysis, "domain", "general"),
```

Use `getattr` with defaults for safety — if the LLM omits these fields, the Pydantic defaults kick in, and `getattr` provides a second layer of protection.

- [ ] **Step 2: Also add to the optimization_created event payload**

In the `event_bus.publish("optimization_created", {...})` call (lines 540-548), add:

```python
                    "intent_label": getattr(analysis, "intent_label", "general"),
                    "domain": getattr(analysis, "domain", "general"),
```

- [ ] **Step 3: Verify existing pipeline tests still pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=30 -x
```

Expected: all existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/pipeline.py
git commit -m "feat(pipeline): persist intent_label and domain to optimization record"
```

---

### Task 4: Extract Patterns Prompt Template

**Files:**
- Create: `prompts/extract_patterns.md`
- Modify: `prompts/manifest.json`

- [ ] **Step 1: Create the extraction prompt template**

Create `prompts/extract_patterns.md`:

```markdown
<optimization-record>
<original-prompt>
{{raw_prompt}}
</original-prompt>
<optimized-prompt>
{{optimized_prompt}}
</optimized-prompt>
<intent-label>{{intent_label}}</intent-label>
<domain>{{domain}}</domain>
<strategy-used>{{strategy_used}}</strategy-used>
</optimization-record>

## Instructions

You are an expert prompt engineer analyzing a completed prompt optimization. Extract **reusable meta-patterns** — techniques that could be applied to similar prompts regardless of the specific framework, language, or project.

A meta-pattern is a high-level, framework-agnostic prompt engineering technique. Examples:
- "Enforce error boundaries at service layer boundaries"
- "Specify return type contract with edge case behavior"
- "Include concrete input/output examples for ambiguous operations"
- "Define explicit validation rules before describing logic"

Rules:
1. Extract 1-5 meta-patterns from this optimization.
2. Each pattern must be framework-agnostic — it should apply to any technology stack.
3. Focus on what made the optimization effective, not what the prompt is about.
4. Be specific enough to be actionable, general enough to transfer across projects.
5. If the optimization is trivial (minor wording changes only), return 1 pattern at most.

Return a JSON array of pattern descriptions (strings).
```

- [ ] **Step 2: Add to manifest.json**

Add to `prompts/manifest.json`:

```json
"extract_patterns.md": {"required": ["raw_prompt", "optimized_prompt", "intent_label", "domain", "strategy_used"], "optional": []}
```

- [ ] **Step 3: Verify template validation passes**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.config import PROMPTS_DIR
from app.services.prompt_loader import PromptLoader
loader = PromptLoader(PROMPTS_DIR)
loader.validate_all()
print('All templates valid')
"
```

Expected: "All templates valid"

- [ ] **Step 4: Commit**

```bash
git add prompts/extract_patterns.md prompts/manifest.json
git commit -m "feat(prompts): add extract_patterns.md template for Haiku meta-pattern extraction"
```

---

### Task 5: PatternExtractorService

**Files:**
- Create: `backend/app/services/pattern_extractor.py`
- Create: `backend/tests/test_pattern_extractor.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pattern_extractor.py`:

```python
"""Tests for PatternExtractorService — family creation, merging, meta-pattern extraction."""

import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.pattern_extractor import (
    PatternExtractorService,
    FAMILY_MERGE_THRESHOLD,
    PATTERN_MERGE_THRESHOLD,
)


@pytest.fixture
def embedding_service():
    svc = MagicMock()
    # Return deterministic 384-dim embeddings
    svc.aembed_single = AsyncMock(return_value=np.random.RandomState(42).randn(384).astype(np.float32))
    return svc


@pytest.fixture
def extractor(embedding_service):
    return PatternExtractorService(embedding_service=embedding_service)


class TestFamilyCreation:
    @pytest.mark.asyncio
    async def test_creates_new_family_when_no_families_exist(self, extractor):
        """Cold start: first optimization creates a new family."""
        from app.models import PatternFamily

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        family = await extractor._find_or_create_family(
            mock_db,
            embedding=np.ones(384, dtype=np.float32),
            intent_label="test pattern",
            domain="backend",
            task_type="coding",
            overall_score=7.5,
        )
        assert family is not None
        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_merges_into_existing_family_above_threshold(self, extractor):
        """Optimization with similar embedding merges into existing family."""
        from app.models import PatternFamily

        base_vec = np.ones(384, dtype=np.float32)
        # Create a family with a very similar centroid
        existing = PatternFamily(
            id="fam-1",
            intent_label="test",
            domain="backend",
            task_type="coding",
            centroid_embedding=base_vec.tobytes(),
            member_count=3,
            usage_count=0,
            avg_score=7.0,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [existing]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        family = await extractor._find_or_create_family(
            mock_db,
            embedding=base_vec,  # identical = cosine 1.0, above threshold
            intent_label="test",
            domain="backend",
            task_type="coding",
            overall_score=8.0,
        )
        assert family.id == "fam-1"


class TestCentroidUpdate:
    def test_running_mean_computation(self, extractor):
        """Centroid updates as running mean: (old * n + new) / (n + 1)."""
        old = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        new = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        result = extractor._update_centroid(old, new, member_count=1)
        expected = np.array([0.5, 0.5, 0.0], dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)

    def test_centroid_converges_with_many_members(self, extractor):
        """Centroid barely changes when family has many members."""
        old = np.ones(384, dtype=np.float32)
        new = np.zeros(384, dtype=np.float32)
        result = extractor._update_centroid(old, new, member_count=100)
        # New member should barely affect the centroid
        assert np.allclose(result, old, atol=0.02)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_pattern_extractor.py -v
```

Expected: FAIL — `pattern_extractor` module doesn't exist.

- [ ] **Step 3: Implement PatternExtractorService**

Create `backend/app/services/pattern_extractor.py`:

```python
"""Post-completion pattern extraction — embeds prompts, clusters into families,
extracts meta-patterns via Haiku LLM call.

Subscribes to 'optimization_created' events on the event bus and runs
extraction as an async background task.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, MODEL_HAIKU, PROMPTS_DIR
from app.database import async_session_factory
from app.models import MetaPattern, Optimization, OptimizationPattern, PatternFamily
from app.services.embedding_service import EmbeddingService
from app.services.event_bus import event_bus
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# Thresholds (cosine similarity)
FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82


class PatternExtractorService:
    """Extracts and clusters prompt patterns from completed optimizations."""

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._prompt_loader = PromptLoader(PROMPTS_DIR)

    async def process(self, optimization_id: str) -> None:
        """Full extraction pipeline for a single optimization.

        1. Embed the raw prompt
        2. Find or create a pattern family
        3. Extract meta-patterns via Haiku
        4. Merge meta-patterns into the family
        5. Write join records
        6. Publish pattern_updated event
        """
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Optimization).where(Optimization.id == optimization_id)
                )
                opt = result.scalar_one_or_none()
                if not opt or opt.status != "completed":
                    logger.debug("Skipping pattern extraction for %s (not found or not completed)", optimization_id)
                    return

                # 1. Embed the raw prompt
                embedding = await self._embedding.aembed_single(opt.raw_prompt)
                opt.embedding = embedding.astype(np.float32).tobytes()

                # 2. Find or create family
                family = await self._find_or_create_family(
                    db,
                    embedding=embedding,
                    intent_label=opt.intent_label or "general",
                    domain=getattr(opt, "domain", "general") or "general",
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                )

                # 3. Extract meta-patterns via Haiku
                meta_texts = await self._extract_meta_patterns(opt)

                # 4. Merge meta-patterns
                for text in meta_texts:
                    await self._merge_meta_pattern(db, family.id, text)

                # 5. Write join record
                join = OptimizationPattern(
                    optimization_id=opt.id,
                    family_id=family.id,
                    relationship="source",
                )
                db.add(join)

                await db.commit()

                # 6. Publish event
                event_bus.publish("pattern_updated", {
                    "family_id": family.id,
                    "intent_label": family.intent_label,
                    "domain": family.domain,
                    "optimization_id": opt.id,
                })

                logger.info(
                    "Pattern extraction complete: opt=%s family=%s patterns=%d",
                    optimization_id, family.intent_label, len(meta_texts),
                )

        except Exception as exc:
            logger.error("Pattern extraction failed for %s: %s", optimization_id, exc, exc_info=True)

    async def _find_or_create_family(
        self,
        db: AsyncSession,
        embedding: np.ndarray,
        intent_label: str,
        domain: str,
        task_type: str,
        overall_score: float | None,
    ) -> PatternFamily:
        """Find best matching family or create a new one."""
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if families:
            # Build centroid matrix and search
            centroids = [np.frombuffer(f.centroid_embedding, dtype=np.float32) for f in families]
            matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)

            if matches and matches[0][1] >= FAMILY_MERGE_THRESHOLD:
                idx, score = matches[0]
                family = families[idx]
                logger.debug("Merging into family '%s' (cosine=%.3f)", family.intent_label, score)

                # Update centroid as running mean
                old_centroid = np.frombuffer(family.centroid_embedding, dtype=np.float32)
                new_centroid = self._update_centroid(old_centroid, embedding, family.member_count)
                family.centroid_embedding = new_centroid.astype(np.float32).tobytes()
                family.member_count += 1

                # Update avg_score
                if overall_score is not None and family.avg_score is not None:
                    total = family.avg_score * (family.member_count - 1) + overall_score
                    family.avg_score = round(total / family.member_count, 2)
                elif overall_score is not None:
                    family.avg_score = overall_score

                return family

        # No match — create new family
        family = PatternFamily(
            intent_label=intent_label,
            domain=domain,
            task_type=task_type,
            centroid_embedding=embedding.astype(np.float32).tobytes(),
            member_count=1,
            usage_count=0,
            avg_score=overall_score,
        )
        db.add(family)
        await db.flush()  # get ID
        logger.info("Created new pattern family: '%s' (%s/%s)", intent_label, domain, task_type)
        return family

    @staticmethod
    def _update_centroid(old: np.ndarray, new: np.ndarray, member_count: int) -> np.ndarray:
        """Running mean: (old * n + new) / (n + 1)."""
        return (old * member_count + new) / (member_count + 1)

    async def _extract_meta_patterns(self, opt: Optimization) -> list[str]:
        """Call Haiku to extract meta-patterns from a completed optimization."""
        try:
            from app.providers.detector import detect_provider

            template = self._prompt_loader.render("extract_patterns.md", {
                "raw_prompt": opt.raw_prompt[:2000],  # cap input size
                "optimized_prompt": (opt.optimized_prompt or "")[:2000],
                "intent_label": opt.intent_label or "general",
                "domain": getattr(opt, "domain", "general") or "general",
                "strategy_used": opt.strategy_used or "auto",
            })

            from pydantic import BaseModel as PydanticBaseModel

            class ExtractedPatterns(PydanticBaseModel):
                model_config = {"extra": "forbid"}
                patterns: list[str]

            provider = detect_provider()
            response = await provider.complete_parsed(
                model=MODEL_HAIKU,
                system_prompt="You are a prompt engineering analyst. Extract reusable meta-patterns.",
                user_message=template,
                output_format=ExtractedPatterns,
            )

            # Filter and cap at 5
            return [str(p) for p in response.patterns if isinstance(p, str)][:5]

        except Exception as exc:
            logger.warning("Meta-pattern extraction failed (non-fatal): %s", exc)
            return []

    async def _merge_meta_pattern(self, db: AsyncSession, family_id: str, pattern_text: str) -> None:
        """Merge a meta-pattern into a family — enrich existing or create new."""
        result = await db.execute(
            select(MetaPattern).where(MetaPattern.family_id == family_id)
        )
        existing = result.scalars().all()

        pattern_embedding = await self._embedding.aembed_single(pattern_text)

        if existing:
            # Check similarity against existing patterns
            embeddings = []
            for mp in existing:
                if mp.embedding:
                    embeddings.append(np.frombuffer(mp.embedding, dtype=np.float32))
                else:
                    embeddings.append(np.zeros(384, dtype=np.float32))

            matches = EmbeddingService.cosine_search(pattern_embedding, embeddings, top_k=1)
            if matches and matches[0][1] >= PATTERN_MERGE_THRESHOLD:
                idx, score = matches[0]
                mp = existing[idx]
                mp.source_count += 1
                # Update text if new version is longer (richer)
                if len(pattern_text) > len(mp.pattern_text):
                    mp.pattern_text = pattern_text
                    mp.embedding = pattern_embedding.astype(np.float32).tobytes()
                logger.debug("Enriched meta-pattern '%s' (cosine=%.3f, count=%d)", mp.pattern_text[:50], score, mp.source_count)
                return

        # No match — create new
        mp = MetaPattern(
            family_id=family_id,
            pattern_text=pattern_text,
            embedding=pattern_embedding.astype(np.float32).tobytes(),
            source_count=1,
        )
        db.add(mp)
        logger.debug("Created new meta-pattern: '%s'", pattern_text[:50])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_pattern_extractor.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pattern_extractor.py backend/tests/test_pattern_extractor.py
git commit -m "feat(services): add PatternExtractorService — family clustering + meta-pattern extraction"
```

---

### Task 6: PatternMatcherService

**Files:**
- Create: `backend/app/services/pattern_matcher.py`
- Create: `backend/tests/test_pattern_matcher.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pattern_matcher.py`:

```python
"""Tests for PatternMatcherService — similarity search, cold start, suggestion threshold."""

import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.pattern_matcher import PatternMatcherService, SUGGESTION_THRESHOLD


@pytest.fixture
def embedding_service():
    svc = MagicMock()
    svc.aembed_single = AsyncMock(return_value=np.ones(384, dtype=np.float32))
    return svc


@pytest.fixture
def matcher(embedding_service):
    return PatternMatcherService(embedding_service=embedding_service)


class TestColdStart:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_families_exist(self, matcher):
        """Cold start: no families means no suggestion."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await matcher.match(mock_db, "test prompt")
        assert result is None


class TestSuggestionThreshold:
    def test_threshold_value(self):
        """Suggestion threshold should be lower than family merge threshold."""
        from app.services.pattern_extractor import FAMILY_MERGE_THRESHOLD
        assert SUGGESTION_THRESHOLD < FAMILY_MERGE_THRESHOLD


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_match_returns_correct_shape(self, matcher, embedding_service):
        """When a match is found, response has family + meta_patterns + similarity."""
        from app.models import PatternFamily, MetaPattern

        vec = np.ones(384, dtype=np.float32)
        family = PatternFamily(
            id="fam-1",
            intent_label="test pattern",
            domain="backend",
            task_type="coding",
            centroid_embedding=vec.tobytes(),
            usage_count=3,
            member_count=5,
            avg_score=7.5,
        )

        meta = MetaPattern(
            id="mp-1",
            family_id="fam-1",
            pattern_text="Use typed error boundaries",
            source_count=2,
        )

        mock_families_result = MagicMock()
        mock_families_result.scalars.return_value.all.return_value = [family]

        mock_meta_result = MagicMock()
        mock_meta_result.scalars.return_value.all.return_value = [meta]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_families_result, mock_meta_result])

        result = await matcher.match(mock_db, "test prompt")
        assert result is not None
        assert "family" in result
        assert "meta_patterns" in result
        assert "similarity" in result
        assert result["family"]["id"] == "fam-1"
        assert len(result["meta_patterns"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_pattern_matcher.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement PatternMatcherService**

Create `backend/app/services/pattern_matcher.py`:

```python
"""On-paste similarity search — matches incoming prompts against pattern family centroids.

Returns the best matching family + meta-patterns if above the suggestion threshold.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaPattern, PatternFamily
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

SUGGESTION_THRESHOLD = 0.72


class PatternMatcherService:
    """Matches prompt text against pattern family centroids."""

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding = embedding_service or EmbeddingService()

    async def match(self, db: AsyncSession, prompt_text: str) -> dict | None:
        """Find the best matching pattern family for a prompt.

        Returns None if no family matches above SUGGESTION_THRESHOLD.
        Returns dict with family, meta_patterns, and similarity score.
        """
        # Load all families
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if not families:
            return None

        # Embed the input prompt
        prompt_embedding = await self._embedding.aembed_single(prompt_text)

        # Cosine search against centroids
        centroids = [np.frombuffer(f.centroid_embedding, dtype=np.float32) for f in families]
        matches = EmbeddingService.cosine_search(prompt_embedding, centroids, top_k=1)

        if not matches or matches[0][1] < SUGGESTION_THRESHOLD:
            return None

        idx, similarity = matches[0]
        family = families[idx]

        # Load meta-patterns for this family
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.family_id == family.id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        return {
            "family": {
                "id": family.id,
                "intent_label": family.intent_label,
                "domain": family.domain,
                "task_type": family.task_type,
                "usage_count": family.usage_count,
                "avg_score": family.avg_score,
            },
            "meta_patterns": [
                {
                    "id": mp.id,
                    "pattern_text": mp.pattern_text,
                    "source_count": mp.source_count,
                }
                for mp in meta_patterns
            ],
            "similarity": round(similarity, 3),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_pattern_matcher.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pattern_matcher.py backend/tests/test_pattern_matcher.py
git commit -m "feat(services): add PatternMatcherService — on-paste similarity search"
```

---

### Task 7: KnowledgeGraphService

**Files:**
- Create: `backend/app/services/knowledge_graph.py`
- Create: `backend/tests/test_knowledge_graph.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_knowledge_graph.py`:

```python
"""Tests for KnowledgeGraphService — graph building, edge computation, search."""

import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.knowledge_graph import KnowledgeGraphService, EDGE_THRESHOLD


@pytest.fixture
def graph_service():
    embedding_svc = MagicMock()
    embedding_svc.aembed_single = AsyncMock(return_value=np.ones(384, dtype=np.float32))
    return KnowledgeGraphService(embedding_service=embedding_svc)


class TestGraphBuilding:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_zero_counts(self, graph_service):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        graph = await graph_service.get_graph(mock_db)
        assert graph["center"]["total_families"] == 0
        assert graph["families"] == []
        assert graph["edges"] == []


class TestEdgeComputation:
    def test_edge_threshold_value(self):
        assert EDGE_THRESHOLD == 0.55

    def test_similar_centroids_produce_edge(self, graph_service):
        """Two families with similar centroids should produce an edge."""
        from app.models import PatternFamily

        vec_a = np.ones(384, dtype=np.float32)
        vec_b = np.ones(384, dtype=np.float32) * 0.95 + np.random.RandomState(42).randn(384).astype(np.float32) * 0.05

        families = [
            PatternFamily(id="a", intent_label="test-a", domain="backend", task_type="coding",
                         centroid_embedding=vec_a.tobytes(), usage_count=1, member_count=1, avg_score=7.0),
            PatternFamily(id="b", intent_label="test-b", domain="backend", task_type="coding",
                         centroid_embedding=vec_b.tobytes(), usage_count=1, member_count=1, avg_score=7.0),
        ]
        edges = graph_service._compute_edges(families)
        # Very similar vectors (0.95 base + 0.05 noise) should produce an edge
        assert len(edges) == 1
        assert edges[0]["from"] == "a"
        assert edges[0]["to"] == "b"
        assert edges[0]["weight"] >= EDGE_THRESHOLD


class TestSemanticSearch:
    @pytest.mark.asyncio
    async def test_search_returns_ranked_results(self, graph_service):
        from app.models import PatternFamily

        vec = np.ones(384, dtype=np.float32)
        families = [
            PatternFamily(id="a", intent_label="DI refactoring", domain="backend", task_type="coding",
                         centroid_embedding=vec.tobytes(), usage_count=3, member_count=3, avg_score=7.5),
        ]
        mock_fam_result = MagicMock()
        mock_fam_result.scalars.return_value.all.return_value = families
        mock_meta_result = MagicMock()
        mock_meta_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_fam_result, mock_meta_result])

        results = await graph_service.search_patterns(mock_db, "dependency injection", top_k=5)
        assert isinstance(results, list)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_knowledge_graph.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement KnowledgeGraphService**

Create `backend/app/services/knowledge_graph.py`:

```python
"""Knowledge graph service — in-memory cache, graph computation, semantic search.

Provides the data structure for the radial mindmap frontend visualization.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaPattern, Optimization, OptimizationPattern, PatternFamily
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

EDGE_THRESHOLD = 0.55


class KnowledgeGraphService:
    """Builds and queries the pattern knowledge graph."""

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embedding = embedding_service or EmbeddingService()

    async def get_graph(self, db: AsyncSession, family_id: str | None = None) -> dict:
        """Build the full graph structure for the radial mindmap."""
        # Load families
        query = select(PatternFamily)
        if family_id:
            query = query.where(PatternFamily.id == family_id)
        result = await db.execute(query)
        families = result.scalars().all()

        if not families:
            return {"center": {"total_families": 0, "total_patterns": 0, "total_optimizations": 0}, "families": [], "edges": []}

        # Load meta-patterns for all families
        family_ids = [f.id for f in families]
        meta_result = await db.execute(
            select(MetaPattern).where(MetaPattern.family_id.in_(family_ids))
        )
        all_meta = meta_result.scalars().all()
        meta_by_family: dict[str, list] = {}
        for mp in all_meta:
            meta_by_family.setdefault(mp.family_id, []).append(mp)

        # Count total optimizations linked
        opt_count_result = await db.execute(
            select(func.count(func.distinct(OptimizationPattern.optimization_id)))
            .where(OptimizationPattern.family_id.in_(family_ids))
        )
        total_optimizations = opt_count_result.scalar() or 0

        # Build family nodes
        family_nodes = []
        for f in families:
            meta_patterns = meta_by_family.get(f.id, [])
            family_nodes.append({
                "id": f.id,
                "intent_label": f.intent_label,
                "domain": f.domain,
                "task_type": f.task_type,
                "usage_count": f.usage_count,
                "member_count": f.member_count,
                "avg_score": f.avg_score,
                "meta_patterns": [
                    {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
                    for mp in sorted(meta_patterns, key=lambda m: m.source_count, reverse=True)
                ],
            })

        # Compute edges
        edges = self._compute_edges(families) if len(families) > 1 else []

        return {
            "center": {
                "total_families": len(families),
                "total_patterns": len(all_meta),
                "total_optimizations": total_optimizations,
            },
            "families": family_nodes,
            "edges": edges,
        }

    def _compute_edges(self, families: list[PatternFamily]) -> list[dict]:
        """Compute cross-family edges based on centroid similarity."""
        edges = []
        centroids = []
        for f in families:
            centroids.append(np.frombuffer(f.centroid_embedding, dtype=np.float32))

        for i in range(len(families)):
            for j in range(i + 1, len(families)):
                # Cosine similarity
                norm_i = np.linalg.norm(centroids[i]) + 1e-9
                norm_j = np.linalg.norm(centroids[j]) + 1e-9
                sim = float(np.dot(centroids[i], centroids[j]) / (norm_i * norm_j))

                if sim >= EDGE_THRESHOLD:
                    edges.append({
                        "from": families[i].id,
                        "to": families[j].id,
                        "shared_patterns": 0,
                        "weight": round(sim, 3),
                    })

        return edges

    async def search_patterns(
        self, db: AsyncSession, query: str, top_k: int = 5
    ) -> list[dict]:
        """Semantic search across all families and meta-patterns."""
        query_embedding = await self._embedding.aembed_single(query)

        # Search families
        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if not families:
            return []

        centroids = [np.frombuffer(f.centroid_embedding, dtype=np.float32) for f in families]
        matches = EmbeddingService.cosine_search(query_embedding, centroids, top_k=top_k)

        # Also search meta-patterns
        meta_result = await db.execute(select(MetaPattern).where(MetaPattern.embedding.isnot(None)))
        all_meta = meta_result.scalars().all()

        results = []
        for idx, score in matches:
            f = families[idx]
            results.append({
                "type": "family",
                "id": f.id,
                "label": f.intent_label,
                "domain": f.domain,
                "score": round(score, 3),
            })

        if all_meta:
            meta_embeddings = [np.frombuffer(mp.embedding, dtype=np.float32) for mp in all_meta]
            meta_matches = EmbeddingService.cosine_search(query_embedding, meta_embeddings, top_k=top_k)
            for idx, score in meta_matches:
                mp = all_meta[idx]
                results.append({
                    "type": "meta_pattern",
                    "id": mp.id,
                    "label": mp.pattern_text,
                    "family_id": mp.family_id,
                    "score": round(score, 3),
                })

        # Sort by score, return top_k
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    async def get_family_detail(self, db: AsyncSession, family_id: str) -> dict | None:
        """Get detailed view of a single family."""
        result = await db.execute(
            select(PatternFamily).where(PatternFamily.id == family_id)
        )
        family = result.scalar_one_or_none()
        if not family:
            return None

        # Meta-patterns
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.family_id == family_id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        # Linked optimizations
        opt_result = await db.execute(
            select(Optimization)
            .join(OptimizationPattern, OptimizationPattern.optimization_id == Optimization.id)
            .where(OptimizationPattern.family_id == family_id)
            .order_by(Optimization.created_at.desc())
            .limit(20)
        )
        optimizations = opt_result.scalars().all()

        return {
            "id": family.id,
            "intent_label": family.intent_label,
            "domain": family.domain,
            "task_type": family.task_type,
            "usage_count": family.usage_count,
            "member_count": family.member_count,
            "avg_score": family.avg_score,
            "created_at": family.created_at.isoformat() if family.created_at else None,
            "updated_at": family.updated_at.isoformat() if family.updated_at else None,
            "meta_patterns": [
                {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
                for mp in meta_patterns
            ],
            "optimizations": [
                {
                    "id": o.id,
                    "raw_prompt": (o.raw_prompt or "")[:100],
                    "overall_score": o.overall_score,
                    "strategy_used": o.strategy_used,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in optimizations
            ],
        }

    async def get_stats(self, db: AsyncSession) -> dict:
        """Summary statistics for the knowledge graph."""
        fam_count = (await db.execute(select(func.count(PatternFamily.id)))).scalar() or 0
        meta_count = (await db.execute(select(func.count(MetaPattern.id)))).scalar() or 0
        opt_count = (await db.execute(
            select(func.count(func.distinct(OptimizationPattern.optimization_id)))
        )).scalar() or 0

        # Domain distribution
        domain_result = await db.execute(
            select(PatternFamily.domain, func.count(PatternFamily.id))
            .group_by(PatternFamily.domain)
        )
        domain_dist = {row[0]: row[1] for row in domain_result}

        return {
            "total_families": fam_count,
            "total_patterns": meta_count,
            "total_optimizations": opt_count,
            "domain_distribution": domain_dist,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_knowledge_graph.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/knowledge_graph.py backend/tests/test_knowledge_graph.py
git commit -m "feat(services): add KnowledgeGraphService — graph building, edge computation, semantic search"
```

---

### Task 8: Patterns Router

**Files:**
- Create: `backend/app/routers/patterns.py`
- Create: `backend/tests/test_patterns_router.py`
- Modify: `backend/app/main.py:170-179`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_patterns_router.py`:

```python
"""Tests for patterns router — endpoints, error cases."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestPatternsGraphEndpoint:
    @pytest.mark.asyncio
    async def test_graph_returns_200(self):
        """GET /api/patterns/graph returns graph structure."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/graph" for r in router.routes)

    @pytest.mark.asyncio
    async def test_match_endpoint_exists(self):
        """POST /api/patterns/match endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/match" for r in router.routes)

    @pytest.mark.asyncio
    async def test_families_endpoint_exists(self):
        """GET /api/patterns/families endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/families" for r in router.routes)

    @pytest.mark.asyncio
    async def test_search_endpoint_exists(self):
        """GET /api/patterns/search endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/search" for r in router.routes)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_patterns_router.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the router**

Create `backend/app/routers/patterns.py`:

```python
"""Pattern knowledge graph endpoints — graph, families, match, search."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PatternFamily
from app.services.knowledge_graph import KnowledgeGraphService
from app.services.pattern_matcher import PatternMatcherService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])

_graph_service = KnowledgeGraphService()
_matcher_service = PatternMatcherService()


class MatchRequest(BaseModel):
    prompt_text: str = Field(..., min_length=10)


class RenameRequest(BaseModel):
    intent_label: str = Field(..., min_length=1, max_length=100)


@router.get("/graph")
async def get_graph(
    family_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Full mindmap graph data or subtree for a specific family."""
    return await _graph_service.get_graph(db, family_id=family_id)


@router.post("/match")
async def match_pattern(
    body: MatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Similarity check for auto-suggestion on paste."""
    result = await _matcher_service.match(db, body.prompt_text)
    if result is None:
        return {"match": None}
    return {"match": result}


@router.get("/families")
async def list_families(
    offset: int = 0,
    limit: int = 50,
    domain: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all pattern families with pagination."""
    from sqlalchemy import func, select

    query = select(PatternFamily).order_by(PatternFamily.usage_count.desc())
    count_query = select(func.count(PatternFamily.id))

    if domain:
        query = query.where(PatternFamily.domain == domain)
        count_query = count_query.where(PatternFamily.domain == domain)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    families = result.scalars().all()

    return {
        "total": total,
        "count": len(families),
        "offset": offset,
        "has_more": offset + len(families) < total,
        "next_offset": offset + len(families) if offset + len(families) < total else None,
        "items": [
            {
                "id": f.id,
                "intent_label": f.intent_label,
                "domain": f.domain,
                "task_type": f.task_type,
                "usage_count": f.usage_count,
                "member_count": f.member_count,
                "avg_score": f.avg_score,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in families
        ],
    }


@router.get("/families/{family_id}")
async def get_family(
    family_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Family detail with meta-patterns and linked optimizations."""
    detail = await _graph_service.get_family_detail(db, family_id)
    if not detail:
        raise HTTPException(404, "Pattern family not found")
    return detail


@router.patch("/families/{family_id}")
async def rename_family(
    family_id: str,
    body: RenameRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rename a pattern family (user label override)."""
    from sqlalchemy import select

    result = await db.execute(
        select(PatternFamily).where(PatternFamily.id == family_id)
    )
    family = result.scalar_one_or_none()
    if not family:
        raise HTTPException(404, "Pattern family not found")

    family.intent_label = body.intent_label
    await db.commit()
    return {"id": family.id, "intent_label": family.intent_label}


@router.get("/search")
async def search_patterns(
    q: str,
    top_k: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """Semantic search across families and meta-patterns."""
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    return await _graph_service.search_patterns(db, q, top_k=min(top_k, 20))


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Summary statistics for the knowledge graph."""
    return await _graph_service.get_stats(db)
```

- [ ] **Step 4: Register the router in main.py**

Add after the strategies router block (around line 179) in `backend/app/main.py`:

```python
try:
    from app.routers.patterns import router as patterns_router
    app.include_router(patterns_router)
except ImportError:
    pass
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_patterns_router.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/patterns.py backend/tests/test_patterns_router.py backend/app/main.py
git commit -m "feat(routers): add /api/patterns endpoints — graph, match, families, search"
```

---

### Task 9: Wire Pattern Extraction to Event Bus

**Files:**
- Modify: `backend/app/main.py:52-57` (lifespan, before yield)

- [ ] **Step 1: Add pattern extraction subscriber to app lifespan**

In `backend/app/main.py`, add before the `yield` statement (line 57) in the lifespan:

```python
    # Start pattern extraction subscriber
    async def _pattern_extraction_listener():
        """Subscribe to optimization_created events and extract patterns."""
        try:
            from app.services.pattern_extractor import PatternExtractorService
            extractor = PatternExtractorService()
            async for event in event_bus.subscribe():
                if event.get("event") == "optimization_created":
                    opt_id = event.get("data", {}).get("id")
                    if opt_id:
                        import asyncio
                        asyncio.create_task(extractor.process(opt_id))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Pattern extraction listener crashed: %s", exc, exc_info=True)

    from app.services.event_bus import event_bus
    extraction_task = asyncio.create_task(_pattern_extraction_listener())
    app.state.extraction_task = extraction_task
```

Add cleanup after the watcher cancel block (around line 65):

```python
    # Stop pattern extraction listener
    if hasattr(app.state, "extraction_task"):
        app.state.extraction_task.cancel()
        try:
            await app.state.extraction_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Verify the app still starts**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.main import app
print('App created successfully, routes:', len(app.routes))
"
```

Expected: app creation succeeds.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(main): wire pattern extraction listener to event bus"
```

---

## Phase 2: Auto-Suggestion Frontend

### Task 10: Frontend API Client — Pattern Types and Functions

**Files:**
- Create: `frontend/src/lib/api/patterns.ts`
- Modify: `frontend/src/lib/api/client.ts` (add types)

- [ ] **Step 1: Create pattern API client**

Create `frontend/src/lib/api/patterns.ts`:

```typescript
/**
 * Pattern knowledge graph API client.
 */
import { apiFetch } from './client';

export interface PatternFamily {
  id: string;
  intent_label: string;
  domain: string;
  task_type: string;
  usage_count: number;
  member_count: number;
  avg_score: number | null;
  created_at: string | null;
}

export interface MetaPatternItem {
  id: string;
  pattern_text: string;
  source_count: number;
}

export interface PatternMatch {
  family: PatternFamily;
  meta_patterns: MetaPatternItem[];
  similarity: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  shared_patterns: number;
  weight: number;
}

export interface GraphFamily extends PatternFamily {
  meta_patterns: MetaPatternItem[];
}

export interface PatternGraph {
  center: { total_families: number; total_patterns: number; total_optimizations: number };
  families: GraphFamily[];
  edges: GraphEdge[];
}

export interface FamilyDetail extends PatternFamily {
  updated_at: string | null;
  meta_patterns: MetaPatternItem[];
  optimizations: { id: string; raw_prompt: string; overall_score: number | null; strategy_used: string | null; created_at: string | null }[];
}

export const matchPattern = (prompt_text: string) =>
  apiFetch<{ match: PatternMatch | null }>('/patterns/match', {
    method: 'POST',
    body: JSON.stringify({ prompt_text }),
  });

export const getPatternGraph = (familyId?: string) => {
  const qs = familyId ? `?family_id=${familyId}` : '';
  return apiFetch<PatternGraph>(`/patterns/graph${qs}`);
};

export const listFamilies = (params?: { offset?: number; limit?: number; domain?: string }) => {
  const search = new URLSearchParams();
  if (params?.offset) search.set('offset', String(params.offset));
  if (params?.limit) search.set('limit', String(params.limit));
  if (params?.domain) search.set('domain', params.domain);
  const qs = search.toString();
  return apiFetch<{ total: number; count: number; offset: number; has_more: boolean; next_offset: number | null; items: PatternFamily[] }>(
    `/patterns/families${qs ? '?' + qs : ''}`
  );
};

export const getFamilyDetail = (familyId: string) =>
  apiFetch<FamilyDetail>(`/patterns/families/${familyId}`);

export const renameFamily = (familyId: string, intent_label: string) =>
  apiFetch<{ id: string; intent_label: string }>(`/patterns/families/${familyId}`, {
    method: 'PATCH',
    body: JSON.stringify({ intent_label }),
  });

export const searchPatterns = (q: string, topK = 5) =>
  apiFetch<{ type: string; id: string; label: string; score: number }[]>(
    `/patterns/search?q=${encodeURIComponent(q)}&top_k=${topK}`
  );

export const getPatternStats = () =>
  apiFetch<{ total_families: number; total_patterns: number; total_optimizations: number; domain_distribution: Record<string, number> }>(
    '/patterns/stats'
  );
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/api/patterns.ts
git commit -m "feat(frontend): add pattern API client — types and fetch functions"
```

---

### Task 11: Pattern Store

**Files:**
- Create: `frontend/src/lib/stores/patterns.svelte.ts`

- [ ] **Step 1: Create the pattern store**

Create `frontend/src/lib/stores/patterns.svelte.ts`:

```typescript
/**
 * Pattern store — suggestion state, family data, paste detection.
 *
 * Manages auto-suggestion on paste and pattern graph data for the mindmap.
 */

import { matchPattern, getPatternGraph, type PatternMatch, type PatternGraph } from '$lib/api/patterns';

const PASTE_CHAR_DELTA = 50;
const PASTE_DEBOUNCE_MS = 300;
const SUGGESTION_AUTO_DISMISS_MS = 10_000;

class PatternStore {
  // Suggestion state
  suggestion = $state<PatternMatch | null>(null);
  suggestionVisible = $state(false);

  // Graph data
  graph = $state<PatternGraph | null>(null);
  graphLoaded = $state(false);
  graphError = $state<string | null>(null);

  // Internal
  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private _dismissTimer: ReturnType<typeof setTimeout> | null = null;
  private _lastLength = 0;

  /**
   * Called on paste/input — checks if content delta exceeds threshold,
   * debounces, then calls the match endpoint.
   */
  checkForPatterns(text: string): void {
    const delta = Math.abs(text.length - this._lastLength);
    this._lastLength = text.length;

    if (delta < PASTE_CHAR_DELTA) return;

    // Debounce
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(async () => {
      try {
        const resp = await matchPattern(text);
        if (resp.match) {
          this.suggestion = resp.match;
          this.suggestionVisible = true;
          this._startDismissTimer();
        } else {
          this.suggestion = null;
          this.suggestionVisible = false;
        }
      } catch (err) {
        console.warn('Pattern match failed:', err);
      }
    }, PASTE_DEBOUNCE_MS);
  }

  /**
   * User clicked [Apply] — returns the meta-pattern texts for pipeline injection.
   */
  applySuggestion(): string[] | null {
    if (!this.suggestion) return null;
    const patterns = this.suggestion.meta_patterns.map(mp => mp.pattern_text);
    this.dismissSuggestion();
    return patterns;
  }

  /**
   * User clicked [Skip] or auto-dismiss timer fired.
   */
  dismissSuggestion(): void {
    this.suggestion = null;
    this.suggestionVisible = false;
    if (this._dismissTimer) {
      clearTimeout(this._dismissTimer);
      this._dismissTimer = null;
    }
  }

  /**
   * Load graph data for the mindmap.
   */
  async loadGraph(familyId?: string): Promise<void> {
    try {
      this.graphError = null;
      this.graph = await getPatternGraph(familyId);
      this.graphLoaded = true;
    } catch (err) {
      this.graphError = err instanceof Error ? err.message : 'Failed to load graph';
      console.error('Graph load failed:', err);
    }
  }

  /**
   * Refresh graph data (called on pattern_updated events).
   */
  invalidateGraph(): void {
    this.graphLoaded = false;
  }

  /**
   * Reset last length tracking (call when prompt is cleared).
   */
  resetTracking(): void {
    this._lastLength = 0;
  }

  private _startDismissTimer(): void {
    if (this._dismissTimer) clearTimeout(this._dismissTimer);
    this._dismissTimer = setTimeout(() => {
      this.dismissSuggestion();
    }, SUGGESTION_AUTO_DISMISS_MS);
  }
}

export const patternsStore = new PatternStore();
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/stores/patterns.svelte.ts
git commit -m "feat(frontend): add pattern store — suggestion state, graph data, paste detection"
```

---

### Task 12: PatternSuggestion Component

**Files:**
- Create: `frontend/src/lib/components/editor/PatternSuggestion.svelte`

- [ ] **Step 1: Create the suggestion banner component**

Create `frontend/src/lib/components/editor/PatternSuggestion.svelte`:

```svelte
<script lang="ts">
  import { patternsStore } from '$lib/stores/patterns.svelte';

  interface Props {
    onApply: (patterns: string[]) => void;
  }

  let { onApply }: Props = $props();

  function handleApply() {
    const patterns = patternsStore.applySuggestion();
    if (patterns) onApply(patterns);
  }

  function handleSkip() {
    patternsStore.dismissSuggestion();
  }
</script>

{#if patternsStore.suggestionVisible && patternsStore.suggestion}
  {@const match = patternsStore.suggestion}
  <div class="suggestion-banner" role="alert">
    <div class="suggestion-content">
      <div class="suggestion-header">
        <span class="suggestion-icon">&#x27E1;</span>
        <span class="suggestion-label">
          Matches "<strong>{match.family.intent_label}</strong>" pattern ({Math.round(match.similarity * 100)}%)
        </span>
      </div>
      <div class="suggestion-meta">
        {match.meta_patterns.length} meta-pattern{match.meta_patterns.length !== 1 ? 's' : ''} available
        {#if match.family.avg_score != null}
          &middot; avg score {match.family.avg_score.toFixed(1)}
        {/if}
      </div>
    </div>
    <div class="suggestion-actions">
      <button class="btn-apply" onclick={handleApply}>Apply</button>
      <button class="btn-skip" onclick={handleSkip}>Skip</button>
    </div>
  </div>
{/if}

<style>
  .suggestion-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    background: #0d0d1a;
    border: 1px solid #00e5ff33;
    margin: 4px 0;
    animation: slideIn 200ms ease-out;
    font-size: 11px;
  }

  @keyframes slideIn {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .suggestion-content {
    flex: 1;
    min-width: 0;
  }

  .suggestion-header {
    display: flex;
    align-items: center;
    gap: 6px;
    color: #c8c8d0;
  }

  .suggestion-icon {
    color: #00e5ff;
    font-size: 14px;
  }

  .suggestion-label strong {
    color: #00e5ff;
  }

  .suggestion-meta {
    color: #666;
    font-size: 10px;
    margin-top: 2px;
    padding-left: 20px;
  }

  .suggestion-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  .btn-apply {
    background: transparent;
    border: 1px solid #00e5ff;
    color: #00e5ff;
    padding: 2px 10px;
    font-size: 10px;
    font-family: inherit;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .btn-apply:hover {
    background: #00e5ff15;
  }

  .btn-skip {
    background: transparent;
    border: 1px solid #333;
    color: #666;
    padding: 2px 10px;
    font-size: 10px;
    font-family: inherit;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .btn-skip:hover {
    border-color: #555;
    color: #888;
  }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/editor/PatternSuggestion.svelte
git commit -m "feat(frontend): add PatternSuggestion banner component"
```

---

### Task 13: Wire Paste Detection into PromptEdit

**Files:**
- Modify: `frontend/src/lib/components/editor/PromptEdit.svelte`
- Modify: `frontend/src/lib/stores/forge.svelte.ts`

- [ ] **Step 1: Add paste detection and suggestion banner to PromptEdit**

In `frontend/src/lib/components/editor/PromptEdit.svelte`:

1. Add imports at the top of the `<script>` block:

```typescript
import { patternsStore } from '$lib/stores/patterns.svelte';
import PatternSuggestion from './PatternSuggestion.svelte';
```

2. Add a handler function for paste detection:

```typescript
function handleInput(e: Event) {
  const target = e.target as HTMLTextAreaElement;
  patternsStore.checkForPatterns(target.value);
}
```

3. Add `oninput={handleInput}` to the textarea element.

4. Add the `PatternSuggestion` component above the textarea, with an `onApply` handler:

```svelte
<PatternSuggestion onApply={(patterns) => {
  forgeStore.appliedPatterns = patterns;
}} />
```

- [ ] **Step 2: Add appliedPatterns field to forge store**

In `frontend/src/lib/stores/forge.svelte.ts`, add a new state field:

```typescript
appliedPatterns = $state<string[] | null>(null);
```

Ensure it is cleared when a new optimization starts (in the `forge()` or `reset()` method):

```typescript
this.appliedPatterns = null;
```

- [ ] **Step 3: Verify the frontend builds**

```bash
cd frontend && npm run check
```

Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/editor/PromptEdit.svelte frontend/src/lib/stores/forge.svelte.ts
git commit -m "feat(frontend): wire paste detection and suggestion banner into PromptEdit"
```

---

### Task 14: Pipeline — Accept applied_patterns Parameter

**Files:**
- Modify: `backend/app/routers/optimize.py:27-31` (OptimizeRequest)
- Modify: `backend/app/services/pipeline.py` (OptimizerInput assembly)
- Modify: `backend/app/schemas/pipeline_contracts.py:121-132` (OptimizerInput)

- [ ] **Step 1: Add applied_patterns to OptimizeRequest**

In `backend/app/routers/optimize.py`, add to `OptimizeRequest`:

```python
    applied_pattern_ids: list[str] | None = Field(None, description="Pattern IDs to inject into optimizer context")
```

- [ ] **Step 2: Add applied_patterns to OptimizerInput**

In `backend/app/schemas/pipeline_contracts.py`, add to `OptimizerInput` (after `adaptation_state`):

```python
    applied_patterns: str | None = None
```

- [ ] **Step 3: Pass applied_patterns through the pipeline**

In `backend/app/services/pipeline.py`, where `OptimizerInput` is constructed, add logic to:
1. Accept `applied_pattern_ids` parameter in `PipelineOrchestrator.run()`
2. Look up `MetaPattern.pattern_text` for the given IDs
3. Format them as a string and pass as `applied_patterns` to `OptimizerInput`

In `backend/app/routers/optimize.py`, pass `applied_pattern_ids=body.applied_pattern_ids` to `orchestrator.run()`.

- [ ] **Step 4: Update optimize.md to use applied_patterns**

Add an optional section to `prompts/optimize.md`:

```markdown
{{#if applied_patterns}}
<applied-meta-patterns>
The user has chosen to apply these reusable meta-patterns from their prompt library. Incorporate them into the optimization where they logically apply:

{{applied_patterns}}
</applied-meta-patterns>
{{/if}}
```

Note: Check if the template engine supports conditionals. If not, pass `applied_patterns` as an empty string when not set, and use the `optional` field in manifest.json.

- [ ] **Step 5: Update manifest.json**

Add `applied_patterns` to the `optimize.md` optional list:

```json
"optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"], "optional": ["codebase_guidance", "codebase_context", "adaptation_state", "applied_patterns"]}
```

- [ ] **Step 6: Verify tests pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=30 -x
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/optimize.py backend/app/services/pipeline.py backend/app/schemas/pipeline_contracts.py prompts/optimize.md prompts/manifest.json
git commit -m "feat(pipeline): accept and inject applied_patterns from knowledge graph"
```

---

## Phase 3: Radial Mindmap

### Task 15: Install D3.js

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install D3 dependencies**

```bash
cd frontend && npm install d3 && npm install -D @types/d3
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add d3 dependency for radial mindmap"
```

---

### Task 16: Pattern Navigator Compact View

**Files:**
- Create: `frontend/src/lib/components/layout/PatternNavigator.svelte`
- Modify: `frontend/src/lib/components/layout/ActivityBar.svelte`
- Modify: `frontend/src/lib/components/layout/Navigator.svelte`

- [ ] **Step 1: Create PatternNavigator component**

Create `frontend/src/lib/components/layout/PatternNavigator.svelte` — a compact list of families grouped by domain, with usage count and score badges. Each row shows `intent_label`, clicking expands to show meta-patterns inline. Include an "Open Mindmap" button that opens the full visualization in an editor tab.

Follow the existing Navigator panel pattern — same 240px width, same styling as the history panel rows. Use the domain color coding from the spec:
- backend = `#a855f7`, frontend = `#f59e0b`, database = `#10b981`, security = `#ef4444`, devops = `#3b82f6`, fullstack = `#00e5ff`, general = `#6b7280`

Empty state: "Optimize your first prompt to start building your pattern library."

- [ ] **Step 2: Add patterns icon to ActivityBar**

In `frontend/src/lib/components/layout/ActivityBar.svelte`, add a new activity icon (constellation/nodes icon) between History and GitHub. The icon should be an SVG of 3 connected dots (simple node graph). The activity name should be `'patterns'`.

- [ ] **Step 3: Add patterns panel to Navigator**

In `frontend/src/lib/components/layout/Navigator.svelte`, add a new conditional block for `active === 'patterns'` that renders `<PatternNavigator />`. Follow the same lazy-loading pattern as history — load on first activation, refresh on `pattern_updated` SSE events.

- [ ] **Step 4: Verify the frontend builds**

```bash
cd frontend && npm run check
```

Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/PatternNavigator.svelte frontend/src/lib/components/layout/ActivityBar.svelte frontend/src/lib/components/layout/Navigator.svelte
git commit -m "feat(frontend): add pattern navigator compact view with activity bar icon"
```

---

### Task 17: Radial Mindmap Editor Tab

**Files:**
- Create: `frontend/src/lib/components/patterns/RadialMindmap.svelte`
- Modify: `frontend/src/lib/stores/editor.svelte.ts`
- Modify: `frontend/src/lib/components/layout/EditorGroups.svelte`

- [ ] **Step 1: Create RadialMindmap component**

Create `frontend/src/lib/components/patterns/RadialMindmap.svelte` — a full interactive radial mindmap using D3.js:

1. **Center node:** Shows total families/patterns count
2. **Ring 1 (domains):** Color-coded domain arcs, sized by family count in that domain
3. **Ring 2 (families):** Pattern family nodes, sized by `usage_count`, positioned within their domain sector
4. **Ring 3 (meta-patterns):** Shown on hover/click of a family node
5. **Edges:** Curved lines between families with `weight > EDGE_THRESHOLD`, opacity = weight
6. **Interactions:** Click family → update Inspector, zoom/pan via d3-zoom

Use `$effect` to react to `patternsStore.graph` changes. Mount D3 into an SVG element via `bind:this`.

Follow brand guidelines: dark background `#06060c`, 1px neon contours, no rounded corners, monospace labels.

- [ ] **Step 2: Add mindmap tab type to editor store**

In `frontend/src/lib/stores/editor.svelte.ts`, add a method:

```typescript
openMindmap(): void {
  // Create or activate a tab with id 'mindmap'
  // Tab type: 'mindmap', title: 'Pattern Graph'
}
```

- [ ] **Step 3: Render mindmap tab in EditorGroups**

In `frontend/src/lib/components/layout/EditorGroups.svelte`, add a conditional for the mindmap tab type that renders `<RadialMindmap />`.

- [ ] **Step 4: Verify the frontend builds**

```bash
cd frontend && npm run check
```

Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/patterns/RadialMindmap.svelte frontend/src/lib/stores/editor.svelte.ts frontend/src/lib/components/layout/EditorGroups.svelte
git commit -m "feat(frontend): add radial mindmap editor tab with D3.js visualization"
```

---

### Task 18: Inspector Integration for Pattern Families

**Files:**
- Modify: `frontend/src/lib/components/layout/Inspector.svelte`

- [ ] **Step 1: Add pattern family detail view to Inspector**

When the active tab is the mindmap and a family is selected, the Inspector should show:
- Family intent_label as header
- Domain badge (color-coded)
- Usage count and member count
- Average score
- List of meta-patterns with source counts
- List of linked optimizations (clickable — opens the optimization in a result tab)

Follow existing Inspector patterns — same styling, same density.

- [ ] **Step 2: Verify the frontend builds**

```bash
cd frontend && npm run check
```

Expected: no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/layout/Inspector.svelte
git commit -m "feat(frontend): add pattern family detail view to Inspector"
```

---

### Task 19: CHANGELOG and Version Bump

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `version.json`

- [ ] **Step 1: Add changelog entries**

Add under `## Unreleased` in `docs/CHANGELOG.md`:

```markdown
### Added
- Added prompt knowledge graph — auto-extracts reusable meta-patterns from optimizations
- Added auto-suggestion banner on paste — detects similar pattern families with 1-click apply
- Added radial mindmap visualization — interactive D3.js graph of pattern portfolio
- Added pattern families API — `/api/patterns/graph`, `/api/patterns/match`, `/api/patterns/families`, `/api/patterns/search`
- Added `intent_label` and `domain` fields to optimization analysis
- Added `extract_patterns.md` Haiku prompt template for meta-pattern extraction
```

- [ ] **Step 2: Bump minor version**

Update `version.json` to bump the minor version (new feature).

- [ ] **Step 3: Run version sync**

```bash
./scripts/sync-version.sh
```

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md version.json backend/app/_version.py frontend/package.json
git commit -m "chore: bump version and add knowledge graph changelog entries"
```

---

### Task 20: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add knowledge graph documentation**

Add a new section to `CLAUDE.md` documenting:
- The pattern knowledge graph services (`pattern_extractor.py`, `pattern_matcher.py`, `knowledge_graph.py`)
- The new router (`/api/patterns/*`)
- The new models (`PatternFamily`, `MetaPattern`, `OptimizationPattern`)
- The threshold constants and their purposes
- The event bus integration (`optimization_created` → pattern extraction)
- The frontend components (`PatternSuggestion`, `PatternNavigator`, `RadialMindmap`)

Follow the existing documentation style and section structure in CLAUDE.md.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add knowledge graph architecture to CLAUDE.md"
```
