# Quality Feedback Loops + Session Resumption (H3) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the pipeline from a stateless single-shot system into an adaptive, session-aware optimization engine with user feedback loops, adaptive retries, and parallel branching.

**Architecture:** Two converging tracks — Track A (feedback data model, internal diagnostics, adaptation engine) and Track B (session resumption, unified refinement, branching) — meeting at the pipeline layer where feedback drives adaptation and refinement sessions accumulate conversational context. Seven backend chunks build bottom-up from data models through services to API endpoints, followed by frontend stores and components.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Hypothesis (property tests), SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4

**Spec:** `docs/superpowers/specs/2026-03-13-quality-feedback-loops-design.md`

---

## File Structure

### New Files (Backend)

| File | Responsibility |
|------|---------------|
| `backend/app/models/feedback.py` | `Feedback` + `UserAdaptation` ORM models |
| `backend/app/models/branch.py` | `RefinementBranch` + `PairwisePreference` ORM models |
| `backend/app/schemas/feedback.py` | Pydantic request/response schemas for feedback + adaptation |
| `backend/app/schemas/refinement.py` | Pydantic schemas for refinement + branching endpoints |
| `backend/app/services/retry_oracle.py` | 7-gate adaptive retry algorithm |
| `backend/app/services/adaptation_engine.py` | Feedback → adaptation recomputation |
| `backend/app/services/feedback_service.py` | Feedback CRUD + aggregation |
| `backend/app/services/refinement_service.py` | Unified refine operation + branch CRUD |
| `backend/app/services/session_context.py` | `SessionContext` dataclass + compaction logic |
| `backend/app/services/prompt_diff.py` | Prompt hashing, cycle detection, dimension deltas |
| `backend/app/routers/feedback.py` | Feedback API endpoints |
| `backend/app/routers/refinement.py` | Refinement + branching API endpoints |
| `backend/app/routers/_sse.py` | Shared `_sse_event()` formatter (extracted from optimize.py) |
| `backend/tests/test_retry_oracle.py` | RetryOracle unit + property tests |
| `backend/tests/test_adaptation_engine.py` | Adaptation engine unit + property tests |
| `backend/tests/test_feedback_service.py` | Feedback CRUD tests |
| `backend/tests/test_refinement_service.py` | Refinement + branch tests |
| `backend/tests/test_session_context.py` | Session context + compaction tests |
| `backend/tests/test_prompt_diff.py` | Hashing, cycle detection, delta tests |
| `backend/tests/test_feedback_api.py` | Feedback endpoint contract tests |
| `backend/tests/test_refinement_api.py` | Refinement endpoint contract tests |
| `backend/tests/test_pipeline_retry_oracle.py` | Pipeline integration with oracle |

### New Files (Frontend)

| File | Responsibility |
|------|---------------|
| `frontend/src/lib/stores/feedback.svelte.ts` | Feedback + adaptation state |
| `frontend/src/lib/stores/refinement.svelte.ts` | Branch + refinement session state |
| `frontend/src/lib/components/editor/FeedbackInline.svelte` | Compact feedback strip below result |
| `frontend/src/lib/components/editor/RefinementInput.svelte` | Expandable refinement well |
| `frontend/src/lib/components/pipeline/BranchIndicator.svelte` | Branch label + switcher |
| `frontend/src/lib/components/pipeline/BranchCompare.svelte` | Full comparison overlay |
| `frontend/src/lib/components/pipeline/RetryDiagnostics.svelte` | Oracle signal visualization |
| `frontend/src/lib/components/layout/InspectorFeedback.svelte` | Full feedback panel |
| `frontend/src/lib/components/layout/InspectorRefinement.svelte` | Turn history + session |
| `frontend/src/lib/components/layout/InspectorBranches.svelte` | Branch tree + compare + select |
| `frontend/src/lib/components/layout/InspectorAdaptation.svelte` | Learned weights transparency |

### Modified Files

| File | Changes |
|------|---------|
| `backend/app/models/optimization.py` | Add 7 new columns (retry_history, session_id, etc.) + JSON parse in to_dict |
| `backend/app/database.py` | Add new model imports to `create_tables()`, extend `_new_columns` + `_new_indexes` |
| `backend/app/config.py` | Add rate limit settings for feedback/refinement endpoints |
| `backend/app/providers/base.py` | Add `complete_with_session()` concrete method + `SessionContext` import |
| `backend/app/providers/anthropic_api.py` | Override `complete_with_session()` with message history replay |
| `backend/app/providers/claude_cli.py` | Override `complete_with_session()` with SDK resume |
| `backend/app/services/pipeline.py` | Replace retry loop with RetryOracle, create trunk branch, emit new SSE events |
| `backend/app/services/validator.py` | Accept optional `user_weights` for adapted scoring, add instruction compliance |
| `backend/app/services/strategy.py` | Accept optional `strategy_affinities` for soft bias |
| `backend/app/services/optimizer.py` | Accept retry context enrichment (priority dimensions, elasticity) |
| `backend/app/services/optimization_service.py` | Extend `VALID_SORT_COLUMNS`, extend `PipelineAccumulator` for new events |
| `backend/app/routers/optimize.py` | Extract `_sse_event()` to `_sse.py`, import from there |
| `backend/app/main.py` | Register feedback + refinement routers |
| `backend/app/mcp_server.py` | Add 3 new MCP tools |
| `backend/app/schemas/__init__.py` | Re-export new schemas |
| `frontend/src/lib/api/client.ts` | Add feedback, refinement, branch API functions |
| `frontend/src/lib/stores/forge.svelte.ts` | Handle new SSE events (retry_diagnostics, etc.) |
| `frontend/src/lib/components/editor/ForgeArtifact.svelte` | Mount FeedbackInline + RefinementInput |
| `frontend/src/lib/components/layout/Inspector.svelte` | Add feedback/refinement/branches/adaptation sections |

---

## Chunk 1: Data Foundation

Models, schemas, database migrations, and the prompt diff utility. No service logic yet — pure data layer.

### Task 1: Prompt Diff Utility

**Files:**
- Create: `backend/app/services/prompt_diff.py`
- Test: `backend/tests/test_prompt_diff.py`

- [ ] **Step 1: Write failing tests for prompt hashing**

```python
# backend/tests/test_prompt_diff.py
import pytest
from app.services.prompt_diff import compute_prompt_hash, compute_dimension_deltas, detect_cycle


class TestComputePromptHash:
    def test_identical_prompts_same_hash(self):
        h1 = compute_prompt_hash("Hello world")
        h2 = compute_prompt_hash("Hello world")
        assert h1 == h2

    def test_whitespace_normalized(self):
        h1 = compute_prompt_hash("Hello   world")
        h2 = compute_prompt_hash("Hello world")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = compute_prompt_hash("Hello World")
        h2 = compute_prompt_hash("hello world")
        assert h1 == h2

    def test_different_prompts_different_hash(self):
        h1 = compute_prompt_hash("Hello world")
        h2 = compute_prompt_hash("Goodbye world")
        assert h1 != h2

    def test_hash_length_is_16(self):
        h = compute_prompt_hash("test")
        assert len(h) == 16

    def test_empty_string(self):
        h = compute_prompt_hash("")
        assert len(h) == 16


class TestComputeDimensionDeltas:
    def test_basic_deltas(self):
        before = {"clarity_score": 6, "specificity_score": 5}
        after = {"clarity_score": 7, "specificity_score": 5}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["clarity_score"] == 1
        assert deltas["specificity_score"] == 0

    def test_negative_delta(self):
        before = {"structure_score": 7}
        after = {"structure_score": 5}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["structure_score"] == -2

    def test_missing_dimension_skipped(self):
        before = {"clarity_score": 6}
        after = {}
        deltas = compute_dimension_deltas(before, after)
        assert deltas == {}


class TestDetectCycle:
    def test_no_cycle_empty(self):
        assert detect_cycle("abc123", []) is None

    def test_no_cycle_unique(self):
        assert detect_cycle("abc123", ["def456", "ghi789"]) is None

    def test_cycle_detected(self):
        result = detect_cycle("abc123", ["def456", "abc123"])
        assert result == 2  # matching attempt number (1-indexed)

    def test_oscillation_detected(self):
        # A -> B -> A pattern
        result = detect_cycle("hash_a", ["hash_a", "hash_b"])
        assert result == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.prompt_diff'`

- [ ] **Step 3: Implement prompt_diff.py**

```python
# backend/app/services/prompt_diff.py
"""Prompt diff utilities: hashing, cycle detection, dimension deltas.

Used by RetryOracle and pipeline diagnostics.
"""

import hashlib
import re

SCORE_DIMENSIONS = (
    "clarity_score",
    "specificity_score",
    "structure_score",
    "faithfulness_score",
    "conciseness_score",
)


def compute_prompt_hash(prompt: str) -> str:
    """Normalized hash for cycle detection. Case-insensitive, whitespace-collapsed."""
    normalized = re.sub(r"\s+", " ", prompt.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def compute_dimension_deltas(
    before: dict[str, int | float],
    after: dict[str, int | float],
) -> dict[str, int | float]:
    """Compute per-dimension score changes between two validation results."""
    deltas: dict[str, int | float] = {}
    for dim in SCORE_DIMENSIONS:
        b = before.get(dim)
        a = after.get(dim)
        if b is not None and a is not None:
            deltas[dim] = a - b
    return deltas


def detect_cycle(
    current_hash: str,
    previous_hashes: list[str],
) -> int | None:
    """Check if current prompt hash matches any previous attempt.

    Returns 1-indexed attempt number of the match, or None if no cycle.
    """
    for i, h in enumerate(previous_hashes):
        if h == current_hash:
            return i + 1
    return None


def compute_prompt_entropy(prompt_a: str, prompt_b: str) -> float:
    """Jaccard similarity on sentence-level tokens.

    Returns 0.0 (identical) to 1.0 (completely different).
    Higher = more exploration.
    """
    def _sentences(text: str) -> set[str]:
        parts = re.split(r"[.!?\n]+", text.strip().lower())
        return {re.sub(r"\s+", " ", s.strip()) for s in parts if s.strip()}

    a_set = _sentences(prompt_a)
    b_set = _sentences(prompt_b)

    if not a_set and not b_set:
        return 0.0

    intersection = a_set & b_set
    union = a_set | b_set

    if not union:
        return 0.0

    similarity = len(intersection) / len(union)
    return round(1.0 - similarity, 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_diff.py -v`
Expected: All PASS

- [ ] **Step 5: Add entropy tests**

Append to `backend/tests/test_prompt_diff.py`:

```python
from app.services.prompt_diff import compute_prompt_entropy


class TestComputePromptEntropy:
    def test_identical_prompts_zero_entropy(self):
        e = compute_prompt_entropy("Hello world.", "Hello world.")
        assert e == 0.0

    def test_completely_different_prompts_high_entropy(self):
        e = compute_prompt_entropy(
            "The cat sat on the mat.",
            "A completely unrelated sentence about quantum physics.",
        )
        assert e > 0.5

    def test_empty_prompts_zero_entropy(self):
        e = compute_prompt_entropy("", "")
        assert e == 0.0

    def test_entropy_bounded_zero_to_one(self):
        e = compute_prompt_entropy("One sentence.", "Another sentence.")
        assert 0.0 <= e <= 1.0
```

- [ ] **Step 6: Run all prompt_diff tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_diff.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/prompt_diff.py backend/tests/test_prompt_diff.py
git commit -m "feat: add prompt diff utility (hashing, deltas, cycle detection, entropy)"
```

---

### Task 2: Feedback + UserAdaptation Models

**Files:**
- Create: `backend/app/models/feedback.py`
- Modify: `backend/app/database.py:205-213` (model imports in `create_tables`)

- [ ] **Step 1: Write model file**

```python
# backend/app/models/feedback.py
"""Feedback and user adaptation ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, Text, UniqueConstraint

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    optimization_id = Column(Text, ForeignKey("optimizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Text, nullable=False)
    rating = Column(SmallInteger, nullable=False)  # -1, 0, +1
    dimension_overrides = Column(Text, nullable=True)  # JSON
    corrected_issues = Column(Text, nullable=True)  # JSON array
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("optimization_id", "user_id", name="uq_feedback_opt_user"),
        Index("ix_feedback_user_created", "user_id", "created_at"),
    )


class UserAdaptation(Base):
    __tablename__ = "user_adaptation"

    user_id = Column(Text, primary_key=True)
    dimension_weights = Column(Text, nullable=True)  # JSON
    strategy_affinities = Column(Text, nullable=True)  # JSON
    retry_threshold = Column(Float, default=5.0)
    feedback_count = Column(Integer, default=0)
    last_computed_at = Column(DateTime, default=_utcnow)
```

- [ ] **Step 2: Add model import to create_tables()**

In `backend/app/database.py`, add after line 213 (`import app.models.repo_index`):

```python
    import app.models.feedback  # noqa: F401
```

- [ ] **Step 3: Verify model loads**

Run: `cd backend && source .venv/bin/activate && python -c "from app.models.feedback import Feedback, UserAdaptation; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/feedback.py backend/app/database.py
git commit -m "feat: add Feedback and UserAdaptation ORM models"
```

---

### Task 3: RefinementBranch + PairwisePreference Models

**Files:**
- Create: `backend/app/models/branch.py`
- Modify: `backend/app/database.py:205-214` (model imports)

- [ ] **Step 1: Write model file**

```python
# backend/app/models/branch.py
"""Refinement branch and pairwise preference ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Text

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class RefinementBranch(Base):
    __tablename__ = "refinement_branch"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    optimization_id = Column(Text, ForeignKey("optimizations.id", ondelete="CASCADE"), nullable=False)
    parent_branch_id = Column(Text, ForeignKey("refinement_branch.id", ondelete="SET NULL"), nullable=True)
    forked_at_turn = Column(Integer, nullable=True)
    label = Column(Text, nullable=False, default="trunk")
    optimized_prompt = Column(Text, nullable=True)
    scores = Column(Text, nullable=True)  # JSON
    session_context = Column(Text, nullable=True)  # JSON (SessionContext)
    turn_count = Column(Integer, default=0)
    turn_history = Column(Text, default="[]")  # JSON array
    status = Column(Text, default="active", nullable=False)  # active | selected | abandoned
    row_version = Column(Integer, nullable=False, server_default="0", default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_branch_optimization", "optimization_id"),
        Index("ix_branch_opt_status", "optimization_id", "status"),
    )


class PairwisePreference(Base):
    __tablename__ = "pairwise_preference"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    optimization_id = Column(Text, ForeignKey("optimizations.id", ondelete="CASCADE"), nullable=False)
    preferred_branch_id = Column(Text, ForeignKey("refinement_branch.id", ondelete="SET NULL"), nullable=True)
    rejected_branch_id = Column(Text, ForeignKey("refinement_branch.id", ondelete="SET NULL"), nullable=True)
    preferred_scores = Column(Text, nullable=True)  # JSON
    rejected_scores = Column(Text, nullable=True)  # JSON
    user_id = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_pairwise_user", "user_id"),
        Index("ix_pairwise_optimization", "optimization_id"),
        Index("ix_pairwise_user_created", "user_id", "created_at"),
    )
```

- [ ] **Step 2: Add model import to create_tables()**

In `backend/app/database.py`, add after the feedback import:

```python
    import app.models.branch  # noqa: F401
```

- [ ] **Step 3: Verify model loads**

Run: `cd backend && source .venv/bin/activate && python -c "from app.models.branch import RefinementBranch, PairwisePreference; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/branch.py backend/app/database.py
git commit -m "feat: add RefinementBranch and PairwisePreference ORM models"
```

---

### Task 4: Optimization Model Extensions

**Files:**
- Modify: `backend/app/models/optimization.py` (add 7 new columns + JSON parsing in to_dict)
- Modify: `backend/app/database.py:89-130` (extend `_new_columns`)
- Modify: `backend/app/database.py:173-183` (extend `_new_indexes`)

- [ ] **Step 1: Add new columns to Optimization model**

In `backend/app/models/optimization.py`, add after line 98 (`codebase_context_snapshot`):

```python
    # H3: Quality feedback loops + session resumption
    retry_history = Column(Text, nullable=True)  # JSON array
    per_instruction_compliance = Column(Text, nullable=True)  # JSON array
    session_id = Column(Text, nullable=True)
    refinement_turns = Column(Integer, default=0)
    active_branch_id = Column(Text, nullable=True)  # app-layer FK to refinement_branch
    branch_count = Column(Integer, default=0)
    adaptation_snapshot = Column(Text, nullable=True)  # JSON
```

- [ ] **Step 2: Add JSON parse support in to_dict for new columns**

In `backend/app/models/optimization.py`, inside `to_dict()`, add the new JSON-as-TEXT columns to the list parse and dict parse blocks.

**Insert BEFORE the `result[col.name] = value` line (line 144)** — this is critical because that line is the final assignment inside the column loop. Inserting after it would discard the parsed values:

```python
            # Parse H3 JSON columns (list-type)
            if col.name in ("retry_history", "per_instruction_compliance"):
                if value and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        value = []
            # Parse H3 JSON columns (dict-type)
            if col.name == "adaptation_snapshot":
                if value and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        value = None
```

- [ ] **Step 3: Add columns to _new_columns migration dict**

In `backend/app/database.py`, inside `_new_columns["optimizations"]` (after line 110):

```python
            "retry_history": "TEXT",
            "per_instruction_compliance": "TEXT",
            "session_id": "TEXT",
            "refinement_turns": "INTEGER DEFAULT 0",
            "active_branch_id": "TEXT",
            "branch_count": "INTEGER DEFAULT 0",
            "adaptation_snapshot": "TEXT",
```

- [ ] **Step 4: Add indexes to _new_indexes list**

In `backend/app/database.py`, append to `_new_indexes` list (after line 182).

**Note:** These new tables already get their ORM-declared indexes from `Base.metadata.create_all()`. The `_new_indexes` entries are harmless (existence-checked before creation) but provide a safety net for edge cases where `create_all` doesn't create indexes on pre-existing tables. Adding a comment in the code clarifies this:

```python
        # H3 tables — indexes also defined in ORM __table_args__ (safety net for migrations)
        ("ix_feedback_user_created", "feedback", "user_id, created_at"),
        ("ix_branch_optimization", "refinement_branch", "optimization_id"),
        ("ix_branch_opt_status", "refinement_branch", "optimization_id, status"),
        ("ix_pairwise_user", "pairwise_preference", "user_id"),
        ("ix_pairwise_optimization", "pairwise_preference", "optimization_id"),
        ("ix_pairwise_user_created", "pairwise_preference", "user_id, created_at"),
```

- [ ] **Step 5: Extend sort whitelist**

In `backend/app/services/optimization_service.py:196-199`, add to `VALID_SORT_COLUMNS`:

```python
VALID_SORT_COLUMNS: frozenset[str] = frozenset({
    "created_at", "overall_score", "task_type", "updated_at",
    "duration_ms", "primary_framework", "status",
    "refinement_turns", "branch_count",
})
```

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/optimization.py backend/app/database.py backend/app/services/optimization_service.py
git commit -m "feat: add H3 columns to Optimization model + migration entries + sort whitelist"
```

---

### Task 5: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/feedback.py`
- Create: `backend/app/schemas/refinement.py`
- Modify: `backend/app/schemas/__init__.py`

- [ ] **Step 1: Write feedback schemas**

```python
# backend/app/schemas/feedback.py
"""Pydantic schemas for feedback and adaptation endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

VALID_DIMENSIONS = {
    "clarity_score", "specificity_score", "structure_score",
    "faithfulness_score", "conciseness_score",
}


class FeedbackCreate(BaseModel):
    rating: Literal[-1, 0, 1]
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def validate_dimension_overrides(self) -> "FeedbackCreate":
        if self.dimension_overrides:
            for key, value in self.dimension_overrides.items():
                if key not in VALID_DIMENSIONS:
                    raise ValueError(f"Invalid dimension: {key}")
                if not (1 <= value <= 10):
                    raise ValueError(f"Score must be 1-10, got {value} for {key}")
        return self


class DimensionDelta(BaseModel):
    """Per-dimension score change between retry attempts."""
    dimension: str
    before: int
    after: int
    delta: int


class RetryHistoryEntry(BaseModel):
    """One entry in the retry_history JSON array on Optimization."""
    attempt: int
    scores: dict[str, int | float]
    focus_areas: list[str]
    dimension_deltas: dict[str, int] = {}
    prompt_hash: str = ""


class InstructionCompliance(BaseModel):
    """Per-instruction compliance from validation."""
    instruction: str
    satisfied: bool
    note: str | None = None


class FeedbackResponse(BaseModel):
    id: str
    optimization_id: str
    user_id: str
    rating: int
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = None
    comment: str | None = None
    created_at: str


class FeedbackAggregate(BaseModel):
    total_ratings: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    avg_dimension_overrides: dict[str, float] | None = None


class FeedbackWithAggregate(BaseModel):
    feedback: FeedbackResponse | None = None
    aggregate: FeedbackAggregate


class FeedbackStatsResponse(BaseModel):
    total_feedbacks: int
    rating_distribution: dict[str, int]
    avg_override_delta: dict[str, float] | None
    most_corrected_dimension: str | None
    adaptation_state: dict | None


class AdaptationStateResponse(BaseModel):
    dimension_weights: dict[str, float] | None = None
    strategy_affinities: dict | None = None
    retry_threshold: float = 5.0
    feedback_count: int = 0
```

- [ ] **Step 2: Write refinement schemas**

```python
# backend/app/schemas/refinement.py
"""Pydantic schemas for refinement and branching endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RefineRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    protect_dimensions: list[str] | None = None


class ForkRequest(BaseModel):
    parent_branch_id: str
    message: str = Field(..., min_length=1, max_length=2000)
    label: str | None = None


class SelectRequest(BaseModel):
    branch_id: str
    reason: str | None = None


class BranchResponse(BaseModel):
    id: str
    optimization_id: str
    parent_branch_id: str | None
    label: str
    optimized_prompt: str | None
    scores: dict | None
    turn_count: int
    status: str
    created_at: str
    updated_at: str | None


class BranchListResponse(BaseModel):
    branches: list[BranchResponse]
    total: int


class BranchCompareResponse(BaseModel):
    branch_a: BranchResponse
    branch_b: BranchResponse
    score_deltas: dict[str, float]
```

- [ ] **Step 3: Update schemas __init__.py**

In `backend/app/schemas/__init__.py`, add imports for new schemas AND extend the `__all__` list (the existing file uses an explicit `__all__`):

```python
from app.schemas.feedback import (
    AdaptationStateResponse,
    DimensionDelta,
    FeedbackAggregate,
    FeedbackCreate,
    FeedbackResponse,
    FeedbackStatsResponse,
    FeedbackWithAggregate,
    InstructionCompliance,
    RetryHistoryEntry,
)
from app.schemas.refinement import (
    BranchCompareResponse,
    BranchListResponse,
    BranchResponse,
    ForkRequest,
    RefineRequest,
    SelectRequest,
)
```

Also append to the `__all__` list:

```python
    # H3: Quality feedback loops
    "AdaptationStateResponse",
    "DimensionDelta",
    "FeedbackAggregate",
    "FeedbackCreate",
    "FeedbackResponse",
    "FeedbackStatsResponse",
    "FeedbackWithAggregate",
    "InstructionCompliance",
    "RetryHistoryEntry",
    "BranchCompareResponse",
    "BranchListResponse",
    "BranchResponse",
    "ForkRequest",
    "RefineRequest",
    "SelectRequest",
```

- [ ] **Step 4: Verify schemas import cleanly**

Run: `cd backend && source .venv/bin/activate && python -c "from app.schemas import FeedbackCreate, RefineRequest; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/feedback.py backend/app/schemas/refinement.py backend/app/schemas/__init__.py
git commit -m "feat: add Pydantic schemas for feedback + refinement endpoints"
```

---

### Task 6: SSE Event Formatter Extraction

**Files:**
- Create: `backend/app/routers/_sse.py`
- Modify: `backend/app/routers/optimize.py:25-51` (import from _sse.py, remove local copy)

- [ ] **Step 1: Create shared SSE formatter**

```python
# backend/app/routers/_sse.py
"""Shared SSE event formatting for streaming endpoints."""

import json
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


def _default_serializer(obj: object) -> str:
    """Fallback JSON serializer for SSE payloads."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Exception):
        return str(obj)
    return repr(obj)


def sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event with safe JSON serialization.

    Uses a fallback serializer so that non-serializable values (datetimes,
    exceptions, etc.) never crash the stream silently.
    """
    try:
        payload = json.dumps(data, default=_default_serializer)
    except Exception as e:
        logger.error("SSE serialization failed for event %s: %s", event_type, e)
        payload = json.dumps({"error": f"Serialization error: {e}"})
    return f"event: {event_type}\ndata: {payload}\n\n"
```

- [ ] **Step 2: Update optimize.py to import from _sse.py**

In `backend/app/routers/optimize.py`:
- Add import: `from app.routers._sse import sse_event`
- Remove the local `_default_serializer()` function (lines ~30-37)
- Remove the local `_sse_event()` function (lines ~40-51)
- Replace all `_sse_event(` calls with `sse_event(`

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/_sse.py backend/app/routers/optimize.py
git commit -m "refactor: extract SSE event formatter to shared _sse.py module"
```

---

### Task 7: Config Extensions

**Files:**
- Modify: `backend/app/config.py` (add rate limit settings)

- [ ] **Step 1: Add rate limit settings**

In `backend/app/config.py`, after the existing rate limit settings:

```python
    RATE_LIMIT_FEEDBACK: str = "10/minute"
    RATE_LIMIT_REFINE: str = "5/minute"
    RATE_LIMIT_BRANCH_FORK: str = "3/minute"
    RATE_LIMIT_BRANCH_SELECT: str = "10/minute"
```

- [ ] **Step 2: Verify config loads**

Run: `cd backend && source .venv/bin/activate && python -c "from app.config import settings; print(settings.RATE_LIMIT_FEEDBACK)"`
Expected: `10/minute`

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add rate limit settings for feedback and refinement endpoints"
```

---

## Chunk 2: RetryOracle

The 7-gate adaptive retry algorithm. Depends on Chunk 1 (prompt_diff.py). Pure service logic — no API endpoints yet.

### Task 8: RetryOracle Core — Signals and State

**Files:**
- Create: `backend/app/services/retry_oracle.py`
- Test: `backend/tests/test_retry_oracle.py`

- [ ] **Step 1: Write failing tests for oracle initialization and recording**

```python
# backend/tests/test_retry_oracle.py
"""Tests for the 7-gate adaptive RetryOracle."""

import pytest
from app.services.retry_oracle import RetryOracle, RetryDecision


class TestOracleInit:
    def test_default_threshold(self):
        oracle = RetryOracle(max_retries=3)
        assert oracle.threshold == 5.0

    def test_custom_threshold(self):
        oracle = RetryOracle(max_retries=3, threshold=6.5)
        assert oracle.threshold == 6.5

    def test_threshold_clamped_low(self):
        oracle = RetryOracle(max_retries=3, threshold=1.0)
        assert oracle.threshold == 3.0

    def test_threshold_clamped_high(self):
        oracle = RetryOracle(max_retries=3, threshold=9.5)
        assert oracle.threshold == 8.0


class TestRecordAttempt:
    def test_first_attempt_recorded(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"clarity_score": 6, "specificity_score": 5, "structure_score": 7,
                    "faithfulness_score": 4, "conciseness_score": 8, "overall_score": 5.8},
            prompt="Test prompt",
            focus_areas=[],
        )
        assert oracle.attempt_count == 1

    def test_multiple_attempts(self):
        oracle = RetryOracle(max_retries=3)
        for i in range(3):
            oracle.record_attempt(
                scores={"overall_score": 4.0 + i},
                prompt=f"Prompt version {i}",
                focus_areas=[],
            )
        assert oracle.attempt_count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_retry_oracle.py::TestOracleInit -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement oracle skeleton with constants, init, and record_attempt**

```python
# backend/app/services/retry_oracle.py
"""7-gate adaptive RetryOracle.

Replaces the fixed LOW_SCORE_THRESHOLD retry logic with a stateful oracle
that tracks five real-time signals across attempts within a single pipeline run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.services.prompt_diff import (
    SCORE_DIMENSIONS,
    compute_dimension_deltas,
    compute_prompt_entropy,
    compute_prompt_hash,
    detect_cycle,
)

logger = logging.getLogger(__name__)

# ── Configurable constants ───────────────────────────────────────────
ENTROPY_EXHAUSTION_THRESHOLD = 0.15
ENTROPY_EXPLORATION_THRESHOLD = 0.40
REGRESSION_RATIO_THRESHOLD = 0.40
ELASTICITY_HIGH = 0.60
ELASTICITY_LOW = 0.30
FOCUS_EFFECTIVENESS_LOW = 0.30
MOMENTUM_NEGATIVE_THRESHOLD = -0.30
MOMENTUM_DECAY_FACTOR = 0.70
DIMINISHING_RETURNS_BASE = 0.50
DIMINISHING_RETURNS_GROWTH = 1.30
THRESHOLD_LOWER_BOUND = 3.0
THRESHOLD_UPPER_BOUND = 8.0
DEFAULT_THRESHOLD = 5.0


@dataclass
class RetryDecision:
    """Result from should_retry()."""
    action: str  # "accept" | "accept_best" | "retry"
    reason: str
    focus_areas: list[str] = field(default_factory=list)
    best_attempt: int | None = None  # 0-indexed


@dataclass
class _Attempt:
    """Internal record of one pipeline attempt."""
    scores: dict
    overall_score: float
    prompt_hash: str
    focus_areas: list[str]
    dimension_deltas: dict[str, float] = field(default_factory=dict)


class RetryOracle:
    """Stateful retry decision engine.

    Instantiated once per pipeline run. Records attempts and decides
    whether to retry based on 7 gates.
    """

    def __init__(
        self,
        max_retries: int = 1,
        threshold: float = DEFAULT_THRESHOLD,
        user_weights: dict[str, float] | None = None,
        task_baseline: float | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.threshold = max(THRESHOLD_LOWER_BOUND, min(THRESHOLD_UPPER_BOUND, threshold))
        self.user_weights = user_weights
        self.task_baseline = task_baseline
        self._attempts: list[_Attempt] = []
        # Per-dimension tracking
        self._elasticity: dict[str, list[bool]] = {d: [] for d in SCORE_DIMENSIONS}
        self._focus_history: list[list[str]] = []
        self._last_entropy: float = 1.0  # default: assume exploring on first attempt
        self._last_prompt: str = ""  # last prompt text for entropy computation

    @property
    def attempt_count(self) -> int:
        return len(self._attempts)

    @property
    def best_attempt_index(self) -> int:
        """Index of the attempt with the highest weighted overall score."""
        if not self._attempts:
            return 0
        if self.user_weights:
            scores = []
            for a in self._attempts:
                weighted = sum(
                    a.scores.get(d, 0) * self.user_weights.get(d, 0)
                    for d in SCORE_DIMENSIONS
                )
                total_w = sum(self.user_weights.get(d, 0) for d in SCORE_DIMENSIONS)
                scores.append(weighted / total_w if total_w > 0 else a.overall_score)
            return max(range(len(scores)), key=lambda i: scores[i])
        return max(range(len(self._attempts)), key=lambda i: self._attempts[i].overall_score)

    def record_attempt(
        self,
        scores: dict,
        prompt: str,
        focus_areas: list[str],
    ) -> None:
        """Record the results of an optimization attempt."""
        prompt_hash = compute_prompt_hash(prompt)
        overall = scores.get("overall_score", 0.0)
        if not isinstance(overall, (int, float)):
            overall = 0.0

        deltas: dict[str, float] = {}
        if self._attempts:
            prev = self._attempts[-1]
            deltas = compute_dimension_deltas(prev.scores, scores)
            # Update elasticity tracking
            for dim in SCORE_DIMENSIONS:
                if dim in (focus_areas or []):
                    improved = deltas.get(dim, 0) > 0
                    self._elasticity[dim].append(improved)

        # Compute entropy between consecutive prompts
        if self._last_prompt:
            self._last_entropy = compute_prompt_entropy(self._last_prompt, prompt)
        self._last_prompt = prompt

        self._attempts.append(_Attempt(
            scores=scores,
            overall_score=float(overall),
            prompt_hash=prompt_hash,
            focus_areas=focus_areas,
            dimension_deltas=deltas,
        ))
        self._focus_history.append(focus_areas or [])

    def _compute_momentum(self) -> float:
        """Exponentially weighted moving delta of overall scores."""
        if len(self._attempts) < 2:
            return 0.0
        deltas = []
        for i in range(1, len(self._attempts)):
            deltas.append(self._attempts[i].overall_score - self._attempts[i - 1].overall_score)
        if not deltas:
            return 0.0
        weighted_sum = 0.0
        weight_total = 0.0
        for i, d in enumerate(reversed(deltas)):
            w = MOMENTUM_DECAY_FACTOR ** i
            weighted_sum += d * w
            weight_total += w
        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def _compute_entropy(self) -> float:
        """Prompt entropy between last two attempts. Higher = more exploration."""
        if len(self._attempts) < 2:
            return 1.0  # assume exploring on first attempt
        # We store prompt_hash, but need actual prompts for entropy
        # Since we don't store full prompts, use hash collision as proxy
        # For proper entropy, the pipeline passes prompts — we cache last two
        return self._last_entropy

    def _compute_regression_ratio(self) -> float:
        """Fraction of dimensions that degraded on the last attempt."""
        if len(self._attempts) < 2:
            return 0.0
        deltas = self._attempts[-1].dimension_deltas
        if not deltas:
            return 0.0
        degraded = sum(1 for v in deltas.values() if v < 0)
        return degraded / len(deltas)

    def _compute_focus_effectiveness(self) -> float:
        """Fraction of focused dimensions that improved."""
        if len(self._attempts) < 2:
            return 1.0
        last = self._attempts[-1]
        focus = last.focus_areas
        if not focus:
            return 1.0
        improved = sum(1 for d in focus if last.dimension_deltas.get(d, 0) > 0)
        return improved / len(focus)

    def _get_elasticity(self, dim: str) -> float:
        """Ratio of successful improvements when targeted for a dimension."""
        history = self._elasticity.get(dim, [])
        if not history:
            return 0.5  # unknown
        return sum(history) / len(history)
```

- [ ] **Step 4: Run init and record tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_retry_oracle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit skeleton**

```bash
git add backend/app/services/retry_oracle.py backend/tests/test_retry_oracle.py
git commit -m "feat: add RetryOracle skeleton with state tracking and signal computation"
```

---

### Task 9: RetryOracle — 7 Gates and Decision Logic

**Files:**
- Modify: `backend/app/services/retry_oracle.py` (add should_retry, _select_focus, build_diagnostic_message)
- Extend: `backend/tests/test_retry_oracle.py`

- [ ] **Step 1: Write failing tests for each gate**

Append to `backend/tests/test_retry_oracle.py`:

```python
class TestGate1ScoreAboveThreshold:
    def test_accept_when_score_above_threshold(self):
        oracle = RetryOracle(max_retries=3, threshold=5.0)
        oracle.record_attempt(
            scores={"overall_score": 7.0, "clarity_score": 7, "specificity_score": 7,
                    "structure_score": 7, "faithfulness_score": 7, "conciseness_score": 7},
            prompt="Good prompt", focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "accept"
        assert "threshold" in decision.reason.lower()


class TestGate2BudgetExhausted:
    def test_accept_best_when_max_retries_reached(self):
        oracle = RetryOracle(max_retries=1, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V2", focus_areas=["clarity_score"])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "budget" in decision.reason.lower()


class TestGate3CycleDetected:
    def test_accept_best_on_cycle(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="same prompt", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 4.5}, prompt="same prompt", focus_areas=[])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "cycle" in decision.reason.lower()


class TestGate4CreativeExhaustion:
    def test_accept_best_on_low_entropy(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="same prompt here", focus_areas=[])
        # Nearly identical prompt → entropy < 0.15
        oracle.record_attempt(scores={"overall_score": 4.5}, prompt="same prompt here.", focus_areas=[])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "exhaustion" in decision.reason.lower() or "cycle" in decision.reason.lower()


class TestGate5NegativeMomentum:
    def test_accept_best_on_declining_scores(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1 prompt text", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V2 very different prompt text", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="V3 completely new approach here", focus_areas=[])
        decision = oracle.should_retry()
        # Strongly negative momentum should trigger gate 5 or 7
        assert decision.action == "accept_best"


class TestGate6ZeroSumTrap:
    def test_accept_best_on_consecutive_regressions(self):
        oracle = RetryOracle(max_retries=10, threshold=8.0)
        # Attempt 1: baseline
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 5, "specificity_score": 5,
                    "structure_score": 5, "faithfulness_score": 5, "conciseness_score": 5},
            prompt="V1 original prompt", focus_areas=[],
        )
        # Attempt 2: some up, some down (>40% regressed)
        oracle.record_attempt(
            scores={"overall_score": 5.1, "clarity_score": 7, "specificity_score": 3,
                    "structure_score": 3, "faithfulness_score": 7, "conciseness_score": 3},
            prompt="V2 different approach", focus_areas=[],
        )
        # Attempt 3: again some up, some down (>40% regressed)
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 4, "specificity_score": 6,
                    "structure_score": 6, "faithfulness_score": 3, "conciseness_score": 6},
            prompt="V3 yet another approach", focus_areas=[],
        )
        decision = oracle.should_retry()
        # Two consecutive attempts with >40% regression ratio → gate 6
        assert decision.action == "accept_best"


class TestGate7DiminishingReturns:
    def test_accept_best_when_expected_gain_too_low(self):
        oracle = RetryOracle(max_retries=10, threshold=8.0)
        # Record many attempts with tiny improvements (0.1 each)
        # After 5 attempts, diminishing returns threshold = 0.5 * 1.3^4 ≈ 1.43
        # Momentum ≈ 0.1, well below 1.43 → gate 7 fires "accept_best"
        for i in range(5):
            oracle.record_attempt(
                scores={"overall_score": 4.0 + i * 0.1},
                prompt=f"Version {i} with unique content to avoid cycle detection number {i}",
                focus_areas=[],
            )
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "diminishing" in decision.reason.lower()


class TestBestOfNSelection:
    def test_best_attempt_is_highest_score(self):
        oracle = RetryOracle(max_retries=5)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 7.5}, prompt="V2 unique", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V3 unique different", focus_areas=[])
        assert oracle.best_attempt_index == 1

    def test_best_attempt_with_user_weights(self):
        weights = {"clarity_score": 0.40, "specificity_score": 0.15, "structure_score": 0.15,
                   "faithfulness_score": 0.15, "conciseness_score": 0.15}
        oracle = RetryOracle(max_retries=5, user_weights=weights)
        # V1: high clarity but low others
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 9, "specificity_score": 3,
                    "structure_score": 3, "faithfulness_score": 3, "conciseness_score": 3},
            prompt="V1", focus_areas=[],
        )
        # V2: balanced but lower clarity
        oracle.record_attempt(
            scores={"overall_score": 6.0, "clarity_score": 5, "specificity_score": 6,
                    "structure_score": 6, "faithfulness_score": 6, "conciseness_score": 6},
            prompt="V2 unique", focus_areas=[],
        )
        # With 40% clarity weight, V1 should win despite lower overall
        assert oracle.best_attempt_index == 0


class TestFocusSelection:
    def test_focus_returns_lowest_elastic_dimensions(self):
        oracle = RetryOracle(max_retries=5)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3, "specificity_score": 7,
                    "structure_score": 5, "faithfulness_score": 4, "conciseness_score": 6},
            prompt="V1", focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "retry"
        # Focus should target lowest dimensions
        assert "clarity_score" in decision.focus_areas


class TestDiagnosticMessage:
    def test_builds_message_string(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3, "faithfulness_score": 4},
            prompt="V1", focus_areas=[],
        )
        msg = oracle.build_diagnostic_message(["clarity_score", "faithfulness_score"])
        assert "clarity" in msg.lower()
        assert "faithfulness" in msg.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_retry_oracle.py::TestGate1ScoreAboveThreshold -v`
Expected: FAIL — `AttributeError: 'RetryOracle' object has no attribute 'should_retry'`

- [ ] **Step 3: Implement should_retry, _select_focus, build_diagnostic_message**

Add to `RetryOracle` class in `backend/app/services/retry_oracle.py` (note: `_last_entropy` and `_last_prompt` are already initialized in `__init__`, and entropy is computed in `record_attempt` from the skeleton in Task 8):

```python
    def should_retry(self) -> RetryDecision:
        """7-gate decision algorithm."""
        if not self._attempts:
            return RetryDecision(action="retry", reason="No attempts yet")

        latest = self._attempts[-1]

        # Gate 1: Score >= adapted threshold → ACCEPT
        if latest.overall_score >= self.threshold:
            return RetryDecision(action="accept", reason=f"Score {latest.overall_score} >= threshold {self.threshold}")

        # Gate 2: Budget exhausted → ACCEPT_BEST
        if self.attempt_count > self.max_retries:
            return RetryDecision(
                action="accept_best",
                reason=f"Budget exhausted ({self.attempt_count} attempts, max {self.max_retries + 1})",
                best_attempt=self.best_attempt_index,
            )

        # Gate 3: Cycle detected → ACCEPT_BEST
        if self.attempt_count >= 2:
            previous_hashes = [a.prompt_hash for a in self._attempts[:-1]]
            cycle_match = detect_cycle(latest.prompt_hash, previous_hashes)
            if cycle_match is not None:
                return RetryDecision(
                    action="accept_best",
                    reason=f"Cycle detected: attempt {self.attempt_count} matches attempt {cycle_match}",
                    best_attempt=self.best_attempt_index,
                )

        # Gate 4: Creative exhaustion → ACCEPT_BEST
        entropy = self._last_entropy if self.attempt_count >= 2 else 1.0
        if entropy < ENTROPY_EXHAUSTION_THRESHOLD and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Creative exhaustion: entropy {entropy:.3f} < {ENTROPY_EXHAUSTION_THRESHOLD}",
                best_attempt=self.best_attempt_index,
            )

        # Gate 5: Negative momentum → ACCEPT_BEST
        momentum = self._compute_momentum()
        if momentum < MOMENTUM_NEGATIVE_THRESHOLD and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Negative momentum: {momentum:.3f} < {MOMENTUM_NEGATIVE_THRESHOLD}",
                best_attempt=self.best_attempt_index,
            )

        # Gate 6: Zero-sum trap → ACCEPT_BEST
        if self.attempt_count >= 3:
            r1 = self._attempts[-1].dimension_deltas
            r2 = self._attempts[-2].dimension_deltas
            if r1 and r2:
                ratio_1 = sum(1 for v in r1.values() if v < 0) / max(len(r1), 1)
                ratio_2 = sum(1 for v in r2.values() if v < 0) / max(len(r2), 1)
                if ratio_1 > REGRESSION_RATIO_THRESHOLD and ratio_2 > REGRESSION_RATIO_THRESHOLD:
                    return RetryDecision(
                        action="accept_best",
                        reason=f"Zero-sum trap: regression ratio {ratio_1:.2f}, {ratio_2:.2f}",
                        best_attempt=self.best_attempt_index,
                    )

        # Gate 7: Diminishing returns → ACCEPT_BEST
        min_expected_gain = DIMINISHING_RETURNS_BASE * (DIMINISHING_RETURNS_GROWTH ** (self.attempt_count - 1))
        if momentum < min_expected_gain and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Diminishing returns: momentum {momentum:.3f} < expected {min_expected_gain:.3f}",
                best_attempt=self.best_attempt_index,
            )

        # All gates passed → RETRY
        focus = self._select_focus()
        return RetryDecision(action="retry", reason="All gates passed", focus_areas=focus)

    def _select_focus(self) -> list[str]:
        """Select dimensions to focus the next retry on."""
        if not self._attempts:
            return []

        # If last two retries had low focus effectiveness → go unconstrained
        if self.attempt_count >= 3:
            eff_1 = self._compute_focus_effectiveness()
            if eff_1 < FOCUS_EFFECTIVENESS_LOW:
                logger.info("Focus effectiveness %.2f < %.2f, going unconstrained", eff_1, FOCUS_EFFECTIVENESS_LOW)
                return []

        latest = self._attempts[-1]
        # Rank dimensions by score (lowest first), filter by elasticity
        dim_scores = []
        for dim in SCORE_DIMENSIONS:
            score = latest.scores.get(dim)
            if score is not None:
                elasticity = self._get_elasticity(dim)
                if elasticity >= ELASTICITY_LOW:  # skip inelastic dimensions
                    dim_scores.append((dim, score, elasticity))

        dim_scores.sort(key=lambda x: x[1])  # lowest score first
        # Return top 2 lowest-scoring elastic dimensions
        return [d[0] for d in dim_scores[:2]]

    def build_diagnostic_message(self, focus_areas: list[str]) -> str:
        """Build a diagnostic message for the optimizer as a refinement turn."""
        if not self._attempts:
            return "Improve the prompt."

        latest = self._attempts[-1]
        parts = ["The previous optimization attempt scored:"]

        for dim in SCORE_DIMENSIONS:
            score = latest.scores.get(dim)
            if score is not None:
                delta = latest.dimension_deltas.get(dim)
                delta_str = f" ({'+' if delta and delta > 0 else ''}{delta})" if delta else ""
                parts.append(f"  - {dim.replace('_score', '')}: {score}/10{delta_str}")

        if focus_areas:
            focus_names = [f.replace("_score", "") for f in focus_areas]
            parts.append(f"\nFocus on improving: {', '.join(focus_names)}.")

        momentum = self._compute_momentum()
        if momentum < 0.2:
            parts.append("Try a structurally different approach rather than incremental refinement.")

        return "\n".join(parts)

    def get_diagnostics(self) -> dict:
        """Return diagnostic data for SSE events."""
        return {
            "attempt": self.attempt_count,
            "momentum": round(self._compute_momentum(), 3),
            "entropy": round(self._last_entropy, 3) if self.attempt_count >= 2 else None,
            "regression_ratio": round(self._compute_regression_ratio(), 3),
            "focus_effectiveness": round(self._compute_focus_effectiveness(), 3),
            "best_attempt": self.best_attempt_index,
            "best_score": self._attempts[self.best_attempt_index].overall_score if self._attempts else None,
        }
```

- [ ] **Step 4: Run all oracle tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_retry_oracle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit decision logic**

```bash
git add backend/app/services/retry_oracle.py backend/tests/test_retry_oracle.py
git commit -m "feat: implement 7-gate decision logic, focus selection, and diagnostic messages"
```

---

### Task 10: RetryOracle — Property-Based Tests

**Files:**
- Extend: `backend/tests/test_retry_oracle.py`

- [ ] **Step 1: Add Hypothesis property tests**

Append to `backend/tests/test_retry_oracle.py`:

```python
import os
from hypothesis import given, settings as h_settings, strategies as st

h_settings.register_profile("ci", max_examples=200, deadline=5000)
h_settings.register_profile("dev", max_examples=1000, deadline=10000)
h_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))

score_strategy = st.integers(min_value=1, max_value=10)
overall_strategy = st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False)


class TestOraclePropertyBased:
    @given(overall=overall_strategy)
    def test_best_attempt_score_gte_minimum(self, overall):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"overall_score": overall},
            prompt=f"Test prompt {overall}",
            focus_areas=[],
        )
        best_idx = oracle.best_attempt_index
        best_score = oracle._attempts[best_idx].overall_score
        min_score = min(a.overall_score for a in oracle._attempts)
        assert best_score >= min_score

    @given(threshold=st.floats(min_value=0.0, max_value=15.0, allow_nan=False, allow_infinity=False))
    def test_threshold_always_bounded(self, threshold):
        oracle = RetryOracle(max_retries=3, threshold=threshold)
        assert 3.0 <= oracle.threshold <= 8.0

    @given(
        s1=overall_strategy,
        s2=overall_strategy,
        s3=overall_strategy,
    )
    def test_momentum_is_bounded(self, s1, s2, s3):
        oracle = RetryOracle(max_retries=5)
        for i, s in enumerate([s1, s2, s3]):
            oracle.record_attempt(
                scores={"overall_score": s},
                prompt=f"Unique prompt version {i} score {s}",
                focus_areas=[],
            )
        momentum = oracle._compute_momentum()
        # Momentum is bounded by max possible score delta (-9 to +9)
        assert -10.0 <= momentum <= 10.0

    @given(
        clarity=score_strategy,
        specificity=score_strategy,
    )
    def test_regression_ratio_bounded_zero_to_one(self, clarity, specificity):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"clarity_score": 5, "specificity_score": 5, "overall_score": 5.0},
            prompt="V1", focus_areas=[],
        )
        oracle.record_attempt(
            scores={"clarity_score": clarity, "specificity_score": specificity, "overall_score": 5.0},
            prompt="V2 unique", focus_areas=[],
        )
        ratio = oracle._compute_regression_ratio()
        assert 0.0 <= ratio <= 1.0

    @given(overall=overall_strategy)
    def test_decision_is_valid_action(self, overall):
        oracle = RetryOracle(max_retries=1)
        oracle.record_attempt(
            scores={"overall_score": overall},
            prompt=f"Prompt {overall}",
            focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action in ("accept", "accept_best", "retry")
```

- [ ] **Step 2: Run property tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_retry_oracle.py::TestOraclePropertyBased -v`
Expected: All PASS

- [ ] **Step 3: Commit property tests**

```bash
git add backend/tests/test_retry_oracle.py
git commit -m "test: add Hypothesis property-based tests for RetryOracle invariants"
```

---

## Chunk 3: Feedback Service + Adaptation Engine

CRUD for feedback, user adaptation recomputation, and the 4 pipeline integration points. Depends on Chunk 1 (models, schemas).

### Task 11: Feedback Service — CRUD + Aggregation

**Files:**
- Create: `backend/app/services/feedback_service.py`
- Test: `backend/tests/test_feedback_service.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_feedback_service.py
"""Tests for feedback CRUD and aggregation."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.feedback_service import (
    upsert_feedback,
    get_feedback_for_optimization,
    get_feedback_aggregate,
    get_user_feedback_history,
)


def _mock_feedback(rating=1, overrides=None, opt_id="opt-1", user_id="user-1"):
    fb = MagicMock()
    fb.id = "fb-1"
    fb.optimization_id = opt_id
    fb.user_id = user_id
    fb.rating = rating
    fb.dimension_overrides = json.dumps(overrides) if overrides else None
    fb.corrected_issues = None
    fb.comment = None
    fb.created_at = MagicMock(isoformat=MagicMock(return_value="2026-03-13T00:00:00"))
    return fb


class TestUpsertFeedback:
    @pytest.mark.asyncio
    async def test_creates_new_feedback(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        result = await upsert_feedback(
            optimization_id="opt-1",
            user_id="user-1",
            rating=1,
            dimension_overrides=None,
            corrected_issues=None,
            comment=None,
            db=db,
        )
        assert result["created"] is True
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_feedback(self):
        db = AsyncMock()
        existing = _mock_feedback()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute.return_value = result_mock

        result = await upsert_feedback(
            optimization_id="opt-1",
            user_id="user-1",
            rating=-1,
            dimension_overrides=None,
            corrected_issues=None,
            comment="updated",
            db=db,
        )
        assert result["created"] is False
        assert existing.rating == -1


class TestGetAggregate:
    @pytest.mark.asyncio
    async def test_empty_aggregate(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        db.execute.return_value = result_mock

        agg = await get_feedback_aggregate("opt-1", db)
        assert agg["total_ratings"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_feedback_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement feedback_service.py**

```python
# backend/app/services/feedback_service.py
"""Feedback CRUD and aggregation.

One feedback per optimization per user (upsert semantics).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback

logger = logging.getLogger(__name__)


async def upsert_feedback(
    optimization_id: str,
    user_id: str,
    rating: int,
    dimension_overrides: dict | None,
    corrected_issues: list[str] | None,
    comment: str | None,
    db: AsyncSession,
) -> dict:
    """Create or update feedback. Returns {id, created: bool}."""
    stmt = select(Feedback).where(
        Feedback.optimization_id == optimization_id,
        Feedback.user_id == user_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.rating = rating
        existing.dimension_overrides = json.dumps(dimension_overrides) if dimension_overrides else None
        existing.corrected_issues = json.dumps(corrected_issues) if corrected_issues else None
        existing.comment = comment
        await db.flush()
        return {"id": existing.id, "created": False}

    fb = Feedback(
        id=str(uuid.uuid4()),
        optimization_id=optimization_id,
        user_id=user_id,
        rating=rating,
        dimension_overrides=json.dumps(dimension_overrides) if dimension_overrides else None,
        corrected_issues=json.dumps(corrected_issues) if corrected_issues else None,
        comment=comment,
    )
    db.add(fb)
    await db.flush()
    return {"id": fb.id, "created": True}


async def get_feedback_for_optimization(
    optimization_id: str,
    user_id: str,
    db: AsyncSession,
) -> dict | None:
    """Get the current user's feedback for an optimization."""
    stmt = select(Feedback).where(
        Feedback.optimization_id == optimization_id,
        Feedback.user_id == user_id,
    )
    result = await db.execute(stmt)
    fb = result.scalar_one_or_none()
    if not fb:
        return None
    return _to_dict(fb)


async def get_feedback_aggregate(
    optimization_id: str,
    db: AsyncSession,
) -> dict:
    """Compute aggregate feedback stats for an optimization."""
    stmt = select(Feedback).where(Feedback.optimization_id == optimization_id)
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {"total_ratings": 0, "positive": 0, "negative": 0, "neutral": 0, "avg_dimension_overrides": None}

    feedbacks = [r[0] if isinstance(r, tuple) else r for r in rows]
    positive = sum(1 for f in feedbacks if f.rating > 0)
    negative = sum(1 for f in feedbacks if f.rating < 0)
    neutral = sum(1 for f in feedbacks if f.rating == 0)

    # Average dimension overrides
    all_overrides: dict[str, list[int]] = {}
    for f in feedbacks:
        if f.dimension_overrides:
            try:
                overrides = json.loads(f.dimension_overrides) if isinstance(f.dimension_overrides, str) else f.dimension_overrides
                for k, v in overrides.items():
                    all_overrides.setdefault(k, []).append(v)
            except (json.JSONDecodeError, TypeError):
                pass

    avg_overrides = {k: round(sum(v) / len(v), 1) for k, v in all_overrides.items()} if all_overrides else None

    return {
        "total_ratings": len(feedbacks),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "avg_dimension_overrides": avg_overrides,
    }


async def get_user_feedback_history(
    user_id: str,
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    rating_filter: int | None = None,
) -> dict:
    """Paginated feedback history for a user."""
    stmt = select(Feedback).where(Feedback.user_id == user_id)
    if rating_filter is not None:
        stmt = stmt.where(Feedback.rating == rating_filter)
    stmt = stmt.order_by(Feedback.created_at.desc())

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Page
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "total": total,
        "count": len(rows),
        "offset": offset,
        "items": [_to_dict(f) for f in rows],
        "has_more": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


async def get_all_feedbacks_for_user(
    user_id: str,
    db: AsyncSession,
) -> list:
    """Get all feedbacks for adaptation computation. Returns ORM objects."""
    stmt = select(Feedback).where(Feedback.user_id == user_id).order_by(Feedback.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _to_dict(fb: Feedback) -> dict:
    """Convert Feedback ORM to response dict."""
    overrides = None
    if fb.dimension_overrides:
        try:
            overrides = json.loads(fb.dimension_overrides) if isinstance(fb.dimension_overrides, str) else fb.dimension_overrides
        except (json.JSONDecodeError, TypeError):
            pass

    issues = None
    if fb.corrected_issues:
        try:
            issues = json.loads(fb.corrected_issues) if isinstance(fb.corrected_issues, str) else fb.corrected_issues
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": fb.id,
        "optimization_id": fb.optimization_id,
        "user_id": fb.user_id,
        "rating": fb.rating,
        "dimension_overrides": overrides,
        "corrected_issues": issues,
        "comment": fb.comment,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_feedback_service.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/feedback_service.py backend/tests/test_feedback_service.py
git commit -m "feat: add feedback service with CRUD, upsert, and aggregation"
```

---

### Task 12: Adaptation Engine

**Files:**
- Create: `backend/app/services/adaptation_engine.py`
- Test: `backend/tests/test_adaptation_engine.py`

- [ ] **Step 1: Write failing tests for weight adjustment**

```python
# backend/tests/test_adaptation_engine.py
"""Tests for the adaptation engine — feedback → pipeline parameter tuning."""

import pytest
from app.services.adaptation_engine import (
    DEFAULT_WEIGHTS,
    adjust_weights_from_deltas,
    compute_override_deltas,
    compute_threshold_from_feedback,
    WEIGHT_LOWER_BOUND,
    WEIGHT_UPPER_BOUND,
    MAX_DAMPING,
)


class TestDefaultWeights:
    def test_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_five_dimensions(self):
        assert len(DEFAULT_WEIGHTS) == 5


class TestComputeOverrideDeltas:
    def test_basic_override(self):
        feedbacks = [
            {"dimension_overrides": {"clarity_score": 8}, "scores": {"clarity_score": 6}},
        ]
        deltas = compute_override_deltas(feedbacks)
        assert "clarity_score" in deltas
        assert deltas["clarity_score"] > 0  # user says it's better than validator thought

    def test_no_overrides_returns_empty(self):
        feedbacks = [{"dimension_overrides": None, "scores": {}}]
        deltas = compute_override_deltas(feedbacks)
        assert deltas == {}


class TestAdjustWeights:
    def test_no_deltas_returns_defaults(self):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, {}, damping=0.15, min_samples=3)
        assert weights == DEFAULT_WEIGHTS

    def test_sum_to_one_after_adjustment(self):
        deltas = {"clarity_score": 2.0, "faithfulness_score": -1.5}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_weights_within_bounds(self):
        # Extreme deltas
        deltas = {"clarity_score": 10.0, "specificity_score": -10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND <= w <= WEIGHT_UPPER_BOUND

    def test_damping_limits_shift(self):
        deltas = {"clarity_score": 10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        shift = abs(weights["clarity_score"] - DEFAULT_WEIGHTS["clarity_score"])
        assert shift <= MAX_DAMPING + 0.01  # small epsilon for float math


class TestComputeThreshold:
    def test_default_with_no_feedback(self):
        t = compute_threshold_from_feedback([], default=5.0, bounds=(3.0, 8.0))
        assert t == 5.0

    def test_bounded_low(self):
        # All negative feedback on high-scoring prompts → lower threshold
        feedbacks = [{"rating": -1, "overall_score": 8.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t >= 3.0

    def test_bounded_high(self):
        feedbacks = [{"rating": 1, "overall_score": 3.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t <= 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_adaptation_engine.py::TestDefaultWeights -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement adaptation_engine.py**

```python
# backend/app/services/adaptation_engine.py
"""Adaptation engine: feedback → pipeline parameter tuning.

Computes user-specific dimension weights, retry thresholds, and
strategy affinities from accumulated feedback and pairwise preferences.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import UserAdaptation
from app.services.prompt_diff import SCORE_DIMENSIONS

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "clarity_score": 0.20,
    "specificity_score": 0.20,
    "structure_score": 0.15,
    "faithfulness_score": 0.25,
    "conciseness_score": 0.20,
}

WEIGHT_LOWER_BOUND = 0.05
WEIGHT_UPPER_BOUND = 0.40
MAX_DAMPING = 0.15
THRESHOLD_BOUNDS = (3.0, 8.0)
MIN_FEEDBACKS_FOR_ADAPTATION = 3
MIN_SAMPLES_PER_DIMENSION = 3
MIN_SAMPLES_PER_STRATEGY = 2
STRATEGY_DECAY_DAYS = 90
PAIRWISE_WEIGHT_MULTIPLIER = 2.0

# Concurrency guard: per-user locks
_user_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _get_user_lock(user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def compute_override_deltas(feedbacks: list[dict]) -> dict[str, float]:
    """Compute average delta between user overrides and validator scores.

    Positive delta = user thinks dimension is underscored.
    Negative delta = user thinks dimension is overscored.
    """
    deltas: dict[str, list[float]] = {}
    for fb in feedbacks:
        overrides = fb.get("dimension_overrides")
        scores = fb.get("scores", {})
        if not overrides:
            continue
        for dim, user_val in overrides.items():
            validator_val = scores.get(dim)
            if validator_val is not None:
                deltas.setdefault(dim, []).append(user_val - validator_val)
    return {k: sum(v) / len(v) for k, v in deltas.items() if v}


def adjust_weights_from_deltas(
    base_weights: dict[str, float],
    deltas: dict[str, float],
    damping: float = MAX_DAMPING,
    min_samples: int = MIN_SAMPLES_PER_DIMENSION,
) -> dict[str, float]:
    """Adjust dimension weights based on user override patterns.

    If a user consistently overrides a dimension upward, its weight increases
    (the pipeline should care more about what the user values).

    Invariants: weights sum to 1.0, each within [0.05, 0.40], max shift 15%.
    """
    if not deltas:
        return dict(base_weights)

    adjusted = dict(base_weights)
    for dim, delta in deltas.items():
        if dim not in adjusted:
            continue
        # Normalize delta to a shift: positive delta → increase weight
        # Scale: delta of 3 points → full damping shift
        shift = (delta / 3.0) * damping
        shift = max(-damping, min(damping, shift))
        adjusted[dim] = adjusted[dim] + shift

    # Clamp
    for dim in adjusted:
        adjusted[dim] = max(WEIGHT_LOWER_BOUND, min(WEIGHT_UPPER_BOUND, adjusted[dim]))

    # Normalize to sum to 1.0
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total for k, v in adjusted.items()}

    return adjusted


def compute_threshold_from_feedback(
    feedbacks: list[dict],
    default: float = 5.0,
    bounds: tuple[float, float] = THRESHOLD_BOUNDS,
) -> float:
    """Compute retry threshold from feedback patterns.

    Negative feedback on high-scoring prompts → lower threshold (user is easier to please).
    Positive feedback on low-scoring prompts → raise threshold (user is harder to please).
    """
    if not feedbacks:
        return default

    adjustments: list[float] = []
    for fb in feedbacks:
        rating = fb.get("rating", 0)
        score = fb.get("overall_score")
        if score is None or rating == 0:
            continue
        if rating > 0 and score < default:
            adjustments.append(-0.2)  # user happy with low score → lower bar
        elif rating < 0 and score >= default:
            adjustments.append(0.3)  # user unhappy with high score → raise bar

    if not adjustments:
        return default

    avg_adj = sum(adjustments) / len(adjustments)
    result = default + avg_adj * len(adjustments) * 0.1  # cumulative but damped
    return max(bounds[0], min(bounds[1], round(result, 1)))


def compute_strategy_affinities(
    feedbacks: list[dict],
    min_samples: int = MIN_SAMPLES_PER_STRATEGY,
    decay_days: int = STRATEGY_DECAY_DAYS,
) -> dict:
    """Compute per-task_type strategy preferences from feedback.

    Returns {task_type: {preferred: [frameworks], avoid: [frameworks]}}.
    """
    now = datetime.now(timezone.utc)
    by_task: dict[str, dict[str, list[int]]] = {}

    for fb in feedbacks:
        task_type = fb.get("task_type")
        framework = fb.get("primary_framework")
        rating = fb.get("rating", 0)
        created = fb.get("created_at")

        if not task_type or not framework or rating == 0:
            continue

        # Apply decay
        if created and isinstance(created, datetime):
            age = (now - created).days
            if age > decay_days:
                continue

        by_task.setdefault(task_type, {}).setdefault(framework, []).append(rating)

    affinities: dict[str, dict[str, list[str]]] = {}
    for task_type, frameworks in by_task.items():
        preferred = []
        avoid = []
        for fw, ratings in frameworks.items():
            if len(ratings) < min_samples:
                continue
            avg = sum(ratings) / len(ratings)
            if avg > 0.3:
                preferred.append(fw)
            elif avg < -0.3:
                avoid.append(fw)
        if preferred or avoid:
            affinities[task_type] = {"preferred": preferred, "avoid": avoid}

    return affinities


async def recompute_adaptation(
    user_id: str,
    db: AsyncSession,
    feedbacks: list | None = None,
    preferences: list | None = None,
) -> None:
    """Recompute user adaptation from accumulated feedback.

    Protected by per-user asyncio.Lock — concurrent calls for same user skip.
    """
    lock = _get_user_lock(user_id)
    # Use try_lock pattern: acquire non-blocking, skip if already held.
    # This avoids the check-then-act race of `if lock.locked(): return`.
    if lock.locked():
        logger.info("Adaptation recompute already in progress for user %s, skipping", user_id)
        return

    async with lock:
        from app.services.feedback_service import get_all_feedbacks_for_user
        from sqlalchemy import select as sa_select

        if feedbacks is None:
            feedbacks_orm = await get_all_feedbacks_for_user(user_id, db)
            # Join with optimization to get validator scores for delta computation
            from app.models.optimization import Optimization
            feedbacks = []
            for fb in feedbacks_orm:
                overrides = None
                if fb.dimension_overrides:
                    try:
                        overrides = json.loads(fb.dimension_overrides) if isinstance(fb.dimension_overrides, str) else fb.dimension_overrides
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Fetch optimization scores for this feedback
                opt_stmt = sa_select(Optimization).where(Optimization.id == fb.optimization_id)
                opt_result = await db.execute(opt_stmt)
                opt = opt_result.scalar_one_or_none()
                scores: dict = {}
                if opt:
                    for dim in SCORE_DIMENSIONS:
                        val = getattr(opt, dim, None)
                        if val is not None:
                            scores[dim] = val
                    scores["overall_score"] = opt.overall_score
                feedbacks.append({
                    "rating": fb.rating,
                    "dimension_overrides": overrides,
                    "scores": scores,
                    "overall_score": scores.get("overall_score"),
                    "task_type": getattr(opt, "task_type", None) if opt else None,
                    "primary_framework": getattr(opt, "primary_framework", None) if opt else None,
                    "created_at": fb.created_at,
                })

        if len(feedbacks) < MIN_FEEDBACKS_FOR_ADAPTATION:
            return

        override_deltas = compute_override_deltas(feedbacks)
        adjusted_weights = adjust_weights_from_deltas(
            base_weights=DEFAULT_WEIGHTS,
            deltas=override_deltas,
            damping=MAX_DAMPING,
            min_samples=MIN_SAMPLES_PER_DIMENSION,
        )
        threshold = compute_threshold_from_feedback(feedbacks)
        affinities = compute_strategy_affinities(feedbacks)

        # Upsert user_adaptation
        from sqlalchemy import select as sa_select
        stmt = sa_select(UserAdaptation).where(UserAdaptation.user_id == user_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing:
            existing.dimension_weights = json.dumps(adjusted_weights)
            existing.strategy_affinities = json.dumps(affinities)
            existing.retry_threshold = threshold
            existing.feedback_count = len(feedbacks)
            existing.last_computed_at = now
        else:
            adaptation = UserAdaptation(
                user_id=user_id,
                dimension_weights=json.dumps(adjusted_weights),
                strategy_affinities=json.dumps(affinities),
                retry_threshold=threshold,
                feedback_count=len(feedbacks),
                last_computed_at=now,
            )
            db.add(adaptation)

        await db.flush()
        logger.info("Recomputed adaptation for user %s (%d feedbacks)", user_id, len(feedbacks))


async def load_adaptation(user_id: str, db: AsyncSession) -> dict | None:
    """Load user adaptation for pipeline injection. Returns None if not computed."""
    from sqlalchemy import select as sa_select
    stmt = sa_select(UserAdaptation).where(UserAdaptation.user_id == user_id)
    result = await db.execute(stmt)
    adaptation = result.scalar_one_or_none()
    if not adaptation:
        return None

    weights = None
    if adaptation.dimension_weights:
        try:
            weights = json.loads(adaptation.dimension_weights) if isinstance(adaptation.dimension_weights, str) else adaptation.dimension_weights
        except (json.JSONDecodeError, TypeError):
            pass

    affinities = None
    if adaptation.strategy_affinities:
        try:
            affinities = json.loads(adaptation.strategy_affinities) if isinstance(adaptation.strategy_affinities, str) else adaptation.strategy_affinities
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "dimension_weights": weights,
        "strategy_affinities": affinities,
        "retry_threshold": adaptation.retry_threshold,
        "feedback_count": adaptation.feedback_count,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_adaptation_engine.py -v`
Expected: All PASS

- [ ] **Step 5: Add property-based tests for weight invariants**

Append to `backend/tests/test_adaptation_engine.py`:

```python
import os
from hypothesis import given, settings as h_settings, strategies as st

h_settings.register_profile("ci", max_examples=200, deadline=5000)
h_settings.register_profile("dev", max_examples=1000, deadline=10000)
h_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))


class TestAdaptationPropertyBased:
    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_weights_always_sum_to_one(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_all_weights_within_bounds(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND - 1e-6 <= w <= WEIGHT_UPPER_BOUND + 1e-6

    @given(
        ratings=st.lists(
            st.tuples(
                st.sampled_from([-1, 0, 1]),
                st.floats(min_value=1.0, max_value=10.0, allow_nan=False),
            ),
            min_size=0, max_size=20,
        )
    )
    def test_threshold_always_bounded(self, ratings):
        feedbacks = [{"rating": r, "overall_score": s} for r, s in ratings]
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert 3.0 <= t <= 8.0
```

- [ ] **Step 6: Run all adaptation tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_adaptation_engine.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/adaptation_engine.py backend/tests/test_adaptation_engine.py
git commit -m "feat: add adaptation engine with weight tuning, threshold calibration, and strategy affinities"
```

---

### Task 13: Feedback Router

**Files:**
- Create: `backend/app/routers/feedback.py`
- Modify: `backend/app/main.py:24-32` (register router)
- Test: `backend/tests/test_feedback_api.py`

- [ ] **Step 1: Write failing contract tests**

```python
# backend/tests/test_feedback_api.py
"""Contract tests for feedback API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.schemas.feedback import FeedbackCreate, VALID_DIMENSIONS


class TestFeedbackCreateValidation:
    def test_valid_feedback(self):
        fb = FeedbackCreate(rating=1)
        assert fb.rating == 1

    def test_valid_with_overrides(self):
        fb = FeedbackCreate(rating=1, dimension_overrides={"clarity_score": 8})
        assert fb.dimension_overrides["clarity_score"] == 8

    def test_invalid_dimension_rejected(self):
        with pytest.raises(ValueError, match="Invalid dimension"):
            FeedbackCreate(rating=1, dimension_overrides={"invalid_dim": 5})

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="Score must be 1-10"):
            FeedbackCreate(rating=1, dimension_overrides={"clarity_score": 11})

    def test_invalid_rating_rejected(self):
        with pytest.raises(Exception):
            FeedbackCreate(rating=2)
```

- [ ] **Step 2: Run tests to verify they pass (schema validation only)**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_feedback_api.py -v`
Expected: All PASS (pure Pydantic validation, no router needed yet)

- [ ] **Step 3: Implement feedback router**

```python
# backend/app/routers/feedback.py
"""Feedback API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.schemas.auth import AuthenticatedUser
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackStatsResponse,
    FeedbackWithAggregate,
)
from app.services.adaptation_engine import load_adaptation, recompute_adaptation
from app.services.feedback_service import (
    get_feedback_aggregate,
    get_feedback_for_optimization,
    get_user_feedback_history,
    upsert_feedback,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])


@router.post(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_FEEDBACK))],
)
async def submit_feedback(
    optimization_id: str,
    body: FeedbackCreate,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    result = await upsert_feedback(
        optimization_id=optimization_id,
        user_id=current_user.id,
        rating=body.rating,
        dimension_overrides=body.dimension_overrides,
        corrected_issues=body.corrected_issues,
        comment=body.comment,
        db=db,
    )
    await db.commit()

    # Trigger background adaptation recomputation
    background_tasks.add_task(
        _recompute_adaptation_safe, current_user.id
    )

    return {"id": result["id"], "status": "created" if result["created"] else "updated"}


@router.get(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_feedback(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    feedback = await get_feedback_for_optimization(optimization_id, current_user.id, db)
    aggregate = await get_feedback_aggregate(optimization_id, db)
    return FeedbackWithAggregate(
        feedback=feedback,
        aggregate=aggregate,
    )


@router.get(
    "/api/feedback/history",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def feedback_history(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    offset: int = 0,
    limit: int = 50,
    rating: int | None = None,
):
    return await get_user_feedback_history(current_user.id, db, limit, offset, rating)


@router.get(
    "/api/feedback/stats",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def feedback_stats(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    history = await get_user_feedback_history(current_user.id, db, limit=1000)
    adaptation = await load_adaptation(current_user.id, db)

    feedbacks = history["items"]
    rating_dist = {"positive": 0, "negative": 0, "neutral": 0}
    for fb in feedbacks:
        if fb["rating"] > 0:
            rating_dist["positive"] += 1
        elif fb["rating"] < 0:
            rating_dist["negative"] += 1
        else:
            rating_dist["neutral"] += 1

    return FeedbackStatsResponse(
        total_feedbacks=history["total"],
        rating_distribution=rating_dist,
        avg_override_delta=None,  # computed from overrides vs validator scores
        most_corrected_dimension=None,
        adaptation_state=adaptation,
    )


async def _recompute_adaptation_safe(user_id: str) -> None:
    """Background task wrapper with its own DB session."""
    from app.database import get_session_context
    try:
        async with get_session_context() as db:
            await recompute_adaptation(user_id, db)
            await db.commit()
    except Exception:
        logger.exception("Background adaptation recompute failed for user %s", user_id)
```

- [ ] **Step 4: Register router in main.py**

In `backend/app/main.py`, add after the existing router imports (~line 32):

```python
from app.routers.feedback import router as feedback_router
```

And in the router inclusion section, add:

```python
app.include_router(feedback_router)
```

- [ ] **Step 5: Run all backend tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/feedback.py backend/app/main.py backend/tests/test_feedback_api.py
git commit -m "feat: add feedback API router with submit, get, history, and stats endpoints"
```

---

## Chunk 4: Session Context + Refinement Service

Provider-level session abstraction, unified refine operation, branch CRUD, and the refinement router. Depends on Chunks 1-3.

### Task 14: SessionContext Dataclass + Compaction

**Files:**
- Create: `backend/app/services/session_context.py`
- Test: `backend/tests/test_session_context.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_session_context.py
"""Tests for SessionContext serialization and compaction."""

import json
import pytest
from app.services.session_context import (
    SessionContext,
    MAX_REFINEMENT_TURNS,
    MAX_SESSION_CONTEXT_BYTES,
    needs_compaction,
)


class TestSessionContext:
    def test_default_values(self):
        ctx = SessionContext()
        assert ctx.session_id is None
        assert ctx.message_history is None
        assert ctx.turn_count == 0

    def test_serialization_roundtrip(self):
        ctx = SessionContext(
            session_id="test-123",
            provider_type="claude_cli",
            turn_count=3,
        )
        data = ctx.to_dict()
        restored = SessionContext.from_dict(data)
        assert restored.session_id == "test-123"
        assert restored.turn_count == 3

    def test_api_session_with_history(self):
        ctx = SessionContext(
            provider_type="anthropic_api",
            message_history=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
            turn_count=1,
        )
        data = ctx.to_dict()
        assert len(data["message_history"]) == 2


class TestNeedsCompaction:
    def test_no_compaction_below_thresholds(self):
        ctx = SessionContext(turn_count=3, message_history=[{"role": "user", "content": "short"}])
        assert needs_compaction(ctx) is False

    def test_compaction_on_turn_count(self):
        ctx = SessionContext(turn_count=MAX_REFINEMENT_TURNS + 1, message_history=[])
        assert needs_compaction(ctx) is True

    def test_compaction_on_byte_size(self):
        big_history = [{"role": "user", "content": "x" * 100_000}] * 5
        ctx = SessionContext(turn_count=2, message_history=big_history)
        assert needs_compaction(ctx) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_session_context.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement session_context.py**

```python
# backend/app/services/session_context.py
"""Session context abstraction for provider-level session resumption.

CLI provider: stores session_id (~50 bytes), SDK handles context.
API provider: stores message_history, compacted when threshold exceeded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_REFINEMENT_TURNS = 10
MAX_SESSION_CONTEXT_BYTES = 256_000  # 256KB hard cap per branch
COMPACTION_KEEP_PAIRS = 4  # keep last N turn pairs after compaction


@dataclass
class SessionContext:
    """Provider-agnostic session state."""
    session_id: str | None = None
    message_history: list[dict] | None = None
    provider_type: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    turn_count: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "message_history": self.message_history,
            "provider_type": self.provider_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionContext":
        created = data.get("created_at")
        if created and isinstance(created, str):
            created = datetime.fromisoformat(created)
        return cls(
            session_id=data.get("session_id"),
            message_history=data.get("message_history"),
            provider_type=data.get("provider_type", ""),
            created_at=created or datetime.now(timezone.utc),
            turn_count=data.get("turn_count", 0),
        )


def needs_compaction(session: SessionContext) -> bool:
    """Check if session needs compaction (turn count or byte size)."""
    if session.turn_count > MAX_REFINEMENT_TURNS:
        return True
    if session.message_history:
        size = len(json.dumps(session.message_history))
        if size > MAX_SESSION_CONTEXT_BYTES:
            return True
    return False


async def compact_session(
    session: SessionContext,
    provider,
) -> SessionContext:
    """Compact session history by summarizing old turns.

    Keeps system message + summary + last 4 turn pairs.
    Summary generated by Haiku 4.5. On failure: return uncompacted.
    """
    if not needs_compaction(session):
        return session

    history = session.message_history
    if not history or len(history) < 3:
        return session

    keep_count = COMPACTION_KEEP_PAIRS * 2  # pairs → individual messages
    old_turns = history[1:-keep_count] if len(history) > keep_count + 1 else []

    if not old_turns:
        return session

    try:
        summary = await provider.complete(
            system="Summarize this conversation concisely, preserving all decisions and constraints.",
            user=json.dumps(old_turns),
            model="claude-haiku-4-5",
        )
    except Exception:
        logger.warning("Session compaction failed, continuing with full history")
        return session

    compacted_history = [
        history[0],  # system message
        {"role": "user", "content": f"[Previous refinement context]\n{summary}"},
        {"role": "assistant", "content": "Understood. I have the full context."},
        *history[-keep_count:],
    ]

    return SessionContext(
        session_id=session.session_id,
        message_history=compacted_history,
        provider_type=session.provider_type,
        created_at=session.created_at,
        turn_count=session.turn_count,
    )
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_session_context.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/session_context.py backend/tests/test_session_context.py
git commit -m "feat: add SessionContext dataclass with serialization and compaction logic"
```

---

### Task 15: Provider Layer — complete_with_session()

**Files:**
- Modify: `backend/app/providers/base.py:250-290` (add concrete method)
- Modify: `backend/app/providers/anthropic_api.py` (override with message replay)
- Modify: `backend/app/providers/claude_cli.py` (override with SDK resume)

- [ ] **Step 1: Write failing test**

```python
# Append to backend/tests/test_providers.py or create test_session_provider.py
# Testing the base class default implementation

import pytest
from unittest.mock import AsyncMock
from app.services.session_context import SessionContext


class TestCompleteWithSessionBase:
    @pytest.mark.asyncio
    async def test_default_implementation_delegates_to_complete(self):
        from app.providers.mock import MockProvider
        provider = MockProvider()
        result_text, session = await provider.complete_with_session(
            system="test", user="test", model="claude-haiku-4-5",
        )
        assert isinstance(result_text, str)
        assert isinstance(session, SessionContext)
        assert session.turn_count == 1
```

- [ ] **Step 2: Add complete_with_session to LLMProvider base class**

In `backend/app/providers/base.py`, add after `get_last_usage()` (~line 268):

```python
    async def complete_with_session(
        self,
        system: str,
        user: str,
        model: str,
        session: "SessionContext | None" = None,
        schema: dict | None = None,
    ) -> tuple[str, "SessionContext"]:
        """Completion with session continuity. Returns (response, updated_session).

        Default: delegates to complete/complete_json, returns fresh SessionContext.
        CLI and API providers override with session-aware behavior.
        """
        from app.services.session_context import SessionContext as SC

        if schema:
            response = await self.complete_json(system, user, model, schema)
            text = json.dumps(response) if isinstance(response, dict) else str(response)
        else:
            text = await self.complete(system, user, model)

        from datetime import datetime, timezone
        new_session = SC(
            provider_type=self.name,
            created_at=session.created_at if session else datetime.now(timezone.utc),
            turn_count=(session.turn_count + 1) if session else 1,
        )
        return text, new_session
```

Add `import json` at top of file if not already present.

- [ ] **Step 3: Run the test**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_providers.py::TestCompleteWithSessionBase -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/providers/base.py backend/app/providers/anthropic_api.py backend/app/providers/claude_cli.py
git commit -m "feat: add complete_with_session() to LLMProvider with default implementation"
```

Note: CLI and API provider overrides will be implemented when the refinement service integration is tested end-to-end.

---

### Task 16: Refinement Service — Branch CRUD + Unified Refine

**Files:**
- Create: `backend/app/services/refinement_service.py`
- Test: `backend/tests/test_refinement_service.py`

- [ ] **Step 1: Write failing tests for branch CRUD**

```python
# backend/tests/test_refinement_service.py
"""Tests for refinement service — branch CRUD and unified refine."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_service import (
    create_trunk_branch,
    get_branches,
    get_branch,
    MAX_BRANCHES_PER_OPTIMIZATION,
    MAX_ACTIVE_BRANCHES,
)


class TestCreateTrunkBranch:
    @pytest.mark.asyncio
    async def test_creates_trunk(self):
        db = AsyncMock()
        db.add = MagicMock()
        branch = await create_trunk_branch(
            optimization_id="opt-1",
            prompt="Test prompt",
            scores={"overall_score": 6.0, "clarity_score": 7},
            db=db,
        )
        assert branch["label"] == "trunk"
        assert branch["status"] == "active"
        assert branch["turn_count"] == 0
        db.add.assert_called_once()


class TestBranchLimits:
    @pytest.mark.asyncio
    async def test_max_branches_enforced(self):
        db = AsyncMock()
        # Mock existing branches at limit
        result_mock = MagicMock()
        result_mock.scalar.return_value = MAX_BRANCHES_PER_OPTIMIZATION
        db.execute.return_value = result_mock

        from app.services.refinement_service import fork_branch
        with pytest.raises(ValueError, match="Maximum.*branches"):
            await fork_branch(
                optimization_id="opt-1",
                parent_branch_id="branch-1",
                message="test",
                provider=AsyncMock(),
                db=db,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_refinement_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement refinement_service.py**

```python
# backend/app/services/refinement_service.py
"""Unified refinement service — branch CRUD and the single refine() operation.

One code path for auto-refinement (oracle-driven), user refinement, and forks.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import PairwisePreference, RefinementBranch
from app.models.optimization import Optimization
from app.providers.base import LLMProvider
from app.services.prompt_diff import SCORE_DIMENSIONS, compute_dimension_deltas, compute_prompt_hash
from app.services.session_context import SessionContext, compact_session, needs_compaction
from app.services.validator import compute_overall_score

logger = logging.getLogger(__name__)

MAX_BRANCHES_PER_OPTIMIZATION = 5
MAX_ACTIVE_BRANCHES = 3
MAX_TURNS_PER_BRANCH = 10


async def create_trunk_branch(
    optimization_id: str,
    prompt: str,
    scores: dict,
    db: AsyncSession,
    session_context: SessionContext | None = None,
) -> dict:
    """Create the initial trunk branch for an optimization."""
    branch = RefinementBranch(
        id=str(uuid.uuid4()),
        optimization_id=optimization_id,
        label="trunk",
        optimized_prompt=prompt,
        scores=json.dumps(scores),
        session_context=json.dumps(session_context.to_dict()) if session_context else None,
        turn_count=0,
        turn_history="[]",
        status="active",
    )
    db.add(branch)
    await db.flush()

    # Update optimization
    await db.execute(
        update(Optimization)
        .where(Optimization.id == optimization_id)
        .values(active_branch_id=branch.id, branch_count=1)
    )

    return _branch_to_dict(branch)


async def get_branches(optimization_id: str, db: AsyncSession) -> list[dict]:
    """List all branches for an optimization."""
    stmt = select(RefinementBranch).where(
        RefinementBranch.optimization_id == optimization_id
    ).order_by(RefinementBranch.created_at)
    result = await db.execute(stmt)
    return [_branch_to_dict(b) for b in result.scalars().all()]


async def get_branch(branch_id: str, db: AsyncSession) -> dict | None:
    """Get a single branch by ID."""
    stmt = select(RefinementBranch).where(RefinementBranch.id == branch_id)
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    return _branch_to_dict(branch) if branch else None


async def fork_branch(
    optimization_id: str,
    parent_branch_id: str,
    message: str,
    provider: LLMProvider,
    db: AsyncSession,
    label: str | None = None,
    user_adaptation: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """Fork a new branch from a parent. Yields SSE events."""
    # Check limits
    count_stmt = select(func.count()).select_from(RefinementBranch).where(
        RefinementBranch.optimization_id == optimization_id
    )
    result = await db.execute(count_stmt)
    count = result.scalar() or 0

    if count >= MAX_BRANCHES_PER_OPTIMIZATION:
        raise ValueError(f"Maximum {MAX_BRANCHES_PER_OPTIMIZATION} branches per optimization")

    active_stmt = select(func.count()).select_from(RefinementBranch).where(
        RefinementBranch.optimization_id == optimization_id,
        RefinementBranch.status == "active",
    )
    active_result = await db.execute(active_stmt)
    active_count = active_result.scalar() or 0

    if active_count >= MAX_ACTIVE_BRANCHES:
        raise ValueError(f"Maximum {MAX_ACTIVE_BRANCHES} active branches")

    # Load parent
    parent_stmt = select(RefinementBranch).where(RefinementBranch.id == parent_branch_id)
    parent_result = await db.execute(parent_stmt)
    parent = parent_result.scalar_one_or_none()
    if not parent:
        raise ValueError(f"Parent branch {parent_branch_id} not found")

    # Create fork
    auto_label = label or f"fork-{count + 1}"
    new_branch = RefinementBranch(
        id=str(uuid.uuid4()),
        optimization_id=optimization_id,
        parent_branch_id=parent_branch_id,
        forked_at_turn=parent.turn_count,
        label=auto_label,
        optimized_prompt=parent.optimized_prompt,
        scores=parent.scores,
        session_context=None,  # Fresh session for fork
        turn_count=0,
        turn_history="[]",
        status="active",
    )
    db.add(new_branch)

    # Update optimization branch count
    await db.execute(
        update(Optimization)
        .where(Optimization.id == optimization_id)
        .values(branch_count=count + 1)
    )
    await db.flush()

    yield {"event": "branch_created", "branch": _branch_to_dict(new_branch)}

    # Run first refinement turn on the fork
    async for event in refine(
        branch_id=new_branch.id,
        message=message,
        source="user",
        protect_dimensions=None,
        provider=provider,
        user_adaptation=user_adaptation,
        db=db,
    ):
        yield event


async def refine(
    branch_id: str,
    message: str,
    source: Literal["auto", "user"],
    protect_dimensions: list[str] | None,
    provider: LLMProvider,
    user_adaptation: dict | None,
    db: AsyncSession,
    model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """One refinement turn on a branch.

    Used by both auto-retry (oracle) and user refinement.
    Yields SSE events throughout.
    """
    # Load branch
    stmt = select(RefinementBranch).where(RefinementBranch.id == branch_id)
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise ValueError(f"Branch {branch_id} not found")

    if branch.status != "active":
        raise ValueError(f"Branch {branch_id} is {branch.status}, cannot refine")

    if branch.turn_count >= MAX_TURNS_PER_BRANCH:
        raise ValueError(f"Branch {branch_id} has reached max turns ({MAX_TURNS_PER_BRANCH})")

    # Load session context
    session = None
    if branch.session_context:
        try:
            session = SessionContext.from_dict(
                json.loads(branch.session_context) if isinstance(branch.session_context, str) else branch.session_context
            )
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to load session context for branch %s", branch_id)

    scores_before = json.loads(branch.scores) if branch.scores else {}

    yield {"event": "refinement_started", "branch_id": branch_id, "turn": branch.turn_count + 1, "source": source}

    # Build refinement system prompt
    system_prompt = _build_refinement_prompt(
        current_prompt=branch.optimized_prompt or "",
        protect_dimensions=protect_dimensions,
    )

    # Session-aware completion (use passed model or fall back to MODEL_ROUTING)
    from app.providers.base import MODEL_ROUTING
    refine_model = model or MODEL_ROUTING["optimize"]

    try:
        response_text, updated_session = await provider.complete_with_session(
            system=system_prompt,
            user=message,
            model=refine_model,
            session=session,
        )
    except Exception as e:
        logger.error("Refinement failed for branch %s: %s", branch_id, e)
        yield {"event": "refinement_error", "error": str(e), "recoverable": True}
        return

    # Extract refined prompt (response should be the full optimized prompt)
    refined_prompt = response_text.strip()
    if not refined_prompt:
        yield {"event": "refinement_error", "error": "Empty response from optimizer", "recoverable": True}
        return

    yield {"event": "refinement_optimized", "prompt_preview": refined_prompt[:200]}

    # Compact session if needed
    if needs_compaction(updated_session):
        updated_session = await compact_session(updated_session, provider)

    # Update branch
    prompt_hash = compute_prompt_hash(refined_prompt)
    deltas = compute_dimension_deltas(scores_before, scores_before)  # Placeholder: validation runs separately

    turn_entry = {
        "turn": branch.turn_count + 1,
        "source": source,
        "message_summary": message[:200],
        "scores_before": scores_before,
        "prompt_hash": prompt_hash,
    }

    history = json.loads(branch.turn_history or "[]")
    history.append(turn_entry)

    branch.optimized_prompt = refined_prompt
    branch.session_context = json.dumps(updated_session.to_dict())
    branch.turn_count += 1
    branch.turn_history = json.dumps(history)
    branch.row_version += 1

    # Sync to optimization if this is the active branch
    opt_stmt = select(Optimization).where(Optimization.id == branch.optimization_id)
    opt_result = await db.execute(opt_stmt)
    opt = opt_result.scalar_one_or_none()
    if opt and opt.active_branch_id == branch_id:
        opt.optimized_prompt = refined_prompt
        opt.refinement_turns = (opt.refinement_turns or 0) + 1

    await db.flush()

    yield {
        "event": "refinement_complete",
        "branch_id": branch_id,
        "turn": branch.turn_count,
        "prompt": refined_prompt,
    }


async def select_branch(
    optimization_id: str,
    branch_id: str,
    user_id: str,
    reason: str | None,
    db: AsyncSession,
) -> dict:
    """Select a branch as the winner. Records pairwise preferences."""
    # Load all branches
    branches = await get_branches(optimization_id, db)
    if not branches:
        raise ValueError("No branches found")

    winner = next((b for b in branches if b["id"] == branch_id), None)
    if not winner:
        raise ValueError(f"Branch {branch_id} not found")

    # Record pairwise preferences (winner vs each non-winner active/selected branch)
    for b in branches:
        if b["id"] != branch_id and b["status"] in ("active", "selected"):
            pref = PairwisePreference(
                id=str(uuid.uuid4()),
                optimization_id=optimization_id,
                preferred_branch_id=branch_id,
                rejected_branch_id=b["id"],
                preferred_scores=json.dumps(winner.get("scores")),
                rejected_scores=json.dumps(b.get("scores")),
                user_id=user_id,
                reason=reason,
            )
            db.add(pref)

    # Update branch statuses
    for b in branches:
        await db.execute(
            update(RefinementBranch)
            .where(RefinementBranch.id == b["id"])
            .values(status="selected" if b["id"] == branch_id else "abandoned")
        )

    # Sync winner to optimization
    await db.execute(
        update(Optimization)
        .where(Optimization.id == optimization_id)
        .values(
            active_branch_id=branch_id,
            optimized_prompt=winner.get("optimized_prompt"),
        )
    )

    await db.flush()
    return {"selected": branch_id, "preferences_recorded": len(branches) - 1}


def _branch_to_dict(branch: RefinementBranch) -> dict:
    """Convert branch ORM to response dict."""
    scores = None
    if branch.scores:
        try:
            scores = json.loads(branch.scores) if isinstance(branch.scores, str) else branch.scores
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": branch.id,
        "optimization_id": branch.optimization_id,
        "parent_branch_id": branch.parent_branch_id,
        "label": branch.label,
        "optimized_prompt": branch.optimized_prompt,
        "scores": scores,
        "turn_count": branch.turn_count,
        "status": branch.status,
        "created_at": branch.created_at.isoformat() if branch.created_at else None,
        "updated_at": branch.updated_at.isoformat() if branch.updated_at else None,
    }


def _build_refinement_prompt(
    current_prompt: str,
    protect_dimensions: list[str] | None = None,
) -> str:
    """Build system prompt for refinement turns."""
    parts = [
        "You are a prompt optimization expert. Refine the following prompt based on the user's feedback.",
        f"\n## Current Prompt\n\n{current_prompt}",
        "\n## Instructions\n\n- Return ONLY the complete refined prompt, no explanations.",
        "- Preserve the original intent and structure unless explicitly asked to change it.",
    ]
    if protect_dimensions:
        dim_names = [d.replace("_score", "") for d in protect_dimensions]
        parts.append(f"- Protect these quality dimensions (do not degrade): {', '.join(dim_names)}.")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_refinement_service.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/refinement_service.py backend/tests/test_refinement_service.py
git commit -m "feat: add refinement service with branch CRUD, unified refine, and branch selection"
```

---

### Task 17: Refinement Router

**Files:**
- Create: `backend/app/routers/refinement.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_refinement_api.py`

- [ ] **Step 1: Write contract tests**

```python
# backend/tests/test_refinement_api.py
"""Contract tests for refinement API schemas."""

import pytest
from app.schemas.refinement import RefineRequest, ForkRequest, SelectRequest


class TestRefineRequestValidation:
    def test_valid_refine(self):
        r = RefineRequest(message="Make it shorter")
        assert r.message == "Make it shorter"

    def test_empty_message_rejected(self):
        with pytest.raises(Exception):
            RefineRequest(message="")

    def test_protect_dimensions_optional(self):
        r = RefineRequest(message="Improve clarity", protect_dimensions=["clarity_score"])
        assert r.protect_dimensions == ["clarity_score"]


class TestForkRequestValidation:
    def test_valid_fork(self):
        f = ForkRequest(parent_branch_id="branch-1", message="Try concise version")
        assert f.parent_branch_id == "branch-1"

    def test_label_optional(self):
        f = ForkRequest(parent_branch_id="b-1", message="test", label="concise-v1")
        assert f.label == "concise-v1"


class TestSelectRequestValidation:
    def test_valid_select(self):
        s = SelectRequest(branch_id="branch-1")
        assert s.branch_id == "branch-1"

    def test_reason_optional(self):
        s = SelectRequest(branch_id="b-1", reason="Better structure")
        assert s.reason == "Better structure"
```

- [ ] **Step 2: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_refinement_api.py -v`
Expected: All PASS

- [ ] **Step 3: Implement refinement router**

```python
# backend/app/routers/refinement.py
"""Refinement + branching API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.routers._sse import sse_event
from app.schemas.auth import AuthenticatedUser
from app.schemas.refinement import ForkRequest, RefineRequest, SelectRequest
from app.routers.feedback import _recompute_adaptation_safe
from app.services.adaptation_engine import load_adaptation, recompute_adaptation
from app.services.refinement_service import (
    fork_branch,
    get_branch,
    get_branches,
    refine,
    select_branch,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["refinement"])


@router.post(
    "/api/optimize/{optimization_id}/refine",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_REFINE))],
)
async def refine_optimization(
    optimization_id: str,
    body: RefineRequest,
    req: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Run one refinement turn on the active branch. Returns SSE stream."""
    if not req.app.state.provider:
        raise HTTPException(503, "LLM provider not initialized")

    adaptation = await load_adaptation(current_user.id, db)

    # Find active branch
    branches = await get_branches(optimization_id, db)
    active = next((b for b in branches if b["status"] == "active"), None)
    if not active:
        raise HTTPException(404, "No active branch found")

    async def event_stream():
        try:
            async for event in refine(
                branch_id=active["id"],
                message=body.message,
                source="user",
                protect_dimensions=body.protect_dimensions,
                provider=req.app.state.provider,
                user_adaptation=adaptation,
                db=db,
            ):
                yield sse_event(event.get("event", "refinement_update"), event)
            await db.commit()
        except ValueError as e:
            yield sse_event("error", {"error": str(e), "recoverable": False})
        except Exception as e:
            logger.exception("Refinement stream error for %s", optimization_id)
            yield sse_event("error", {"error": "Internal error", "recoverable": False})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/api/optimize/{optimization_id}/branches",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_BRANCH_FORK))],
)
async def create_branch(
    optimization_id: str,
    body: ForkRequest,
    req: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Fork a new branch. Returns SSE stream."""
    if not req.app.state.provider:
        raise HTTPException(503, "LLM provider not initialized")

    adaptation = await load_adaptation(current_user.id, db)

    async def event_stream():
        try:
            async for event in fork_branch(
                optimization_id=optimization_id,
                parent_branch_id=body.parent_branch_id,
                message=body.message,
                provider=req.app.state.provider,
                db=db,
                label=body.label,
                user_adaptation=adaptation,
            ):
                yield sse_event(event.get("event", "branch_update"), event)
            await db.commit()
        except ValueError as e:
            yield sse_event("error", {"error": str(e), "recoverable": False})
        except Exception as e:
            logger.exception("Branch fork error for %s", optimization_id)
            yield sse_event("error", {"error": "Internal error", "recoverable": False})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/api/optimize/{optimization_id}/branches",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def list_branches(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    branches = await get_branches(optimization_id, db)
    return {"branches": branches, "total": len(branches)}


@router.get(
    "/api/optimize/{optimization_id}/branches/{branch_id}",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_branch_detail(
    optimization_id: str,
    branch_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    branch = await get_branch(branch_id, db)
    if not branch or branch["optimization_id"] != optimization_id:
        raise HTTPException(404, "Branch not found")
    return branch


@router.post(
    "/api/optimize/{optimization_id}/branches/select",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_BRANCH_SELECT))],
)
async def select_winner(
    optimization_id: str,
    body: SelectRequest,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        result = await select_branch(
            optimization_id=optimization_id,
            branch_id=body.branch_id,
            user_id=current_user.id,
            reason=body.reason,
            db=db,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Trigger adaptation recomputation (pairwise preferences)
    background_tasks.add_task(
        _recompute_adaptation_safe, current_user.id
    )

    return result


@router.get(
    "/api/optimize/{optimization_id}/branches/compare",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def compare_branches(
    optimization_id: str,
    branch_a: str,
    branch_b: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    a = await get_branch(branch_a, db)
    b = await get_branch(branch_b, db)
    if not a or not b:
        raise HTTPException(404, "Branch not found")

    # Compute score deltas
    deltas = {}
    if a.get("scores") and b.get("scores"):
        for dim in ("clarity_score", "specificity_score", "structure_score",
                     "faithfulness_score", "conciseness_score", "overall_score"):
            va = a["scores"].get(dim, 0)
            vb = b["scores"].get(dim, 0)
            deltas[dim] = round(va - vb, 1)

    return {"branch_a": a, "branch_b": b, "score_deltas": deltas}
```

**Note:** `_recompute_adaptation_safe` is imported at module level from `feedback.py` to avoid duplication.

- [ ] **Step 4: Register router in main.py**

In `backend/app/main.py`, add:

```python
from app.routers.refinement import router as refinement_router
```

And:

```python
app.include_router(refinement_router)
```

- [ ] **Step 5: Run all backend tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/refinement.py backend/app/main.py backend/tests/test_refinement_api.py
git commit -m "feat: add refinement router with refine, fork, select, compare, and list endpoints"
```

---

## Chunk 5: Pipeline Integration + Adaptation Injection

Wire RetryOracle into pipeline.py, inject adaptation into validator/strategy/optimizer, add new SSE events. Depends on Chunks 1-4.

### Task 18: Pipeline RetryOracle Integration

**Files:**
- Modify: `backend/app/services/pipeline.py` (replace retry loop, create trunk, emit new SSE events)
- Test: `backend/tests/test_pipeline_retry_oracle.py`

- [ ] **Step 1: Write failing tests for oracle-based retry**

```python
# backend/tests/test_pipeline_retry_oracle.py
"""Tests for pipeline integration with RetryOracle."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.retry_oracle import RetryOracle, RetryDecision


class TestPipelineOracleIntegration:
    def test_oracle_replaces_fixed_threshold(self):
        """Oracle should be instantiated with user adaptation threshold."""
        oracle = RetryOracle(max_retries=1, threshold=6.5)
        oracle.record_attempt(
            scores={"overall_score": 7.0},
            prompt="Good prompt",
            focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "accept"

    def test_oracle_best_of_n_returns_highest(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 8.0}, prompt="V2 unique", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V3 different", focus_areas=[])
        assert oracle.best_attempt_index == 1

    def test_oracle_diagnostics_structure(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3},
            prompt="Test",
            focus_areas=[],
        )
        diag = oracle.get_diagnostics()
        assert "attempt" in diag
        assert "momentum" in diag
        assert "best_attempt" in diag
```

- [ ] **Step 2: Run tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_pipeline_retry_oracle.py -v`
Expected: All PASS (these test oracle directly, not pipeline wiring)

- [ ] **Step 3: Modify pipeline.py to use RetryOracle**

In `backend/app/services/pipeline.py`, make these concrete changes:

**3a. Add imports** (after line 24):

```python
from app.services.retry_oracle import RetryOracle
from app.services.adaptation_engine import load_adaptation
from app.services.refinement_service import create_trunk_branch
```

**3b. Remove `LOW_SCORE_THRESHOLD`** — delete line 29 (`LOW_SCORE_THRESHOLD = 5.0`).

**3c. Add `user_id` parameter to `run_pipeline()`** — add after `instructions` parameter (~line 191):

```python
    user_id: str | None = None,
```

**3d. Load adaptation and initialize oracle** — insert after `codebase_context = None` / `total_tokens = 0` (~line 225):

```python
    # Load user adaptation (if authenticated)
    adaptation = None
    if user_id:
        from app.database import get_session_context
        async with get_session_context() as db:
            adaptation = await load_adaptation(user_id, db)

    # Initialize oracle (replaces LOW_SCORE_THRESHOLD)
    oracle_threshold = adaptation["retry_threshold"] if adaptation else 5.0
    oracle_weights = adaptation.get("dimension_weights") if adaptation else None
    oracle = RetryOracle(
        max_retries=effective_max_retries,
        threshold=oracle_threshold,
        user_weights=oracle_weights,
    )
```

**3e. Replace the entire retry loop** (lines 601-686) — remove the `while` block that checks `overall_score < LOW_SCORE_THRESHOLD` and replace with:

```python
    # ---- Oracle-driven retry loop ----
    # Record first attempt
    validation_scores = validation.get("scores", {})
    optimized_prompt = (optimization_result or {}).get("optimized_prompt", "")
    oracle.record_attempt(validation_scores, optimized_prompt, [])
    yield ("retry_diagnostics", oracle.get_diagnostics())

    # Store all attempts for best-of-N selection
    all_attempts = [{
        "optimization_result": optimization_result,
        "validation": validation,
    }]

    while True:
        decision = oracle.should_retry()
        if decision.action in ("accept", "accept_best"):
            # Best-of-N: if accept_best, swap to the highest-scoring attempt
            if decision.action == "accept_best" and decision.best_attempt is not None:
                best = all_attempts[decision.best_attempt]
                optimization_result = best["optimization_result"]
                validation = best["validation"]
                yield ("retry_best_selected", {
                    "selected_attempt": decision.best_attempt + 1,
                    "total_attempts": len(all_attempts),
                    "reason": decision.reason,
                })
            break

        # Retry: build diagnostic message for the optimizer
        yield ("stage", {
            "stage": "optimize",
            "status": "retrying",
            "attempt": oracle.attempt_count + 1,
        })
        diagnostic_msg = oracle.build_diagnostic_message(decision.focus_areas)
        yield ("rate_limit_warning", {
            "message": diagnostic_msg,
            "stage": "validate",
        })

        try:
            ov_result = None
            async for event_type, event_data in _run_optimize_validate(
                provider, raw_prompt, analysis, strategy_result, codebase_context,
                file_contexts, url_fetched_contexts, instructions,
                model_optimize, model_validate, stream_optimize,
                retry_constraints={
                    "focus_areas": decision.focus_areas,
                    "min_score_target": oracle.threshold + 2,
                    "previous_score": oracle._attempts[-1].overall_score,
                    "retry_attempt": oracle.attempt_count,
                },
            ):
                if event_type == "_ov_result":
                    ov_result = event_data
                else:
                    yield (event_type, event_data)

            assert ov_result is not None

            if ov_result["opt_failed"]:
                logger.error("Retry optimizer failed; aborting retry loop")
                yield ("stage", {"stage": "validate", "status": "skipped"})
                yield ("error", {"stage": "optimize",
                                 "error": "Retry optimizer failed",
                                 "recoverable": False})
                return

            # Record new attempt
            new_opt = ov_result["optimization_result"]
            new_val = ov_result["validation"] or {}
            new_scores = new_val.get("scores", {})
            new_prompt = (new_opt or {}).get("optimized_prompt", "")
            oracle.record_attempt(new_scores, new_prompt, decision.focus_areas)
            yield ("retry_diagnostics", oracle.get_diagnostics())

            all_attempts.append({
                "optimization_result": new_opt,
                "validation": new_val,
            })

            # Update running references
            if new_opt:
                optimization_result = new_opt
            validation = new_val

        except Exception as e:
            logger.warning("Retry %d failed: %s", oracle.attempt_count, e)
            yield ("error", {
                "stage": "optimize",
                "error": f"Retry failed: {e}",
                "recoverable": True,
            })
            break
```

**3f. Create trunk branch at pipeline end** — after the final validation is settled and before the `complete` event (insert before the yield of `("complete", ...)`):

```python
    # Create trunk branch for refinement support
    try:
        from app.database import get_session_context as _gs
        async with _gs() as db:
            final_prompt = (optimization_result or {}).get("optimized_prompt", "")
            final_scores = validation.get("scores", {})
            if final_prompt:
                trunk = await create_trunk_branch(
                    optimization_id=optimization_id,
                    prompt=final_prompt,
                    scores=final_scores,
                    db=db,
                )
                await db.commit()
                yield ("branch_created", {"branch": trunk})
    except Exception as e:
        logger.warning("Trunk branch creation failed: %s (non-fatal)", e)

    # Store adaptation snapshot for transparency
    if adaptation:
        yield ("adaptation_snapshot", adaptation)
```

**3g. Thread `user_id` from the router** — In `backend/app/routers/optimize.py`, where `run_pipeline()` is called, add `user_id=current_user.id if current_user else None` to the call arguments. The `current_user` is already available from the `get_current_user` dependency.

- [ ] **Step 4: Run all pipeline tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_pipeline.py tests/test_pipeline_retry_oracle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pipeline.py backend/tests/test_pipeline_retry_oracle.py
git commit -m "feat: replace fixed retry threshold with RetryOracle in pipeline"
```

---

### Task 19: Adaptation Integration Points

**Files:**
- Modify: `backend/app/services/validator.py:22-53` (accept optional user_weights)
- Modify: `backend/app/services/strategy.py` (accept optional strategy_affinities)
- Modify: `backend/app/services/optimizer.py` (accept retry context enrichment)

- [ ] **Step 1: Modify validator to accept user weights**

In `backend/app/services/validator.py`, change `compute_overall_score()`:

```python
def compute_overall_score(
    scores: dict,
    user_weights: dict[str, float] | None = None,
) -> float | None:
    """Compute weighted average overall score.

    Uses user-adapted weights when available, falls back to defaults.
    """
    weights = user_weights if user_weights else SCORE_WEIGHTS
    weighted_sum = 0.0
    total_weight = 0.0

    for field, weight in weights.items():
        value = scores.get(field)
        if value is not None and isinstance(value, (int, float)):
            weighted_sum += value * weight
            total_weight += weight

    if total_weight == 0:
        return None
    raw = weighted_sum / total_weight
    return max(1.0, min(10.0, round(raw, 1)))
```

- [ ] **Step 2: Update run_validate to pass user_weights through**

In `backend/app/services/validator.py`, add `user_weights: dict[str, float] | None = None` parameter to `run_validate()` (~line 78). Then change the `compute_overall_score` call (~line 170) to:

```python
    overall_score = compute_overall_score(raw, user_weights)
```

- [ ] **Step 3: Add strategy_affinities to run_strategy**

In `backend/app/services/strategy.py`, add `strategy_affinities: dict | None = None` parameter to `run_strategy()`. Before returning the strategy result, inject soft bias:

```python
    # If user has strategy affinities, add soft bias
    if strategy_affinities:
        task = analysis.get("task_type", "")
        affinities = strategy_affinities.get(task, {})
        if affinities:
            # Inject as advisory context in the strategy result
            strategy_result["user_affinities"] = affinities
```

- [ ] **Step 4: Add retry context to run_optimize**

In `backend/app/services/optimizer.py`, the existing `retry_constraints` parameter already handles focus areas. Add an additional field to the retry_constraints dict when called from the oracle:

```python
    # In the user message builder, if retry_constraints has "priority_dimensions":
    if retry_constraints and retry_constraints.get("focus_areas"):
        focus_names = [f.replace("_score", "") for f in retry_constraints["focus_areas"]]
        user_message += (
            f"\n\nRETRY CONTEXT: Focus on improving these dimensions: {', '.join(focus_names)}. "
            f"Do not sacrifice other dimensions."
        )
```

This is already partially handled by the existing retry_constraints mechanism. The oracle simply provides richer focus_areas.

- [ ] **Step 5: Run validator tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -k "validator or strategy" -v`
Expected: All PASS (existing tests pass default weights)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/validator.py backend/app/services/strategy.py backend/app/services/optimizer.py
git commit -m "feat: add adaptation injection points to validator, strategy, and optimizer"
```

---

### Task 20: MCP Server Extensions + Accumulator Updates

**Files:**
- Modify: `backend/app/mcp_server.py` (add 3 new tools)
- Modify: `backend/app/services/optimization_service.py` (extend accumulator for new events)

- [ ] **Step 1: Extend PipelineAccumulator for new SSE events**

In `backend/app/services/optimization_service.py`, add handling in `process_event()` for:
- `retry_diagnostics` → store in updates
- `retry_best_selected` → record best attempt selection
- `instruction_compliance` → store in updates

- [ ] **Step 2: Add MCP tools**

In `backend/app/mcp_server.py`, add inside `create_mcp_server()`:

```python
@mcp.tool(
    name="submit_feedback",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def submit_feedback_tool(
    ctx: Context,
    optimization_id: str,
    rating: int,
    dimension_overrides: dict | None = None,
    comment: str | None = None,
) -> str:
    """Submit feedback (thumbs up/down) on an optimization. Rating: -1, 0, or 1."""
    from app.services.feedback_service import upsert_feedback
    if rating not in (-1, 0, 1):
        return json.dumps({"error": "Rating must be -1, 0, or 1"})
    async with _opt_session(optimization_id) as (db, opt):
        result = await upsert_feedback(
            optimization_id=optimization_id,
            user_id="mcp",  # MCP callers don't have user sessions
            rating=rating,
            dimension_overrides=dimension_overrides,
            corrected_issues=None,
            comment=comment,
            db=db,
        )
        await db.commit()
    return json.dumps(result)

@mcp.tool(
    name="get_branches",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def get_branches_tool(ctx: Context, optimization_id: str) -> str:
    """List all refinement branches for an optimization."""
    from app.services.refinement_service import get_branches
    async with _opt_session(optimization_id) as (db, opt):
        branches = await get_branches(optimization_id, db)
    return json.dumps({"branches": branches, "total": len(branches)})

@mcp.tool(
    name="get_adaptation_state",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def get_adaptation_state_tool(ctx: Context, user_id: str) -> str:
    """Get the current adaptation state (learned weights, threshold, affinities) for a user."""
    from app.services.adaptation_engine import load_adaptation
    from app.database import get_session_context
    async with get_session_context() as db:
        state = await load_adaptation(user_id, db)
    if not state:
        return json.dumps({"error": "No adaptation state found", "user_id": user_id})
    return json.dumps(state)
```

- [ ] **Step 3: Run existing tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/mcp_server.py backend/app/services/optimization_service.py
git commit -m "feat: add MCP tools (submit_feedback, get_branches, get_adaptation_state) and extend accumulator"
```

---

## Chunk 6: Frontend — Stores + API Client

New Svelte 5 runes stores for feedback and refinement, API client extensions. Depends on backend being complete (Chunks 1-5).

### Task 21: API Client Extensions

**Files:**
- Modify: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Add feedback API functions**

**IMPORTANT:** `apiFetch` returns a `Response` object (it wraps `fetch`). Callers must:
- Call `res.json()` on the response to parse JSON
- Use `JSON.stringify(body)` for POST request bodies
- Include `headers: { 'Content-Type': 'application/json' }` for POST requests
- Prefix all URLs with `${BASE}`
- Follow the existing pattern (see `fetchHealth`, `batchDeleteOptimizations`, `startOptimization`)

```typescript
// Append to frontend/src/lib/api/client.ts

// ── Feedback API ────────────────────────────────────────────────────

export async function submitFeedback(
  optimizationId: string,
  body: { rating: -1 | 0 | 1; dimension_overrides?: Record<string, number>; comment?: string }
): Promise<{ id: string; status: string }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Submit feedback failed: ${res.status}`);
  return res.json();
}

export async function getFeedback(
  optimizationId: string
): Promise<{ feedback: any | null; aggregate: any }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/feedback`);
  if (!res.ok) throw new Error(`Get feedback failed: ${res.status}`);
  return res.json();
}

export async function getFeedbackHistory(
  params: { offset?: number; limit?: number; rating?: number } = {}
): Promise<any> {
  const qs = new URLSearchParams();
  if (params.offset) qs.set('offset', String(params.offset));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.rating !== undefined) qs.set('rating', String(params.rating));
  const res = await apiFetch(`${BASE}/api/feedback/history?${qs}`);
  if (!res.ok) throw new Error(`Feedback history failed: ${res.status}`);
  return res.json();
}

export async function getFeedbackStats(): Promise<any> {
  const res = await apiFetch(`${BASE}/api/feedback/stats`);
  if (!res.ok) throw new Error(`Feedback stats failed: ${res.status}`);
  return res.json();
}

// ── Refinement API ──────────────────────────────────────────────────

/**
 * Start a refinement turn (SSE stream). Follows the same pattern as
 * startOptimization: get Response, check ok, iterate parseSSEStream.
 */
export function startRefinement(
  optimizationId: string,
  body: { message: string; protect_dimensions?: string[] },
  onEvent: (event: SSEEvent) => void,
  onComplete?: () => void,
  onError?: (error: Error) => void
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`Refinement failed (${res.status}): ${errorText}`);
      }
      if (!res.body) throw new Error('No response body for SSE stream');

      for await (const sseEvent of parseSSEStream(res.body, controller.signal)) {
        onEvent(sseEvent);
      }
      onComplete?.();
    } catch (err) {
      if ((err as Error).name !== 'AbortError') onError?.(err as Error);
    }
  })();

  return controller;
}

export function startBranchFork(
  optimizationId: string,
  body: { parent_branch_id: string; message: string; label?: string },
  onEvent: (event: SSEEvent) => void,
  onComplete?: () => void,
  onError?: (error: Error) => void
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`Branch fork failed (${res.status}): ${errorText}`);
      }
      if (!res.body) throw new Error('No response body for SSE stream');

      for await (const sseEvent of parseSSEStream(res.body, controller.signal)) {
        onEvent(sseEvent);
      }
      onComplete?.();
    } catch (err) {
      if ((err as Error).name !== 'AbortError') onError?.(err as Error);
    }
  })();

  return controller;
}

export async function listBranches(
  optimizationId: string
): Promise<{ branches: any[]; total: number }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches`);
  if (!res.ok) throw new Error(`List branches failed: ${res.status}`);
  return res.json();
}

export async function getBranch(
  optimizationId: string,
  branchId: string
): Promise<any> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches/${branchId}`);
  if (!res.ok) throw new Error(`Get branch failed: ${res.status}`);
  return res.json();
}

export async function selectBranch(
  optimizationId: string,
  body: { branch_id: string; reason?: string }
): Promise<any> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Select branch failed: ${res.status}`);
  return res.json();
}

export async function compareBranches(
  optimizationId: string,
  branchA: string,
  branchB: string
): Promise<any> {
  const res = await apiFetch(
    `${BASE}/api/optimize/${optimizationId}/branches/compare?branch_a=${encodeURIComponent(branchA)}&branch_b=${encodeURIComponent(branchB)}`
  );
  if (!res.ok) throw new Error(`Compare branches failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -5`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/client.ts
git commit -m "feat: add feedback and refinement API functions to frontend client"
```

---

### Task 22: Feedback Store

**Files:**
- Create: `frontend/src/lib/stores/feedback.svelte.ts`

- [ ] **Step 1: Write feedback store**

```typescript
// frontend/src/lib/stores/feedback.svelte.ts
/**
 * Feedback + adaptation state store.
 * Manages user feedback submission, dimension overrides, and adaptation transparency.
 */

import { submitFeedback, getFeedback, getFeedbackStats } from '$lib/api/client';

export interface FeedbackState {
  rating: -1 | 0 | 1 | null;
  dimensionOverrides: Record<string, number>;
  comment: string;
  submitting: boolean;
}

export interface AdaptationState {
  dimensionWeights: Record<string, number> | null;
  strategyAffinities: Record<string, any> | null;
  retryThreshold: number;
  feedbackCount: number;
}

class FeedbackStore {
  currentFeedback = $state<FeedbackState>({
    rating: null,
    dimensionOverrides: {},
    comment: '',
    submitting: false,
  });

  aggregate = $state<{
    totalRatings: number;
    positive: number;
    negative: number;
    neutral: number;
  }>({ totalRatings: 0, positive: 0, negative: 0, neutral: 0 });

  adaptationState = $state<AdaptationState | null>(null);
  currentOptimizationId = $state<string | null>(null);

  async loadFeedback(optimizationId: string) {
    this.currentOptimizationId = optimizationId;
    try {
      const result = await getFeedback(optimizationId);
      if (result.feedback) {
        this.currentFeedback.rating = result.feedback.rating;
        this.currentFeedback.dimensionOverrides = result.feedback.dimension_overrides || {};
        this.currentFeedback.comment = result.feedback.comment || '';
      } else {
        this.resetFeedback();
      }
      if (result.aggregate) {
        this.aggregate = {
          totalRatings: result.aggregate.total_ratings,
          positive: result.aggregate.positive,
          negative: result.aggregate.negative,
          neutral: result.aggregate.neutral,
        };
      }
    } catch {
      // Silent fail — feedback is non-critical
    }
  }

  async submit(optimizationId: string) {
    if (this.currentFeedback.rating === null) return;
    this.currentFeedback.submitting = true;
    try {
      const body: any = { rating: this.currentFeedback.rating };
      if (Object.keys(this.currentFeedback.dimensionOverrides).length > 0) {
        body.dimension_overrides = this.currentFeedback.dimensionOverrides;
      }
      if (this.currentFeedback.comment) {
        body.comment = this.currentFeedback.comment;
      }
      await submitFeedback(optimizationId, body);
      await this.loadFeedback(optimizationId);
    } finally {
      this.currentFeedback.submitting = false;
    }
  }

  setRating(rating: -1 | 0 | 1) {
    this.currentFeedback.rating = rating;
  }

  setDimensionOverride(dimension: string, score: number) {
    this.currentFeedback.dimensionOverrides[dimension] = score;
  }

  removeDimensionOverride(dimension: string) {
    delete this.currentFeedback.dimensionOverrides[dimension];
  }

  async loadAdaptationState() {
    try {
      const stats = await getFeedbackStats();
      if (stats.adaptation_state) {
        this.adaptationState = {
          dimensionWeights: stats.adaptation_state.dimension_weights,
          strategyAffinities: stats.adaptation_state.strategy_affinities,
          retryThreshold: stats.adaptation_state.retry_threshold,
          feedbackCount: stats.adaptation_state.feedback_count,
        };
      }
    } catch {
      // Silent fail
    }
  }

  resetFeedback() {
    this.currentFeedback = {
      rating: null,
      dimensionOverrides: {},
      comment: '',
      submitting: false,
    };
  }
}

export const feedback = new FeedbackStore();
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/stores/feedback.svelte.ts
git commit -m "feat: add feedback Svelte 5 runes store"
```

---

### Task 23: Refinement Store

**Files:**
- Create: `frontend/src/lib/stores/refinement.svelte.ts`

- [ ] **Step 1: Write refinement store**

```typescript
// frontend/src/lib/stores/refinement.svelte.ts
/**
 * Refinement + branch state store.
 * Manages refinement sessions, branch tree, and comparison state.
 */

import { listBranches, startRefinement, startBranchFork, selectBranch } from '$lib/api/client';
import type { SSEEvent } from '$lib/api/client';

export interface Branch {
  id: string;
  optimizationId: string;
  parentBranchId: string | null;
  label: string;
  optimizedPrompt: string | null;
  scores: Record<string, number> | null;
  turnCount: number;
  status: 'active' | 'selected' | 'abandoned';
  createdAt: string;
}

export interface RefinementTurn {
  turn: number;
  source: 'auto' | 'user';
  messageSummary: string;
  scoresBefore: Record<string, number> | null;
  promptHash: string;
}

class RefinementStore {
  branches = $state<Branch[]>([]);
  activeBranchId = $state<string | null>(null);
  refinementOpen = $state(false);
  refinementStreaming = $state(false);
  protectedDimensions = $state<string[]>([]);
  comparingBranches = $state<[string, string] | null>(null);
  abortController = $state<AbortController | null>(null);

  get activeBranch(): Branch | undefined {
    return this.branches.find((b) => b.id === this.activeBranchId);
  }

  get branchCount(): number {
    return this.branches.length;
  }

  get activeBranches(): Branch[] {
    return this.branches.filter((b) => b.status === 'active');
  }

  async loadBranches(optimizationId: string) {
    try {
      const result = await listBranches(optimizationId);
      this.branches = result.branches.map(mapBranch);
      const active = this.branches.find((b) => b.status === 'active' || b.status === 'selected');
      this.activeBranchId = active?.id ?? null;
    } catch {
      this.branches = [];
    }
  }

  async startRefine(optimizationId: string, message: string) {
    this.refinementStreaming = true;
    this.abortController = startRefinement(
      optimizationId,
      { message, protect_dimensions: this.protectedDimensions.length > 0 ? this.protectedDimensions : undefined },
      (event) => this.handleRefinementEvent(event),
      () => {
        this.refinementStreaming = false;
        this.loadBranches(optimizationId);
      },
      () => { this.refinementStreaming = false; },
    );
  }

  async startFork(optimizationId: string, parentBranchId: string, message: string, label?: string) {
    this.refinementStreaming = true;
    this.abortController = startBranchFork(
      optimizationId,
      { parent_branch_id: parentBranchId, message, label },
      (event) => this.handleRefinementEvent(event),
      () => {
        this.refinementStreaming = false;
        this.loadBranches(optimizationId);
      },
      () => { this.refinementStreaming = false; },
    );
  }

  async selectWinner(optimizationId: string, branchId: string, reason?: string) {
    await selectBranch(optimizationId, { branch_id: branchId, reason });
    await this.loadBranches(optimizationId);
  }

  handleRefinementEvent(event: SSEEvent) {
    const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
    switch (event.event) {
      case 'refinement_complete':
        // Update active branch prompt locally
        if (data.branch_id && data.prompt) {
          const branch = this.branches.find((b) => b.id === data.branch_id);
          if (branch) {
            branch.optimizedPrompt = data.prompt;
            branch.turnCount = data.turn;
          }
        }
        break;
      case 'branch_created':
        if (data.branch) {
          this.branches.push(mapBranch(data.branch));
        }
        break;
    }
  }

  toggleProtectDimension(dim: string) {
    const idx = this.protectedDimensions.indexOf(dim);
    if (idx >= 0) {
      this.protectedDimensions.splice(idx, 1);
    } else {
      this.protectedDimensions.push(dim);
    }
  }

  openRefinement() { this.refinementOpen = true; }
  closeRefinement() { this.refinementOpen = false; }

  reset() {
    this.branches = [];
    this.activeBranchId = null;
    this.refinementOpen = false;
    this.refinementStreaming = false;
    this.protectedDimensions = [];
    this.comparingBranches = null;
    this.abortController?.abort();
    this.abortController = null;
  }
}

function mapBranch(raw: any): Branch {
  return {
    id: raw.id,
    optimizationId: raw.optimization_id,
    parentBranchId: raw.parent_branch_id,
    label: raw.label,
    optimizedPrompt: raw.optimized_prompt,
    scores: raw.scores,
    turnCount: raw.turn_count,
    status: raw.status,
    createdAt: raw.created_at,
  };
}

export const refinement = new RefinementStore();
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/stores/refinement.svelte.ts
git commit -m "feat: add refinement Svelte 5 runes store with branch management"
```

---

### Task 24: Forge Store — New SSE Events

**Files:**
- Modify: `frontend/src/lib/stores/forge.svelte.ts`

- [ ] **Step 1: Add handling for new SSE events**

In the `handleSSEEvent()` method of the forge store, add cases for:

```typescript
case 'retry_diagnostics':
  // Store oracle diagnostics for RetryDiagnostics component
  this.retryDiagnostics = data;
  break;
case 'retry_best_selected':
  this.retryBestSelected = data;
  break;
case 'retry_cycle_detected':
  this.retryCycleDetected = data;
  break;
case 'instruction_compliance':
  this.instructionCompliance = data;
  break;
```

Add new state fields to the forge store class:

```typescript
retryDiagnostics = $state<any>(null);
retryBestSelected = $state<any>(null);
retryCycleDetected = $state<any>(null);
instructionCompliance = $state<any>(null);
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -5`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/stores/forge.svelte.ts
git commit -m "feat: handle new SSE events (retry_diagnostics, instruction_compliance) in forge store"
```

---

## Chunk 7: Frontend Components + Final Integration

Frontend components following brand guidelines. Depends on Chunk 6 (stores + API client).

### Task 25: FeedbackInline Component

**Files:**
- Create: `frontend/src/lib/components/editor/FeedbackInline.svelte`

- [ ] **Step 1: Create FeedbackInline component**

32px strip below optimized prompt with thumbs up/down, dimension chips, score circle, refine trigger, and branch indicator. Uses brand-guideline colors: neon-green (`--color-neon-green`) for positive, neon-red (`--color-neon-red`) for negative, neon-purple (`--color-neon-purple`) for overridden dimensions. See spec Section 6 for full details.

Component must:
- Import from `feedback` store
- Show thumbs with active state (1px neon border + 8% fill)
- Show dimension chips (font-mono 10px, 3-letter abbreviations: CLR, SPC, STR, FTH, CNC)
- Include "Refine" ghost button that triggers `refinement.openRefinement()`
- Follow 5-state interactive lifecycle and zero-effects directive
- Use Svelte 5 runes syntax (`$state`, `$derived`, `$effect`)

Use `@brand-guidelines` skill during implementation for exact styling.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -10`
Expected: No new errors from FeedbackInline.svelte

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/editor/FeedbackInline.svelte
git commit -m "feat: add FeedbackInline component with thumbs, dimension chips, and refine trigger"
```

---

### Task 26: RefinementInput Component

**Files:**
- Create: `frontend/src/lib/components/editor/RefinementInput.svelte`

- [ ] **Step 1: Create RefinementInput component**

Expandable well below FeedbackInline. Uses `slide-up-in` entry (300ms). Contains:
- Protected dimensions chips (neon-teal)
- Input well (bg-bg-input, focus ring)
- Turn history (reverse chronological, user=cyan, auto=indigo)
- Streaming state indicator (border-color oscillation, NOT a glow)

Use `@brand-guidelines` skill during implementation. Use Svelte 5 runes syntax.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -10`
Expected: No new errors from RefinementInput.svelte

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/editor/RefinementInput.svelte
git commit -m "feat: add RefinementInput expandable well with turn history and protected dimensions"
```

---

### Task 27: RetryDiagnostics Component

**Files:**
- Create: `frontend/src/lib/components/pipeline/RetryDiagnostics.svelte`

- [ ] **Step 1: Create RetryDiagnostics component**

Renders inside Validate StageCard during auto-retry. Shows:
- Oracle signal bars (neon-yellow fill)
- Elasticity mini-bars (40px wide, 4px height)
- Focus areas as chip-rects
- Decision line (neon-yellow=RETRY, neon-green=ACCEPT)

Use `@brand-guidelines` skill during implementation. Use Svelte 5 runes syntax.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -10`
Expected: No new errors from RetryDiagnostics.svelte

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/pipeline/RetryDiagnostics.svelte
git commit -m "feat: add RetryDiagnostics oracle signal visualization component"
```

---

### Task 28: BranchIndicator + BranchCompare Components

**Files:**
- Create: `frontend/src/lib/components/pipeline/BranchIndicator.svelte`
- Create: `frontend/src/lib/components/pipeline/BranchCompare.svelte`

- [ ] **Step 1: Create BranchIndicator**

Compact badge showing current branch label + turn count. Dropdown for switching branches when multiple exist. Fork and compare buttons in dropdown.

- [ ] **Step 2: Create BranchCompare**

Modal overlay (glass bg, 8px blur, z-index 50). Contains:
- Score table (font-mono, tabular figures, per-dimension with deltas)
- Prompt diff using existing DiffView.svelte
- Turn timeline (horizontal, 12px nodes)
- Select buttons

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -10`
Expected: No new errors from BranchIndicator or BranchCompare

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/pipeline/BranchIndicator.svelte frontend/src/lib/components/pipeline/BranchCompare.svelte
git commit -m "feat: add BranchIndicator and BranchCompare components"
```

---

### Task 29: Inspector Panels

**Files:**
- Create: `frontend/src/lib/components/layout/InspectorFeedback.svelte`
- Create: `frontend/src/lib/components/layout/InspectorRefinement.svelte`
- Create: `frontend/src/lib/components/layout/InspectorBranches.svelte`
- Create: `frontend/src/lib/components/layout/InspectorAdaptation.svelte`
- Modify: `frontend/src/lib/components/layout/Inspector.svelte` (mount new panels)

- [ ] **Step 1: Create InspectorFeedback panel**

Full feedback panel (see spec Section 6.4). Contents:
- Verdict thumbs (up/down/neutral) with active neon state
- Per-dimension ScoreBars (20px height) with stepper controls (±1) for dimension overrides
- Dimension names in font-mono 10px
- Issue corrections list (checkboxes, neon-red for unresolved)
- Comment textarea (bg-bg-input, 80px height)
- Save button (neon-cyan primary style)
- Import from `feedback` store, use Svelte 5 runes

- [ ] **Step 2: Create InspectorRefinement panel**

Turn history display (see spec Section 6.5). Contents:
- Reverse-chronological turn list
- Source indicators: user turns = neon-cyan badge, auto turns = neon-indigo badge
- Per-turn message summary (truncated to 2 lines)
- Score delta chips per dimension (neon-green for positive, neon-red for negative)
- Session state indicator (active / compacted / exhausted)
- Import from `refinement` store, use Svelte 5 runes

- [ ] **Step 3: Create InspectorBranches panel**

Branch tree with controls (see spec Section 6.6). Contents:
- Vertical branch tree: trunk at top, forks indented with 1px neon-purple connector lines
- Branch labels in font-mono
- Status badges: active=neon-green, selected=neon-cyan, abandoned=neon-dim
- Per-branch: label, turn count, overall score
- Fork button (ghost, neon-purple), Compare button (ghost, neon-blue), Select button (primary, neon-cyan)
- Import from `refinement` store, use Svelte 5 runes

- [ ] **Step 4: Create InspectorAdaptation panel**

Adaptation transparency (see spec Section 6.7). Contents:
- Per-dimension weight bars: horizontal bars (100% width), height 4px, color-coded
- Default weight shown as a thin vertical 1px marker on each bar
- Strategy affinities: per-task-type list with preferred/avoid badges
- Retry threshold display: numeric + position on 3.0-8.0 scale
- Feedback count and last computed timestamp
- Reset button (ghost, neon-red, with confirmation)
- Import from `feedback` store (adaptationState), use Svelte 5 runes

- [ ] **Step 5: Mount panels in Inspector.svelte**

Add conditional rendering of new panels in `Inspector.svelte`. Each panel shows when:
- **InspectorFeedback**: always visible when an optimization is loaded
- **InspectorRefinement**: visible when the optimization has `refinement_turns > 0`
- **InspectorBranches**: visible when the optimization has `branch_count > 1`
- **InspectorAdaptation**: visible when `feedback.adaptationState` is not null

- [ ] **Step 6: Verify TypeScript compiles**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -10`
Expected: No new errors from Inspector panels

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/components/layout/InspectorFeedback.svelte frontend/src/lib/components/layout/InspectorRefinement.svelte frontend/src/lib/components/layout/InspectorBranches.svelte frontend/src/lib/components/layout/InspectorAdaptation.svelte frontend/src/lib/components/layout/Inspector.svelte
git commit -m "feat: add Inspector panels for feedback, refinement, branches, and adaptation"
```

---

### Task 30: Mount Components + ForgeArtifact Integration

**Files:**
- Modify: `frontend/src/lib/components/editor/ForgeArtifact.svelte` (mount FeedbackInline + RefinementInput)

- [ ] **Step 1: Mount FeedbackInline in ForgeArtifact**

Add FeedbackInline component below the optimized prompt display. Load feedback when optimization is loaded. Show RefinementInput below when expanded.

- [ ] **Step 2: Mount BranchIndicator in ForgeArtifact**

Show BranchIndicator in ForgeArtifact above the optimized prompt, adjacent to the score display. Renders only when `refinement.branchCount > 1`. Import from `refinement` store.

- [ ] **Step 3: Verify full frontend compiles**

Run: `cd frontend && npx svelte-check --threshold warning`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/editor/ForgeArtifact.svelte
git commit -m "feat: mount feedback, refinement, and branch components in ForgeArtifact"
```

---

### Task 31: CLAUDE.md + Changelog Updates

**Files:**
- Modify: `CLAUDE.md` (document new endpoints, services, sort whitelist correction)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to the services section:
- `feedback_service.py` — Feedback CRUD + aggregation
- `adaptation_engine.py` — Feedback → pipeline parameter tuning
- `refinement_service.py` — Unified refine + branch CRUD
- `retry_oracle.py` — 7-gate adaptive retry
- `session_context.py` — Session abstraction + compaction
- `prompt_diff.py` — Hashing, deltas, cycle detection

Add to routers section:
- `feedback.py` — POST/GET feedback, stats, history
- `refinement.py` — Refine, fork, select, compare branches

Correct the sort whitelist documentation (was incorrectly listed as 3 files, is actually single source in optimization_service.py).

Add new rate limit env vars.

- [ ] **Step 2: Update CHANGELOG.md**

Add entries under `## Unreleased`:
- Added quality feedback loops with thumbs up/down, dimension overrides, and issue corrections
- Added adaptive RetryOracle replacing fixed 5.0 threshold with 7-gate decision algorithm
- Added user adaptation engine tuning validator weights, strategy selection, and retry thresholds per-user
- Added session resumption with unified refinement service and parallel branching
- Added feedback, refinement, and branch API endpoints
- Added 3 MCP tools (`submit_feedback`, `get_branches`, `get_adaptation_state`)
- Added frontend inline feedback, refinement input, branch management, and adaptation transparency
- Changed retry logic from fixed threshold to adaptive oracle with best-of-N selection
- Fixed sort whitelist documentation in CLAUDE.md (single source, not three files)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md CHANGELOG.md
git commit -m "docs: update CLAUDE.md and changelog for quality feedback loops feature"
```

---

### Task 32: Full Regression Test

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && npx svelte-check --threshold warning`
Expected: No errors

- [ ] **Step 3: Run Ruff lint**

Run: `cd backend && source .venv/bin/activate && ruff check app/ tests/`
Expected: No errors

- [ ] **Step 4: Verify services start**

Run: `./init.sh restart && sleep 5 && ./init.sh status`
Expected: All services running

- [ ] **Step 5: Final commit if any fixes needed**

Stage only the specific files that needed fixes (do NOT use `git add -A`):

```bash
# Only stage files that had regressions — list them explicitly
git add <specific-files-that-were-fixed>
git commit -m "fix: address regression issues from quality feedback loops integration"
```
