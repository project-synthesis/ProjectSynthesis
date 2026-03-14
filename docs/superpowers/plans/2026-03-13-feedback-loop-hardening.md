# Feedback Loop Hardening & Optimization — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the feedback loop system end-to-end — fix all audit bugs, wire dead integrations, build framework performance model, redesign frontend UX with three-tier progressive disclosure and result assessment engine.

**Architecture:** Two-phase approach. Phase 1 (Tasks 1-11) builds from the database up: schema changes → static config → algorithm hardening → pipeline integration → API layer → observability. Phase 2 (Tasks 12-17) rebuilds the frontend: store refactor → three-tier components → result assessment → toast integration. Each task produces a working, testable commit.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async (Column style), Pydantic v2, SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4, pytest + Hypothesis

**Codebase Conventions (MUST follow):**
- ORM models use `Column()` declarative style, NOT `Mapped[]`/`mapped_column()`. Import `from app.database import Base, utcnow`
- Config: `from app.config import settings` (module-level singleton). No `get_settings()` function exists
- `SCORE_DIMENSIONS` is defined in `prompt_diff.py` and imported from there by 4+ files. Keep it there as canonical source
- Asyncio: use `asyncio.get_running_loop()` (Python 3.14), NOT deprecated `asyncio.get_event_loop()`

**Spec:** `docs/superpowers/specs/2026-03-13-feedback-loop-hardening-design.md`

---

## Chunk 1: Foundation — Database, Models, Config, Framework Profiles

### Task 1: Database Schema & SQLAlchemy Models

**Files:**
- Modify: `backend/app/models/feedback.py:28-36` (UserAdaptation model)
- Modify: `backend/app/models/optimization.py:13-164` (Optimization model)
- Create: `backend/app/models/framework_performance.py`
- Create: `backend/app/models/adaptation_event.py`
- Test: `backend/tests/test_models_feedback.py`

- [ ] **Step 1: Write test for new UserAdaptation columns**

```python
# backend/tests/test_models_feedback.py
"""Tests for feedback-related SQLAlchemy models."""
import pytest
from sqlalchemy import inspect
from app.models.feedback import UserAdaptation


def test_user_adaptation_has_issue_frequency_column():
    """UserAdaptation must have issue_frequency for corrected issues tracking."""
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "issue_frequency" in columns


def test_user_adaptation_has_adaptation_version_column():
    """UserAdaptation must have adaptation_version for debounce versioning."""
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "adaptation_version" in columns


def test_user_adaptation_has_damping_columns():
    """UserAdaptation must track damping_level and consistency_score."""
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "damping_level" in columns
    assert "consistency_score" in columns


def test_user_adaptation_defaults():
    """Verify default values for new columns."""
    ua = UserAdaptation(user_id="test-user")
    assert ua.adaptation_version == 0
    assert ua.damping_level == 0.15
    assert ua.consistency_score == 0.5
    assert ua.issue_frequency is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_models_feedback.py -v`
Expected: FAIL — missing columns

- [ ] **Step 3: Update UserAdaptation model with new columns**

```python
# backend/app/models/feedback.py — update UserAdaptation class (line 28+)
# Add these 4 columns after the existing last_computed_at column:
    # --- New columns for hardened feedback loop ---
    issue_frequency = Column(Text, nullable=True)  # JSON: {issue_id: count}
    adaptation_version = Column(Integer, default=0)
    damping_level = Column(Float, default=0.15)
    consistency_score = Column(Float, default=0.5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_models_feedback.py -v`
Expected: PASS

- [ ] **Step 5: Write test for FrameworkPerformance model**

```python
# backend/tests/test_models_feedback.py — append
from app.models.framework_performance import FrameworkPerformance


def test_framework_performance_unique_constraint():
    """Unique constraint on (user_id, task_type, framework)."""
    table = FrameworkPerformance.__table__
    unique_constraints = [
        c for c in table.constraints
        if hasattr(c, "columns") and len(c.columns) == 3
    ]
    assert len(unique_constraints) == 1
    col_names = {c.name for c in unique_constraints[0].columns}
    assert col_names == {"user_id", "task_type", "framework"}


def test_framework_performance_has_all_columns():
    """FrameworkPerformance must have all required columns."""
    columns = {c.name for c in inspect(FrameworkPerformance).columns}
    expected = {
        "id", "user_id", "task_type", "framework",
        "avg_scores", "user_rating_avg", "issue_frequency",
        "sample_count", "elasticity_snapshot", "last_updated",
    }
    assert expected.issubset(columns)


def test_framework_performance_defaults():
    """Verify default values."""
    fp = FrameworkPerformance(
        id="test-id",
        user_id="test-user",
        task_type="coding",
        framework="chain-of-thought",
    )
    assert fp.user_rating_avg == 0.0
    assert fp.sample_count == 0
```

- [ ] **Step 6: Create FrameworkPerformance model**

```python
# backend/app/models/framework_performance.py
"""Framework performance tracking model — per-user, per-task, per-framework."""
import uuid

from sqlalchemy import Column, DateTime, Float, Index, Integer, Text, UniqueConstraint

from app.database import Base, utcnow


class FrameworkPerformance(Base):
    __tablename__ = "framework_performance"
    __table_args__ = (
        UniqueConstraint("user_id", "task_type", "framework", name="uq_fw_perf_user_task_fw"),
        Index("ix_framework_perf_user_task", "user_id", "task_type"),
    )

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, nullable=False)
    task_type = Column(Text, nullable=False)
    framework = Column(Text, nullable=False)
    avg_scores = Column(Text, nullable=True)  # JSON: {dim: float}
    user_rating_avg = Column(Float, default=0.0)
    issue_frequency = Column(Text, nullable=True)  # JSON: {issue_id: count}
    sample_count = Column(Integer, default=0)
    elasticity_snapshot = Column(Text, nullable=True)  # JSON: {dim: float}
    last_updated = Column(DateTime, default=utcnow)
```

- [ ] **Step 7: Write test for AdaptationEvent model**

```python
# backend/tests/test_models_feedback.py — append
from app.models.adaptation_event import AdaptationEvent


def test_adaptation_event_has_index():
    """AdaptationEvent must have user_id + created_at index."""
    indexes = {idx.name for idx in AdaptationEvent.__table__.indexes}
    assert "ix_adaptation_events_user_created" in indexes


def test_adaptation_event_defaults():
    """created_at should default to utcnow."""
    evt = AdaptationEvent(
        id="test-evt",
        user_id="test-user",
        event_type="recomputed",
    )
    assert evt.event_type == "recomputed"
```

- [ ] **Step 8: Create AdaptationEvent model**

```python
# backend/app/models/adaptation_event.py
"""Adaptation audit trail — 90-day retention, purged during recompute."""
import uuid

from sqlalchemy import Column, DateTime, Index, Text

from app.database import Base, utcnow


class AdaptationEvent(Base):
    __tablename__ = "adaptation_events"
    __table_args__ = (
        Index("ix_adaptation_events_user_created", "user_id", "created_at"),
    )

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)  # 'recomputed', 'feedback_received', etc.
    payload = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, default=utcnow, nullable=False)
```

- [ ] **Step 9: Add framework and active_guardrails columns to Optimization model**

```python
# backend/app/models/optimization.py — add after existing columns (around line 40)
    framework = Column(Text, nullable=True)
    active_guardrails = Column(Text, nullable=True)  # JSON: [issue_ids]
```

- [ ] **Step 10: Add feedback optimization_id index**

```python
# backend/app/models/feedback.py — ADD to existing __table_args__ tuple (line 22-25)
# The existing tuple has UniqueConstraint + ix_feedback_user_created.
# Add the third entry:
    __table_args__ = (
        UniqueConstraint("optimization_id", "user_id", name="uq_feedback_opt_user"),  # existing
        Index("ix_feedback_user_created", "user_id", "created_at"),                    # existing
        Index("ix_feedback_optimization_id", "optimization_id"),                        # NEW
    )
```

- [ ] **Step 11: Register new models in base imports**

```python
# backend/app/models/__init__.py — add imports
from app.models.framework_performance import FrameworkPerformance  # noqa: F401
from app.models.adaptation_event import AdaptationEvent  # noqa: F401
```

- [ ] **Step 12: Run all model tests**

Run: `cd backend && pytest tests/test_models_feedback.py -v`
Expected: ALL PASS

- [ ] **Step 13: Verify DB table creation**

Run: `cd backend && python -c "from app.database import Base; from app.models import *; print([t for t in Base.metadata.tables])"`
Expected: includes `framework_performance`, `adaptation_events`

- [ ] **Step 14: Commit**

```bash
git add backend/app/models/framework_performance.py backend/app/models/adaptation_event.py backend/app/models/feedback.py backend/app/models/optimization.py backend/app/models/__init__.py backend/tests/test_models_feedback.py
git commit -m "feat: add framework_performance and adaptation_event models, extend UserAdaptation"
```

---

### Task 2: Framework Profiles Static Config

**Files:**
- Create: `backend/app/services/framework_profiles.py`
- Test: `backend/tests/test_framework_profiles.py`

- [ ] **Step 1: Write tests for framework profiles**

```python
# backend/tests/test_framework_profiles.py
"""Tests for framework validation profiles — static config."""
import pytest
from app.services.framework_profiles import (
    FRAMEWORK_PROFILES,
    DEFAULT_FRAMEWORK_PROFILE,
    CORRECTABLE_ISSUES,
    ISSUE_DIMENSION_MAP,
    FRAMEWORK_TRADE_OFF_PATTERNS,
    get_profile,
    SCORE_DIMENSIONS,
)


class TestFrameworkProfiles:
    """Framework profile lookup and structure."""

    def test_all_known_frameworks_have_profiles(self):
        """Every framework from strategy_selector.py must have a profile."""
        known = {
            "chain-of-thought", "step-by-step", "persona-assignment",
            "CO-STAR", "RISEN", "structured-output", "constraint-injection",
            "few-shot-scaffolding", "context-enrichment", "role-task-format",
        }
        assert known.issubset(set(FRAMEWORK_PROFILES.keys()))

    def test_default_profile_has_neutral_multipliers(self):
        """Default profile must not bias any dimension."""
        assert DEFAULT_FRAMEWORK_PROFILE["emphasis"] == {}
        assert DEFAULT_FRAMEWORK_PROFILE["de_emphasis"] == {}
        assert DEFAULT_FRAMEWORK_PROFILE["entropy_tolerance"] == 1.0

    def test_get_profile_returns_known_framework(self):
        """get_profile returns the specific profile for known frameworks."""
        profile = get_profile("chain-of-thought")
        assert profile["emphasis"]["structure_score"] == 1.3

    def test_get_profile_returns_default_for_unknown(self):
        """get_profile returns DEFAULT_FRAMEWORK_PROFILE for unknown frameworks."""
        profile = get_profile("nonexistent-framework")
        assert profile is DEFAULT_FRAMEWORK_PROFILE

    def test_all_emphasis_keys_are_valid_dimensions(self):
        """Emphasis/de-emphasis keys must be valid SCORE_DIMENSIONS."""
        for fw, profile in FRAMEWORK_PROFILES.items():
            for dim in profile.get("emphasis", {}):
                assert dim in SCORE_DIMENSIONS, f"{fw}: {dim} not in SCORE_DIMENSIONS"
            for dim in profile.get("de_emphasis", {}):
                assert dim in SCORE_DIMENSIONS, f"{fw}: {dim} not in SCORE_DIMENSIONS"

    def test_entropy_tolerance_in_valid_range(self):
        """Entropy tolerance must be 0.5-1.5."""
        for fw, profile in FRAMEWORK_PROFILES.items():
            et = profile["entropy_tolerance"]
            assert 0.5 <= et <= 1.5, f"{fw}: entropy_tolerance {et} out of range"


class TestCorrectableIssues:
    """Correctable issues registry."""

    def test_has_eight_issues(self):
        assert len(CORRECTABLE_ISSUES) == 8

    def test_all_issue_ids_are_snake_case(self):
        for issue_id in CORRECTABLE_ISSUES:
            assert issue_id == issue_id.lower().replace(" ", "_")
            assert "-" not in issue_id

    def test_all_issues_mapped_to_dimensions(self):
        """Every issue must have at least one dimension mapping."""
        for issue_id in CORRECTABLE_ISSUES:
            assert issue_id in ISSUE_DIMENSION_MAP, f"{issue_id} not in ISSUE_DIMENSION_MAP"
            assert len(ISSUE_DIMENSION_MAP[issue_id]) >= 1

    def test_dimension_map_values_are_valid_dimensions(self):
        """ISSUE_DIMENSION_MAP values must be valid SCORE_DIMENSIONS."""
        for issue_id, dim_map in ISSUE_DIMENSION_MAP.items():
            for dim in dim_map:
                assert dim in SCORE_DIMENSIONS, f"{issue_id}: {dim} not valid"

    def test_dimension_map_values_are_positive(self):
        """All directional weights must be positive (boost, not penalize)."""
        for issue_id, dim_map in ISSUE_DIMENSION_MAP.items():
            for dim, weight in dim_map.items():
                assert weight > 0, f"{issue_id}.{dim} weight must be positive"


class TestTradeOffPatterns:
    """Framework trade-off patterns."""

    def test_patterns_reference_valid_frameworks(self):
        for fw in FRAMEWORK_TRADE_OFF_PATTERNS:
            assert fw in FRAMEWORK_PROFILES, f"{fw} not a known framework"

    def test_patterns_reference_valid_dimensions(self):
        for fw, patterns in FRAMEWORK_TRADE_OFF_PATTERNS.items():
            for gained, lost in patterns:
                assert gained in SCORE_DIMENSIONS, f"{fw}: {gained} invalid"
                assert lost in SCORE_DIMENSIONS, f"{fw}: {lost} invalid"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_framework_profiles.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create framework_profiles.py**

```python
# backend/app/services/framework_profiles.py
"""Static framework validation profiles, correctable issues, and trade-off patterns.

This module defines the domain knowledge that connects frameworks to quality
dimensions, issues to dimension weights, and frameworks to typical trade-off
patterns. All values are tunable constants — no runtime computation.
"""
from __future__ import annotations

# Import from prompt_diff — single source of truth (4+ files import from there)
from app.services.prompt_diff import SCORE_DIMENSIONS

# ---------------------------------------------------------------------------
# Framework validation profiles
# ---------------------------------------------------------------------------

DEFAULT_FRAMEWORK_PROFILE: dict = {
    "emphasis": {},
    "de_emphasis": {},
    "entropy_tolerance": 1.0,
}

FRAMEWORK_PROFILES: dict[str, dict] = {
    "chain-of-thought": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "step-by-step": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "persona-assignment": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"structure_score": 0.9},
        "entropy_tolerance": 1.2,
    },
    "CO-STAR": {
        "emphasis": {"clarity_score": 1.2, "faithfulness_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 1.0,
    },
    "RISEN": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {},
        "entropy_tolerance": 0.9,
    },
    "structured-output": {
        "emphasis": {"structure_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"clarity_score": 0.9},
        "entropy_tolerance": 0.8,
    },
    "constraint-injection": {
        "emphasis": {"specificity_score": 1.3, "faithfulness_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 0.9,
    },
    "few-shot-scaffolding": {
        "emphasis": {"specificity_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.75},
        "entropy_tolerance": 1.1,
    },
    "context-enrichment": {
        "emphasis": {"faithfulness_score": 1.2, "specificity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 1.0,
    },
    "role-task-format": {
        "emphasis": {"structure_score": 1.2, "clarity_score": 1.1},
        "de_emphasis": {},
        "entropy_tolerance": 1.0,
    },
}


def get_profile(framework: str) -> dict:
    """Return the validation profile for a framework, with fallback to default."""
    return FRAMEWORK_PROFILES.get(framework, DEFAULT_FRAMEWORK_PROFILE)


# ---------------------------------------------------------------------------
# Correctable issues
# ---------------------------------------------------------------------------

CORRECTABLE_ISSUES: dict[str, str] = {
    # Fidelity group
    "lost_key_terms": "Lost important terminology or domain language",
    "changed_meaning": "Changed the original intent or meaning",
    "hallucinated_content": "Added claims or details not in the original",
    "lost_examples": "Removed or weakened important examples",
    # Quality group
    "too_verbose": "Unnecessarily long or repetitive",
    "too_vague": "Lost specificity or important details",
    "wrong_tone": "Tone doesn't match intended audience",
    "broken_structure": "Formatting, flow, or organization degraded",
}

ISSUE_DIMENSION_MAP: dict[str, dict[str, float]] = {
    "lost_key_terms": {"faithfulness_score": 1.0, "specificity_score": 0.5},
    "changed_meaning": {"faithfulness_score": 1.0},
    "hallucinated_content": {"faithfulness_score": 0.8, "specificity_score": 0.3},
    "lost_examples": {"specificity_score": 1.0, "faithfulness_score": 0.3},
    "too_verbose": {"conciseness_score": 1.0},
    "too_vague": {"specificity_score": 1.0, "clarity_score": 0.3},
    "wrong_tone": {"clarity_score": 1.0},
    "broken_structure": {"structure_score": 1.0},
}

# Issue effect labels for user-facing confirmation toasts
ISSUE_EFFECT_LABELS: dict[str, str] = {
    "lost_key_terms": "term preservation guardrail activated",
    "changed_meaning": "meaning fidelity check activated",
    "hallucinated_content": "addition prevention guardrail activated",
    "lost_examples": "example preservation prioritized",
    "too_verbose": "conciseness priority increased",
    "too_vague": "specificity priority increased",
    "wrong_tone": "tone matching prioritized",
    "broken_structure": "structure preservation prioritized",
}

# Guardrail text injected into optimizer prompt when issues are frequent
ISSUE_GUARDRAILS: dict[str, str] = {
    "lost_key_terms": (
        "PRESERVE all domain-specific terminology, acronyms, and "
        "technical phrases from the original. Do not paraphrase "
        "specialized language."
    ),
    "changed_meaning": (
        "The optimized prompt must produce the SAME behavioral outcome "
        "as the original. Verify intent preservation before restructuring."
    ),
    "hallucinated_content": (
        "Do NOT add requirements, constraints, examples, or claims "
        "that are not present in the original prompt."
    ),
    "lost_examples": (
        "Preserve all examples from the original prompt. If restructuring, "
        "ensure examples remain functionally equivalent."
    ),
    "too_verbose": (
        "Prefer concise formulations. Remove redundancy. Every sentence "
        "must add information not conveyed elsewhere in the prompt."
    ),
    "too_vague": (
        "Maintain or increase specificity. Do not replace concrete details "
        "with abstract generalizations."
    ),
    "wrong_tone": (
        "Match the tone and register of the original prompt. Preserve "
        "the relationship with the intended audience."
    ),
    "broken_structure": (
        "Preserve the organizational structure of the original. If "
        "restructuring, ensure logical flow is maintained or improved."
    ),
}

# Short labels for UI display
ISSUE_GUARDRAILS_SHORT: dict[str, str] = {
    "lost_key_terms": "Term preservation",
    "changed_meaning": "Meaning fidelity",
    "hallucinated_content": "Addition prevention",
    "lost_examples": "Example preservation",
    "too_verbose": "Conciseness enforcement",
    "too_vague": "Specificity protection",
    "wrong_tone": "Tone matching",
    "broken_structure": "Structure preservation",
}

# Score-to-issue mapping for proactive suggestions
SCORE_ISSUE_MAP: dict[str, list[str]] = {
    "faithfulness_score": ["changed_meaning", "hallucinated_content"],
    "specificity_score": ["too_vague", "lost_examples"],
    "conciseness_score": ["too_verbose"],
    "clarity_score": ["wrong_tone"],
    "structure_score": ["broken_structure"],
}

# ---------------------------------------------------------------------------
# Framework trade-off patterns
# ---------------------------------------------------------------------------

FRAMEWORK_TRADE_OFF_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "chain-of-thought": [
        ("structure_score", "conciseness_score"),
        ("clarity_score", "conciseness_score"),
    ],
    "step-by-step": [
        ("structure_score", "conciseness_score"),
    ],
    "persona-assignment": [
        ("faithfulness_score", "conciseness_score"),
        ("specificity_score", "structure_score"),
    ],
    "CO-STAR": [
        ("clarity_score", "conciseness_score"),
        ("faithfulness_score", "conciseness_score"),
    ],
    "few-shot-scaffolding": [
        ("specificity_score", "conciseness_score"),
    ],
    "structured-output": [
        ("structure_score", "clarity_score"),
    ],
    "constraint-injection": [
        ("specificity_score", "conciseness_score"),
    ],
}


def is_typical_trade_off(framework: str, gained_dim: str, lost_dim: str) -> bool:
    """Check if a gain/loss pair is a typical trade-off for this framework."""
    patterns = FRAMEWORK_TRADE_OFF_PATTERNS.get(framework, [])
    return (gained_dim, lost_dim) in patterns
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_framework_profiles.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/framework_profiles.py backend/tests/test_framework_profiles.py
git commit -m "feat: add framework validation profiles, correctable issues, and trade-off patterns"
```

---

### Task 3: Pydantic Schema Updates

**Files:**
- Modify: `backend/app/schemas/feedback.py:14-99`
- Modify: `backend/app/schemas/mcp_models.py`
- Test: `backend/tests/test_feedback_api.py`

- [ ] **Step 1: Write tests for corrected_issues validation**

```python
# backend/tests/test_feedback_schemas.py
"""Tests for feedback Pydantic schemas — corrected_issues validation."""
import pytest
from pydantic import ValidationError
from app.schemas.feedback import FeedbackCreate


class TestFeedbackCreateSchema:
    """FeedbackCreate schema validation."""

    def test_valid_corrected_issues(self):
        fb = FeedbackCreate(
            rating=1,
            corrected_issues=["lost_key_terms", "too_verbose"],
        )
        assert fb.corrected_issues == ["lost_key_terms", "too_verbose"]

    def test_invalid_corrected_issue_rejected(self):
        with pytest.raises(ValidationError, match="Invalid issue"):
            FeedbackCreate(
                rating=-1,
                corrected_issues=["nonexistent_issue"],
            )

    def test_duplicate_corrected_issues_deduplicated(self):
        fb = FeedbackCreate(
            rating=-1,
            corrected_issues=["lost_key_terms", "lost_key_terms", "too_verbose"],
        )
        assert fb.corrected_issues == ["lost_key_terms", "too_verbose"]

    def test_null_corrected_issues_allowed(self):
        fb = FeedbackCreate(rating=1)
        assert fb.corrected_issues is None

    def test_empty_corrected_issues_allowed(self):
        fb = FeedbackCreate(rating=0, corrected_issues=[])
        assert fb.corrected_issues == []


class TestNewResponseSchemas:
    """New response schemas for observability."""

    def test_adaptation_pulse_schema(self):
        from app.schemas.feedback import AdaptationPulse
        pulse = AdaptationPulse(
            status="active",
            label="Adapted (8 feedbacks)",
            detail="Prioritizing Clarity",
        )
        assert pulse.status == "active"

    def test_feedback_confirmation_schema(self):
        from app.schemas.feedback import FeedbackConfirmation
        conf = FeedbackConfirmation(
            summary="Feedback saved — term preservation guardrail activated",
            effects=["term preservation guardrail activated"],
            stage_note="(2/3 feedbacks for full adaptation)",
        )
        assert len(conf.effects) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_feedback_schemas.py -v`
Expected: FAIL

- [ ] **Step 3: Update FeedbackCreate schema with corrected_issues validation**

IMPORTANT: Preserve all existing classes (`DimensionDelta`, `RetryHistoryEntry`, `InstructionCompliance`) — they are used by other code. Only modify `FeedbackCreate` and add new classes at the end of the file.

```python
# backend/app/schemas/feedback.py — targeted modifications (NOT a full rewrite)
# 1. Add import at top of file:
from app.services.framework_profiles import CORRECTABLE_ISSUES


class FeedbackCreate(BaseModel):
    """Input schema for feedback submission."""
    rating: Literal[-1, 0, 1]
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> "FeedbackCreate":
        # Validate dimension overrides
        if self.dimension_overrides:
            for key, value in self.dimension_overrides.items():
                if key not in VALID_DIMENSIONS:
                    msg = f"Invalid dimension: {key}. Valid: {sorted(VALID_DIMENSIONS)}"
                    raise ValueError(msg)
                if not isinstance(value, int) or not (1 <= value <= 10):
                    msg = f"Override value for {key} must be integer 1-10, got {value}"
                    raise ValueError(msg)
        # Validate corrected issues
        if self.corrected_issues is not None:
            seen = []
            for issue in self.corrected_issues:
                if issue not in CORRECTABLE_ISSUES:
                    msg = f"Invalid issue: {issue}. Valid: {sorted(CORRECTABLE_ISSUES)}"
                    raise ValueError(msg)
                if issue not in seen:
                    seen.append(issue)
            self.corrected_issues = seen
        return self


class FeedbackResponse(BaseModel):
    """Individual feedback record."""
    id: str
    optimization_id: str
    user_id: str
    rating: int
    dimension_overrides: dict[str, Any] | None = None
    corrected_issues: list[str] | None = None
    comment: str | None = None
    created_at: datetime | None = None


class FeedbackAggregate(BaseModel):
    """Aggregated feedback stats for an optimization."""
    total_ratings: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    avg_dimension_overrides: dict[str, float] | None = None


class FeedbackWithAggregate(BaseModel):
    """Combined user feedback + aggregate for an optimization."""
    feedback: FeedbackResponse | None = None
    aggregate: FeedbackAggregate
    suggestions: list[dict] | None = None  # Proactive issue suggestions


class AdaptationPulse(BaseModel):
    """L0 observability — always-visible status pulse."""
    status: Literal["inactive", "learning", "active"]
    label: str
    detail: str


class AdaptationSummary(BaseModel):
    """L2 observability — human-readable adaptation dashboard."""
    feedback_count: int = 0
    priorities: list[dict] = []
    active_guardrails: list[dict] = []
    framework_preferences: list[dict] = []
    top_frameworks: list[dict] = []
    issue_resolution: list[dict] = []
    retry_threshold: float = 5.0
    last_updated: datetime | None = None


class FeedbackConfirmation(BaseModel):
    """Response after feedback submission — drives toast content."""
    summary: str
    effects: list[str] = []
    stage_note: str | None = None


class AdaptationStateResponse(BaseModel):
    """Raw adaptation state for L3 diagnostics."""
    dimension_weights: dict[str, float] | None = None
    strategy_affinities: dict | None = None
    retry_threshold: float = 5.0
    feedback_count: int = 0
    issue_frequency: dict[str, int] | None = None
    damping_level: float = 0.15
    consistency_score: float = 0.5
    adaptation_version: int = 0


class FeedbackStatsResponse(BaseModel):
    """Aggregated feedback statistics."""
    total_feedbacks: int = 0
    rating_distribution: dict[int, int] = {}
    issue_frequency: dict[str, int] = {}
    # Deprecated — return null for one version cycle, remove in next release
    avg_override_delta: float | None = None
    most_corrected_dimension: str | None = None
    adaptation_state: AdaptationStateResponse | None = None
```

- [ ] **Step 4: Update MCP SubmitFeedbackInput with corrected_issues**

```python
# backend/app/schemas/mcp_models.py — update SubmitFeedbackInput class
# Find the existing SubmitFeedbackInput and add corrected_issues field:
class SubmitFeedbackInput(BaseModel):
    """Input for synthesis_submit_feedback MCP tool."""
    optimization_id: str = Field(min_length=1)
    rating: Literal[-1, 0, 1]
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = None  # NEW: predefined issue IDs
    comment: str | None = Field(None, max_length=2000)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_feedback_schemas.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/feedback.py backend/app/schemas/mcp_models.py backend/tests/test_feedback_schemas.py
git commit -m "feat: add corrected_issues validation to schemas, new observability response types"
```

---

### Task 4: Config Constants

**Files:**
- Modify: `backend/app/config.py:251`

- [ ] **Step 1: Add feedback loop config constants**

```python
# backend/app/config.py — add to Settings class

    # Feedback loop hardening
    ADAPTATION_DEBOUNCE_MS: int = 500
    ADAPTATION_MAX_REQUEUE: int = 1
    BASE_DAMPING: float = 0.065
    MAX_DAMPING: float = 0.15
    CONSISTENCY_CEILING_FACTOR: float = 1.2  # MAX_DAMPING * this = ceiling
    MIN_FEEDBACKS_FOR_ADAPTATION: int = 1
    ISSUE_WEIGHT_FACTOR: float = 0.04
    MAX_ISSUE_GUARDRAILS: int = 4
    MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL: int = 2
    MIN_ISSUE_FREQUENCY_FOR_SUGGESTION: int = 2
    ADAPTATION_EVENT_RETENTION_DAYS: int = 90
    FRAMEWORK_PERF_RECENCY_DECAY: float = 0.01  # exp(-0.01 * days)
    ELASTICITY_EMA_ALPHA: float = 0.4
    ELASTICITY_COLD_START: float = 0.5
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add feedback loop hardening config constants"
```

---

## Chunk 2: Algorithm Hardening — Prompt Diff, Damping, Adaptation Engine

### Task 5: Prompt Diff Overhaul

**Files:**
- Modify: `backend/app/services/prompt_diff.py:1-75`
- Test: `backend/tests/test_prompt_diff.py`

- [ ] **Step 1: Write tests for compute_prompt_divergence**

```python
# backend/tests/test_prompt_diff.py
"""Tests for prompt diff utilities — divergence, cycles, hashing."""
import pytest
from app.services.prompt_diff import (
    compute_prompt_hash,
    compute_prompt_divergence,
    compute_dimension_deltas,
    detect_cycle,
    CycleResult,
    extract_structure,
)


class TestPromptDivergence:
    """Multi-signal divergence replaces old sentence-level entropy."""

    def test_identical_prompts_zero_divergence(self):
        assert compute_prompt_divergence("hello world", "hello world") == 0.0

    def test_completely_different_prompts_high_divergence(self):
        d = compute_prompt_divergence(
            "Write a Python function to sort a list",
            "Explain quantum mechanics in simple terms for children",
        )
        assert d > 0.7

    def test_minor_rephrasing_low_divergence(self):
        d = compute_prompt_divergence(
            "Write a function that sorts numbers in ascending order",
            "Write a function that sorts numbers in ascending sequence",
        )
        assert d < 0.3

    def test_structural_change_moderate_divergence(self):
        """Same words, different structure → moderate divergence."""
        flat = "First do A. Then do B. Finally do C."
        structured = "Steps:\n1. First do A\n2. Then do B\n3. Finally do C"
        d = compute_prompt_divergence(flat, structured)
        assert 0.2 < d < 0.6

    def test_empty_vs_content_max_divergence(self):
        d = compute_prompt_divergence("", "hello world")
        assert d == 1.0

    def test_both_empty_zero_divergence(self):
        assert compute_prompt_divergence("", "") == 0.0

    def test_returns_clamped_0_to_1(self):
        d = compute_prompt_divergence("a" * 1000, "b" * 5)
        assert 0.0 <= d <= 1.0


class TestExtractStructure:
    """Structural feature extraction."""

    def test_counts_lines(self):
        s = extract_structure("line 1\nline 2\nline 3")
        assert s["lines"] == 3

    def test_counts_paragraphs(self):
        s = extract_structure("para 1\n\npara 2\n\npara 3")
        assert s["paragraphs"] == 3

    def test_counts_list_items(self):
        s = extract_structure("- item 1\n- item 2\n1. item 3")
        assert s["lists"] == 3

    def test_counts_code_blocks(self):
        s = extract_structure("text\n```python\ncode\n```\nmore text")
        assert s["code_blocks"] == 1


class TestCycleDetection:
    """Hard and soft cycle detection."""

    def test_hard_cycle_exact_hash_match(self):
        h = compute_prompt_hash("same prompt")
        result = detect_cycle(h, [compute_prompt_hash("other"), h])
        assert result is not None
        assert result.type == "hard"
        assert result.matched_attempt == 2

    def test_no_cycle_different_hashes(self):
        result = detect_cycle(
            compute_prompt_hash("prompt a"),
            [compute_prompt_hash("prompt b"), compute_prompt_hash("prompt c")],
        )
        assert result is None

    def test_soft_cycle_low_divergence_no_deltas(self):
        """Divergence alone triggers soft cycle when no delta data available."""
        result = detect_cycle(
            compute_prompt_hash("unique hash"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
        )
        assert result is not None
        assert result.type == "soft"

    def test_soft_cycle_compound_condition(self):
        """Soft cycle requires BOTH low divergence AND low dimension deltas."""
        # Low divergence + low deltas → soft cycle
        result = detect_cycle(
            compute_prompt_hash("unique"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
            dimension_deltas={"clarity_score": 0.1},
        )
        assert result is not None
        assert result.type == "soft"

    def test_no_soft_cycle_high_deltas(self):
        """Low divergence but high deltas → NOT a soft cycle."""
        result = detect_cycle(
            compute_prompt_hash("unique"),
            [compute_prompt_hash("other")],
            current_divergence=0.05,
            dimension_deltas={"clarity_score": 2.0},
        )
        assert result is None

    def test_no_soft_cycle_above_threshold(self):
        result = detect_cycle(
            compute_prompt_hash("unique hash"),
            [compute_prompt_hash("other")],
            current_divergence=0.5,
        )
        assert result is None


class TestDimensionDeltas:
    """Dimension delta computation."""

    def test_computes_deltas_correctly(self):
        before = {"clarity_score": 5.0, "structure_score": 7.0}
        after = {"clarity_score": 8.0, "structure_score": 6.0}
        deltas = compute_dimension_deltas(before, after)
        assert deltas["clarity_score"] == 3.0
        assert deltas["structure_score"] == -1.0

    def test_missing_dimensions_skipped(self):
        deltas = compute_dimension_deltas(
            {"clarity_score": 5.0},
            {"structure_score": 7.0},
        )
        assert deltas == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_prompt_diff.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite prompt_diff.py**

```python
# backend/app/services/prompt_diff.py
"""Prompt diff utilities — hashing, divergence, cycle detection, deltas.

compute_prompt_divergence() replaces the old compute_prompt_entropy().
Uses multi-signal analysis: token overlap, structural similarity, length ratio.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Canonical score dimensions — single source of truth.
# Imported by: adaptation_engine, schemas/feedback, schemas/refinement, routers/refinement
SCORE_DIMENSIONS = (
    "clarity_score",
    "specificity_score",
    "structure_score",
    "faithfulness_score",
    "conciseness_score",
)


@dataclass(frozen=True)
class CycleResult:
    """Result of cycle detection — hard (exact hash) or soft (low divergence)."""
    type: str  # "hard" or "soft"
    matched_attempt: int | None = None  # 1-indexed for hard cycles
    divergence: float | None = None  # for soft cycles

    def __post_init__(self):
        assert self.type in ("hard", "soft")


# Threshold below which divergence indicates a soft cycle
SOFT_CYCLE_THRESHOLD = 0.10  # matches spec: entropy < 0.10


def compute_prompt_hash(prompt: str) -> str:
    """16-char SHA256 hash of normalized prompt text."""
    normalized = re.sub(r"\s+", " ", prompt.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def compute_dimension_deltas(
    before: dict[str, float],
    after: dict[str, float],
) -> dict[str, float]:
    """Compute per-dimension score deltas (after - before)."""
    return {
        dim: after[dim] - before[dim]
        for dim in before
        if dim in after
    }


def detect_cycle(
    current_hash: str,
    previous_hashes: list[str],
    current_divergence: float | None = None,
    dimension_deltas: dict[str, float] | None = None,
) -> CycleResult | None:
    """Detect hard (exact hash match) or soft (low divergence + low deltas) cycles.

    Args:
        current_hash: Hash of the current prompt.
        previous_hashes: Hashes of all previous attempts.
        current_divergence: Optional divergence score vs previous attempt.
        dimension_deltas: Optional {dim: delta} from current vs previous scores.

    Returns:
        CycleResult if cycle detected, None otherwise.
    """
    # Hard cycle: exact hash match
    for i, prev_hash in enumerate(previous_hashes):
        if current_hash == prev_hash:
            return CycleResult(type="hard", matched_attempt=i + 1)

    # Soft cycle: low divergence AND low dimension deltas (compound condition per spec)
    if current_divergence is not None and current_divergence < SOFT_CYCLE_THRESHOLD:
        if dimension_deltas is not None:
            max_delta = max(abs(d) for d in dimension_deltas.values()) if dimension_deltas else 0
            if max_delta < 0.3:
                return CycleResult(type="soft", divergence=current_divergence)
        else:
            # No delta data available — divergence alone is sufficient
            return CycleResult(type="soft", divergence=current_divergence)

    return None


def extract_structure(text: str) -> dict[str, int]:
    """Extract structural features from prompt text.

    Returns counts of lines, paragraphs, list items, and code blocks.
    """
    if not text:
        return {"lines": 0, "paragraphs": 0, "lists": 0, "code_blocks": 0}

    lines = text.count("\n") + 1
    paragraphs = len([p for p in text.split("\n\n") if p.strip()])
    lists = len(re.findall(r"^[\s]*[-*+]|\d+\.", text, re.MULTILINE))
    code_blocks = text.count("```") // 2

    return {
        "lines": lines,
        "paragraphs": paragraphs,
        "lists": lists,
        "code_blocks": code_blocks,
    }


def _normalized_struct_distance(a: dict[str, int], b: dict[str, int]) -> float:
    """Normalized structural distance between two prompts [0.0, 1.0]."""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    total_diff = 0.0
    total_max = 0.0
    for k in keys:
        va, vb = a.get(k, 0), b.get(k, 0)
        total_diff += abs(va - vb)
        total_max += max(va, vb, 1)
    return min(1.0, total_diff / total_max)


def compute_prompt_divergence(prompt_a: str, prompt_b: str) -> float:
    """Multi-signal divergence between two prompts [0.0, 1.0].

    Combines three signals:
    - Token-level Jaccard distance (50% weight): word overlap
    - Structural distance (30% weight): line/paragraph/list/code structure
    - Length ratio distance (20% weight): gross size changes

    Returns 0.0 for identical prompts, 1.0 for completely different.
    """
    if prompt_a == prompt_b:
        return 0.0
    if not prompt_a or not prompt_b:
        return 1.0

    # Signal 1: Token-level Jaccard distance
    tokens_a = set(re.sub(r"\s+", " ", prompt_a.strip().lower()).split())
    tokens_b = set(re.sub(r"\s+", " ", prompt_b.strip().lower()).split())
    if tokens_a or tokens_b:
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        token_jaccard = 1.0 - (intersection / union) if union > 0 else 1.0
    else:
        token_jaccard = 0.0

    # Signal 2: Structural similarity
    struct_a = extract_structure(prompt_a)
    struct_b = extract_structure(prompt_b)
    structural_delta = _normalized_struct_distance(struct_a, struct_b)

    # Signal 3: Length ratio
    len_a, len_b = len(prompt_a), len(prompt_b)
    length_delta = 1.0 - (min(len_a, len_b) / max(len_a, len_b))

    # Weighted blend
    divergence = (
        0.5 * token_jaccard
        + 0.3 * structural_delta
        + 0.2 * length_delta
    )
    return max(0.0, min(1.0, divergence))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_prompt_diff.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/prompt_diff.py backend/tests/test_prompt_diff.py
git commit -m "feat: replace prompt entropy with multi-signal divergence, add soft cycle detection"
```

---

### Task 6: Progressive Damping & Adaptation Engine Hardening

**Files:**
- Modify: `backend/app/services/adaptation_engine.py:1-324`
- Test: `backend/tests/test_adaptation_engine.py`

- [ ] **Step 1: Write tests for progressive damping**

```python
# backend/tests/test_progressive_damping.py
"""Tests for progressive damping — confidence-weighted adaptation."""
import pytest
from math import log
from app.services.adaptation_engine import compute_effective_damping
from app.models.feedback import Feedback


def _make_feedbacks(ratings: list[int]) -> list:
    """Create minimal feedback objects for testing."""
    return [type("F", (), {"rating": r})() for r in ratings]


class TestProgressiveDamping:
    """Progressive damping replaces MIN_FEEDBACKS_FOR_ADAPTATION=3."""

    def test_single_feedback_very_low_damping(self):
        """1 feedback should produce ~0.045 damping (very conservative)."""
        fbs = _make_feedbacks([1])
        d = compute_effective_damping(fbs)
        assert 0.02 < d < 0.08

    def test_three_feedbacks_moderate_damping(self):
        """3 consistent feedbacks should produce ~0.09-0.11."""
        fbs = _make_feedbacks([1, 1, 1])
        d = compute_effective_damping(fbs)
        assert 0.07 < d < 0.14

    def test_ten_feedbacks_near_max(self):
        """10 consistent feedbacks should approach MAX_DAMPING."""
        fbs = _make_feedbacks([1] * 10)
        d = compute_effective_damping(fbs)
        assert d >= 0.12

    def test_inconsistent_feedback_lowers_damping(self):
        """Mixed ratings should produce lower damping than consistent."""
        consistent = _make_feedbacks([1, 1, 1, 1, 1])
        inconsistent = _make_feedbacks([1, -1, 1, -1, 1])
        d_consistent = compute_effective_damping(consistent)
        d_inconsistent = compute_effective_damping(inconsistent)
        assert d_consistent > d_inconsistent

    def test_zero_feedbacks_returns_zero(self):
        assert compute_effective_damping([]) == 0.0

    def test_damping_never_exceeds_ceiling(self):
        """Even with 100 consistent feedbacks, damping is capped."""
        fbs = _make_feedbacks([1] * 100)
        d = compute_effective_damping(fbs)
        assert d <= 0.18  # CONSISTENCY_CEILING = MAX_DAMPING * 1.2

    def test_recent_consistency_weighted_more(self):
        """Recent feedback consistency matters more than old."""
        # Old inconsistent, recent consistent
        old_noisy = _make_feedbacks([-1, 1, -1, 1, -1, 1, 1, 1, 1, 1])
        # Old consistent, recent inconsistent
        recent_noisy = _make_feedbacks([1, 1, 1, 1, 1, -1, 1, -1, 1, -1])
        d_old_noisy = compute_effective_damping(old_noisy)
        d_recent_noisy = compute_effective_damping(recent_noisy)
        assert d_old_noisy > d_recent_noisy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_progressive_damping.py -v`
Expected: FAIL — `compute_effective_damping` not found

- [ ] **Step 3: Implement compute_effective_damping in adaptation_engine.py**

Add this function before `recompute_adaptation()` (around line 190):

```python
# backend/app/services/adaptation_engine.py — new function

def compute_effective_damping(feedbacks: list) -> float:
    """Compute effective damping based on feedback count and consistency.

    Progressive: more feedbacks → higher base damping.
    Confidence-weighted: consistent feedback → higher multiplier.

    Returns:
        Damping value in [0.0, CONSISTENCY_CEILING].
    """
    from app.config import settings

    n = len(feedbacks)
    if n == 0:
        return 0.0

    # Base: logarithmic ramp
    base = min(settings.MAX_DAMPING, settings.BASE_DAMPING * log(1 + n))

    if n < 2:
        # Single feedback: neutral consistency (0.5)
        consistency_multiplier = 0.5 + 0.7 * 0.5  # = 0.85
        ceiling = settings.MAX_DAMPING * settings.CONSISTENCY_CEILING_FACTOR
        return min(ceiling, base * consistency_multiplier)

    # Compute overall consistency
    ratings = [f.rating for f in feedbacks]
    mean_rating = sum(ratings) / n
    variance = sum((r - mean_rating) ** 2 for r in ratings) / n
    overall_consistency = 1.0 - min(1.0, variance)  # max variance for {-1,0,1} is ~1.0

    # Compute recent consistency (last 5)
    recent_window = min(5, n)
    recent = ratings[-recent_window:]
    recent_mean = sum(recent) / recent_window
    recent_var = sum((r - recent_mean) ** 2 for r in recent) / recent_window
    recent_consistency = 1.0 - min(1.0, recent_var)

    # Blend: 60% recent, 40% overall
    blended = 0.6 * recent_consistency + 0.4 * overall_consistency

    # Confidence multiplier: [0.5, 1.2]
    confidence_multiplier = 0.5 + 0.7 * blended

    # Apply ceiling
    ceiling = settings.MAX_DAMPING * settings.CONSISTENCY_CEILING_FACTOR
    return min(ceiling, base * confidence_multiplier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_progressive_damping.py -v`
Expected: ALL PASS

- [ ] **Step 5: Write tests for issue signal integration**

```python
# backend/tests/test_issue_signals.py
"""Tests for corrected issues → dimension weight integration."""
import pytest
from app.services.adaptation_engine import apply_issue_signals


class TestIssueSignals:
    """apply_issue_signals layers issue frequency onto override deltas."""

    def test_no_issues_returns_base_deltas(self):
        base = {"clarity_score": 0.1, "structure_score": -0.05}
        result = apply_issue_signals(base, {}, total_feedbacks=5)
        assert result == base

    def test_lost_key_terms_boosts_faithfulness(self):
        base = {"faithfulness_score": 0.0}
        result = apply_issue_signals(
            base,
            {"lost_key_terms": 3},
            total_feedbacks=10,
        )
        # 1.0 * (3/10) * 0.04 = 0.012
        assert result["faithfulness_score"] > 0

    def test_multiple_issues_accumulate(self):
        base = {"faithfulness_score": 0.0, "specificity_score": 0.0}
        result = apply_issue_signals(
            base,
            {"lost_key_terms": 3, "too_vague": 2},
            total_feedbacks=10,
        )
        assert result["faithfulness_score"] > 0
        assert result["specificity_score"] > 0

    def test_unknown_issue_ignored(self):
        base = {"clarity_score": 0.1}
        result = apply_issue_signals(base, {"unknown_issue": 5}, total_feedbacks=10)
        assert result == base

    def test_zero_total_feedbacks_safe(self):
        """Division by zero protection."""
        result = apply_issue_signals({}, {"lost_key_terms": 1}, total_feedbacks=0)
        assert isinstance(result, dict)
```

- [ ] **Step 6: Implement apply_issue_signals**

```python
# backend/app/services/adaptation_engine.py — new function

def apply_issue_signals(
    base_deltas: dict[str, float],
    issue_frequency: dict[str, int],
    total_feedbacks: int,
) -> dict[str, float]:
    """Layer corrected issue frequency onto override deltas.

    Issues produce a directional signal: recurring 'lost_key_terms'
    boosts faithfulness_score weight proportional to frequency ratio.

    Args:
        base_deltas: Override deltas from compute_override_deltas().
        issue_frequency: {issue_id: count} from UserAdaptation.
        total_feedbacks: Total feedback count for normalization.

    Returns:
        Augmented deltas dict (new copy, base_deltas is not mutated).
    """
    from app.config import settings
    from app.services.framework_profiles import ISSUE_DIMENSION_MAP

    augmented = dict(base_deltas)

    for issue_id, count in issue_frequency.items():
        if issue_id not in ISSUE_DIMENSION_MAP:
            continue
        frequency_ratio = count / max(total_feedbacks, 1)
        for dim, direction in ISSUE_DIMENSION_MAP[issue_id].items():
            augmented[dim] = augmented.get(dim, 0.0) + (
                direction * frequency_ratio * settings.ISSUE_WEIGHT_FACTOR
            )

    return augmented
```

- [ ] **Step 7: Run issue signal tests**

Run: `cd backend && pytest tests/test_issue_signals.py -v`
Expected: ALL PASS

- [ ] **Step 8: Write tests for debounced adaptation versioning**

```python
# backend/tests/test_adaptation_debounce.py
"""Tests for debounced adaptation recomputation."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from app.services.adaptation_engine import (
    _debounce_handles,
    _adaptation_versions,
    schedule_adaptation_recompute,
)


@pytest.mark.asyncio
class TestAdaptationDebounce:
    """Debounced recomputation with version tracking."""

    async def test_schedule_increments_version(self):
        """Each schedule call increments the adaptation version."""
        user_id = "test-debounce-user"
        _adaptation_versions.pop(user_id, None)
        _debounce_handles.pop(user_id, None)

        with patch("app.services.adaptation_engine.recompute_adaptation_safe", new_callable=AsyncMock):
            schedule_adaptation_recompute(user_id)
            assert _adaptation_versions.get(user_id, 0) >= 1

            schedule_adaptation_recompute(user_id)
            assert _adaptation_versions.get(user_id, 0) >= 2

        # Cleanup
        handle = _debounce_handles.pop(user_id, None)
        if handle:
            handle.cancel()

    async def test_rapid_schedules_cancel_previous(self):
        """Rapid scheduling cancels previous timer."""
        user_id = "test-rapid-user"
        _debounce_handles.pop(user_id, None)

        with patch("app.services.adaptation_engine.recompute_adaptation_safe", new_callable=AsyncMock) as mock_recompute:
            schedule_adaptation_recompute(user_id)
            first_handle = _debounce_handles.get(user_id)

            schedule_adaptation_recompute(user_id)
            second_handle = _debounce_handles.get(user_id)

            # First handle should be cancelled
            assert first_handle is not second_handle

        # Cleanup
        handle = _debounce_handles.pop(user_id, None)
        if handle:
            handle.cancel()
```

- [ ] **Step 9: Implement debounce scheduling in adaptation_engine.py**

Add near the top of the file (after imports, before existing functions):

```python
# backend/app/services/adaptation_engine.py — debounce infrastructure

import asyncio
import logging
from math import log

logger = logging.getLogger(__name__)

# Per-user debounce state (module-level)
_debounce_handles: dict[str, asyncio.TimerHandle] = {}
_adaptation_versions: dict[str, int] = {}
_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Get or create a per-user asyncio lock."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def schedule_adaptation_recompute(user_id: str) -> None:
    """Schedule a debounced adaptation recomputation.

    Cancels any pending timer for this user before scheduling a new one.
    The actual recompute fires after ADAPTATION_DEBOUNCE_MS.
    """
    from app.config import settings

    # Increment version
    _adaptation_versions[user_id] = _adaptation_versions.get(user_id, 0) + 1
    version = _adaptation_versions[user_id]

    # Cancel existing timer
    existing = _debounce_handles.get(user_id)
    if existing:
        existing.cancel()

    # Schedule new timer
    loop = asyncio.get_running_loop()
    delay = settings.ADAPTATION_DEBOUNCE_MS / 1000.0

    def _fire():
        asyncio.ensure_future(_debounced_recompute(user_id, version))

    handle = loop.call_later(delay, _fire)
    _debounce_handles[user_id] = handle

    logger.debug(
        "adaptation_debounce_scheduled",
        extra={"user_id": user_id, "version": version, "delay_ms": settings.ADAPTATION_DEBOUNCE_MS},
    )


async def _debounced_recompute(user_id: str, scheduled_version: int) -> None:
    """Execute recomputation if version hasn't changed since scheduling."""
    current_version = _adaptation_versions.get(user_id, 0)
    if current_version != scheduled_version:
        logger.debug(
            "adaptation_skipped_debounce",
            extra={"user_id": user_id, "scheduled": scheduled_version, "current": current_version},
        )
        return

    await recompute_adaptation_safe(user_id)

    # Check if new feedback arrived during computation
    post_version = _adaptation_versions.get(user_id, 0)
    if post_version != scheduled_version:
        from app.config import settings
        if settings.ADAPTATION_MAX_REQUEUE > 0:
            logger.info(
                "adaptation_requeue",
                extra={"user_id": user_id, "reason": "version_changed_during_compute"},
            )
            await recompute_adaptation_safe(user_id)

    # Cleanup handle
    _debounce_handles.pop(user_id, None)
```

- [ ] **Step 10: Run debounce tests**

Run: `cd backend && pytest tests/test_adaptation_debounce.py -v`
Expected: ALL PASS

- [ ] **Step 11: Update recompute_adaptation to use progressive damping and issue signals**

Update the existing `recompute_adaptation()` function to:
1. Use `compute_effective_damping()` instead of hardcoded `MAX_DAMPING`
2. Call `apply_issue_signals()` on the override deltas
3. Aggregate and persist `issue_frequency` to `UserAdaptation`
4. Log structured events
5. Purge old `AdaptationEvent` rows (90-day retention)

```python
# Key changes inside recompute_adaptation() — pseudocode for the diff:
# 1. Replace: damping = MAX_DAMPING
#    With:    damping = compute_effective_damping(feedbacks)
#
# 2. After compute_override_deltas():
#    issue_freq = _aggregate_issue_frequency(feedbacks)
#    deltas = apply_issue_signals(deltas, issue_freq, len(feedbacks))
#
# 3. Before upsert:
#    adaptation.issue_frequency = json.dumps(issue_freq)
#    adaptation.damping_level = damping
#    adaptation.consistency_score = blended_consistency
#    adaptation.adaptation_version = _adaptation_versions.get(user_id, 0)
#
# 4. After upsert:
#    await _record_adaptation_event(user_id, db, "recomputed", {...})
#    await _purge_old_events(user_id, db)
```

The exact implementation will modify `recompute_adaptation()` at lines 197-290. The implementer should read the existing function, then apply these changes while preserving the existing weight computation, threshold computation, and affinity computation logic.

- [ ] **Step 12: Run all adaptation tests**

Run: `cd backend && pytest tests/test_adaptation_engine.py tests/test_progressive_damping.py tests/test_issue_signals.py tests/test_adaptation_debounce.py -v`
Expected: ALL PASS

- [ ] **Step 13: Commit**

```bash
git add backend/app/services/adaptation_engine.py backend/tests/test_progressive_damping.py backend/tests/test_issue_signals.py backend/tests/test_adaptation_debounce.py
git commit -m "feat: progressive damping, issue signals, debounced adaptation recompute"
```

---

## Chunk 3: Retry Oracle & Pipeline Integration

### Task 7: Retry Oracle Hardening

**Files:**
- Modify: `backend/app/services/retry_oracle.py:1-358`
- Test: `backend/tests/test_retry_oracle.py` (extend existing)

- [ ] **Step 1: Write tests for framework-aware elasticity tracking**

```python
# backend/tests/test_oracle_elasticity.py
"""Tests for framework-aware elasticity in RetryOracle."""
import pytest
from app.services.retry_oracle import RetryOracle


class TestElasticityTracking:
    """Elasticity tracked for ALL dimensions, per-framework."""

    def test_elasticity_updates_all_dimensions(self):
        """After recording two attempts, elasticity should exist for all scored dims."""
        oracle = RetryOracle(max_retries=3, threshold=7.0, framework="chain-of-thought")
        scores_1 = {"clarity_score": 5.0, "structure_score": 6.0, "conciseness_score": 7.0,
                     "faithfulness_score": 6.0, "specificity_score": 5.5}
        scores_2 = {"clarity_score": 7.0, "structure_score": 6.5, "conciseness_score": 6.5,
                     "faithfulness_score": 7.0, "specificity_score": 5.5}
        oracle.record_attempt(scores_1, "prompt 1", [])
        oracle.record_attempt(scores_2, "prompt 2", [])

        # All dimensions should have elasticity data
        for dim in scores_1:
            assert oracle.get_elasticity("chain-of-thought", dim) is not None

    def test_high_change_produces_high_elasticity(self):
        """Dimensions that changed a lot should have high elasticity."""
        oracle = RetryOracle(max_retries=3, threshold=7.0, framework="chain-of-thought")
        oracle.record_attempt({"clarity_score": 3.0, "conciseness_score": 8.0}, "p1", [])
        oracle.record_attempt({"clarity_score": 8.0, "conciseness_score": 7.8}, "p2", [])

        clarity_e = oracle.get_elasticity("chain-of-thought", "clarity_score")
        conciseness_e = oracle.get_elasticity("chain-of-thought", "conciseness_score")
        assert clarity_e > conciseness_e

    def test_framework_aware_focus_selection(self):
        """Focus should not suggest dimensions the framework de-emphasizes."""
        oracle = RetryOracle(
            max_retries=3, threshold=7.0, framework="chain-of-thought",
            user_weights={"clarity_score": 0.3, "structure_score": 0.25,
                          "faithfulness_score": 0.2, "specificity_score": 0.15,
                          "conciseness_score": 0.1},
        )
        # Low conciseness but it's de-emphasized for chain-of-thought
        oracle.record_attempt(
            {"clarity_score": 5.0, "structure_score": 5.0, "conciseness_score": 3.0,
             "faithfulness_score": 5.0, "specificity_score": 5.0},
            "p1", [],
        )
        focus = oracle._select_focus_areas()
        # conciseness should NOT be suggested despite being lowest
        assert "conciseness_score" not in focus


class TestGateEnum:
    """Gate names returned as structured enum, not string matching."""

    def test_should_retry_returns_gate_name(self):
        oracle = RetryOracle(max_retries=1, threshold=5.0, framework="chain-of-thought")
        oracle.record_attempt(
            {"clarity_score": 8.0, "structure_score": 8.0, "conciseness_score": 8.0,
             "faithfulness_score": 8.0, "specificity_score": 8.0},
            "prompt", [],
        )
        decision = oracle.should_retry()
        assert hasattr(decision, "gate")
        assert decision.gate is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_oracle_elasticity.py -v`
Expected: FAIL

- [ ] **Step 3: Update RetryOracle with framework-aware elasticity and gate enums**

Key changes to `retry_oracle.py`:

1. Add `GateName` enum: `THRESHOLD_MET`, `BUDGET_EXHAUSTED`, `CYCLE_DETECTED`, `CREATIVE_EXHAUSTION`, `NEGATIVE_MOMENTUM`, `ZERO_SUM_TRAP`, `DIMINISHING_RETURNS`, `FRAMEWORK_MISMATCH`
2. Add `framework` parameter to `__init__`
3. Add `elasticity_matrix: dict[str, dict[str, float]]` instance variable
4. Update `record_attempt()` to track elasticity for ALL dimensions
5. Update `_select_focus_areas()` to use framework emphasis multipliers
6. Update `should_retry()` to return `gate: GateName` in `RetryDecision`
7. Fix Gate 2 off-by-one (`>=` not `>`)
8. Add Gate 0 advisory pre-check
9. Remove dead `task_baseline` parameter
10. Use `compute_prompt_divergence()` instead of `compute_prompt_entropy()`

The implementer should read the existing `retry_oracle.py` (358 lines) and apply these changes incrementally, running `pytest tests/test_retry_oracle.py tests/test_oracle_elasticity.py -v` after each change.

- [ ] **Step 4: Run all oracle tests**

Run: `cd backend && pytest tests/test_retry_oracle.py tests/test_oracle_elasticity.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retry_oracle.py backend/tests/test_oracle_elasticity.py
git commit -m "feat: framework-aware elasticity, gate enums, divergence-based cycle detection in retry oracle"
```

---

### Task 8: Pipeline Integration — Wiring Adaptation Into All Stages

**Files:**
- Modify: `backend/app/services/pipeline.py:182-757`
- Modify: `backend/app/services/strategy_selector.py:191-346`
- Modify: `backend/app/services/optimizer.py:313-529`
- Modify: `backend/app/services/validator.py:74-205`
- Test: `backend/tests/test_pipeline_adaptation.py`

- [ ] **Step 1: Write integration test for adaptation flow through pipeline**

```python
# backend/tests/test_pipeline_adaptation.py
"""Integration tests for adaptation state flowing through all pipeline stages."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.framework_profiles import get_profile


class TestStrategyAffinityInjection:
    """Strategy stage must inject affinities into LLM prompt."""

    def test_affinity_text_generated_for_known_task(self):
        from app.services.strategy_selector import build_affinity_prompt_section
        affinities = {
            "coding": {"preferred": ["chain-of-thought"], "avoid": ["few-shot-scaffolding"]},
        }
        text = build_affinity_prompt_section("coding", affinities)
        assert "chain-of-thought" in text
        assert "few-shot-scaffolding" in text
        assert "preferred" in text.lower() or "Preferred" in text

    def test_no_affinity_for_unknown_task(self):
        from app.services.strategy_selector import build_affinity_prompt_section
        text = build_affinity_prompt_section("unknown_task", {})
        assert text == ""


class TestOptimizerHints:
    """Optimizer must receive framework profile + user weights."""

    def test_build_optimizer_hints(self):
        from app.services.optimizer import build_adaptation_hints
        hints = build_adaptation_hints(
            framework_profile=get_profile("chain-of-thought"),
            user_weights={"clarity_score": 0.3, "conciseness_score": 0.1},
            issue_guardrails=["PRESERVE all domain-specific terminology..."],
        )
        assert "clarity" in hints.lower()
        assert "PRESERVE" in hints

    def test_no_hints_without_adaptation(self):
        from app.services.optimizer import build_adaptation_hints
        hints = build_adaptation_hints(None, None, [])
        assert hints == ""


class TestValidatorFrameworkCalibration:
    """Validator must apply framework profile × user weights."""

    def test_effective_weights_combine_profile_and_user(self):
        from app.services.validator import compute_effective_weights
        profile = get_profile("chain-of-thought")
        user_weights = {
            "clarity_score": 0.25,
            "specificity_score": 0.20,
            "structure_score": 0.20,
            "faithfulness_score": 0.20,
            "conciseness_score": 0.15,
        }
        effective = compute_effective_weights(user_weights, profile)
        # structure_score has emphasis 1.3 → boosted
        # conciseness_score has de_emphasis 0.8 → reduced
        assert effective["structure_score"] > user_weights["structure_score"]
        assert effective["conciseness_score"] < user_weights["conciseness_score"]
        # Should still sum to ~1.0 (renormalized)
        assert abs(sum(effective.values()) - 1.0) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_pipeline_adaptation.py -v`
Expected: FAIL

- [ ] **Step 3: Add build_affinity_prompt_section to strategy_selector.py**

```python
# backend/app/services/strategy_selector.py — add new function

def build_affinity_prompt_section(
    task_type: str,
    strategy_affinities: dict | None,
) -> str:
    """Build a prompt section injecting user's framework preferences.

    Returns empty string if no affinities exist for this task type.
    """
    if not strategy_affinities:
        return ""

    affinities = strategy_affinities.get(task_type, {})
    if not affinities:
        return ""

    lines = ["\n## User Framework Preferences (from feedback history)"]
    preferred = affinities.get("preferred", [])
    avoid = affinities.get("avoid", [])

    if preferred:
        lines.append(f"- Preferred frameworks: {', '.join(preferred)}")
    if avoid:
        lines.append(f"- Frameworks to avoid: {', '.join(avoid)}")
    lines.append(
        "Weight these preferences when selecting a framework, but override "
        "if the prompt characteristics strongly favor a different choice."
    )

    return "\n".join(lines)
```

- [ ] **Step 4: Add build_adaptation_hints to optimizer.py**

```python
# backend/app/services/optimizer.py — add new function

def build_adaptation_hints(
    framework_profile: dict | None,
    user_weights: dict[str, float] | None,
    issue_guardrails: list[str],
) -> str:
    """Build adaptation hints section for the optimizer prompt.

    Combines framework profile priorities, user dimension weights,
    and active issue guardrails into a single prompt section.
    """
    sections = []

    if framework_profile and framework_profile.get("emphasis"):
        emphasis = framework_profile["emphasis"]
        priorities = [
            dim.replace("_score", "").title()
            for dim, mult in sorted(emphasis.items(), key=lambda x: -x[1])
        ]
        if priorities:
            sections.append(
                f"This framework excels at: {', '.join(priorities)}. "
                "Prioritize these qualities where trade-offs exist."
            )

    if user_weights:
        default_w = 1.0 / len(user_weights)
        high_priority = [
            (dim.replace("_score", "").title(), w)
            for dim, w in sorted(user_weights.items(), key=lambda x: -x[1])
            if w > default_w + 0.03
        ]
        if high_priority:
            labels = [f"{name} ({w:.0%})" for name, w in high_priority[:3]]
            sections.append(
                f"User priorities (higher = more important): {', '.join(labels)}. "
                "Bias your rewrite toward these dimensions."
            )

    if issue_guardrails:
        sections.append("\n## Quality Guardrails (from user feedback history)")
        for guardrail in issue_guardrails[:4]:
            sections.append(f"- {guardrail}")

    return "\n\n".join(sections) if sections else ""
```

- [ ] **Step 5: Add compute_effective_weights to validator.py**

```python
# backend/app/services/validator.py — add new function

def compute_effective_weights(
    user_weights: dict[str, float] | None,
    framework_profile: dict | None,
) -> dict[str, float]:
    """Combine user dimension weights with framework profile multipliers.

    effective_weight[dim] = user_weight[dim] × emphasis_multiplier[dim]
    Then renormalize to sum to 1.0.

    Returns default equal weights if no user weights provided.
    """
    from app.services.framework_profiles import SCORE_DIMENSIONS

    dims = sorted(SCORE_DIMENSIONS)
    default_w = 1.0 / len(dims)

    if not user_weights:
        base = {d: default_w for d in dims}
    else:
        base = {d: user_weights.get(d, default_w) for d in dims}

    if framework_profile:
        emphasis = framework_profile.get("emphasis", {})
        de_emphasis = framework_profile.get("de_emphasis", {})
        for dim in dims:
            multiplier = emphasis.get(dim, de_emphasis.get(dim, 1.0))
            base[dim] *= multiplier

    # Renormalize to sum to 1.0
    total = sum(base.values())
    if total > 0:
        base = {d: w / total for d, w in base.items()}

    return base
```

- [ ] **Step 6: Run integration tests**

Run: `cd backend && pytest tests/test_pipeline_adaptation.py -v`
Expected: ALL PASS

- [ ] **Step 7: Wire adaptation into pipeline.py run_pipeline()**

Key changes to `pipeline.py`:

1. After `load_adaptation()` (line ~232): also load `framework_performance` for current task_type
2. Pass `strategy_affinities` AND `framework_performance` to `run_strategy()`
3. In strategy stage: call `build_affinity_prompt_section()` and append to strategy prompt
4. After strategy completes: extract `primary_framework`, load its profile via `get_profile()`
5. Call `build_issue_guardrails()` with user's `issue_frequency`
6. Pass framework profile + user weights + guardrails to `run_optimize()` via `build_adaptation_hints()`
7. Pass `compute_effective_weights()` result to `run_validate()` instead of raw user_weights
8. After final validation: update `framework_performance` table with scores
9. Emit new SSE events: `adaptation_injected`, `adaptation_impact`, `issue_suggestions`
10. Store `framework` and `active_guardrails` on the Optimization record

- [ ] **Step 8: Run full pipeline tests**

Run: `cd backend && pytest tests/test_pipeline_retry_oracle.py tests/test_pipeline_adaptation.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/pipeline.py backend/app/services/strategy_selector.py backend/app/services/optimizer.py backend/app/services/validator.py backend/tests/test_pipeline_adaptation.py
git commit -m "feat: wire adaptation state into strategy, optimizer, and validator stages"
```

---

## Chunk 4: API Layer — Feedback Service, Router, MCP, Result Intelligence, Observability

### Task 9: Feedback Service Hardening

**Files:**
- Modify: `backend/app/services/feedback_service.py:1-170`
- Test: `backend/tests/test_feedback_service.py` (extend)

- [ ] **Step 1: Add service-layer validation and logging**

Add `validate_dimension_overrides()` and `validate_corrected_issues()` functions. Add structured logging to `upsert_feedback()`, `get_feedback_for_optimization()`, and `get_feedback_aggregate()`. Call validation at the top of `upsert_feedback()`.

- [ ] **Step 2: Write test for validation**

```python
# backend/tests/test_feedback_validation.py
import pytest
from app.services.feedback_service import validate_dimension_overrides, validate_corrected_issues


def test_valid_overrides_pass():
    result = validate_dimension_overrides({"clarity_score": 8})
    assert result == {"clarity_score": 8}


def test_invalid_dimension_key_rejected():
    with pytest.raises(ValueError, match="Invalid dimension"):
        validate_dimension_overrides({"invalid_dim": 5})


def test_out_of_range_value_rejected():
    with pytest.raises(ValueError):
        validate_dimension_overrides({"clarity_score": 11})


def test_valid_issues_pass():
    result = validate_corrected_issues(["lost_key_terms", "too_verbose"])
    assert result == ["lost_key_terms", "too_verbose"]


def test_invalid_issue_rejected():
    with pytest.raises(ValueError, match="Invalid issue"):
        validate_corrected_issues(["nonexistent"])


def test_issues_deduplicated():
    result = validate_corrected_issues(["lost_key_terms", "lost_key_terms"])
    assert result == ["lost_key_terms"]
```

- [ ] **Step 3: Run tests, implement, verify pass, commit**

Run: `cd backend && pytest tests/test_feedback_validation.py -v`

```bash
git commit -m "feat: add service-layer validation and structured logging to feedback_service"
```

---

### Task 10: Feedback Router — New Endpoints + Corrected Issues

**Files:**
- Modify: `backend/app/routers/feedback.py:1-122`
- Create: `backend/app/routers/framework.py`
- Modify: `backend/app/main.py` (register new router)

- [ ] **Step 1: Add corrected_issues to submit_feedback endpoint**

Update the existing `submit_feedback()` to pass `corrected_issues` from `FeedbackCreate` to `upsert_feedback()`. Replace `BackgroundTasks.add_task(recompute_adaptation_safe)` with `schedule_adaptation_recompute(user_id)`.

- [ ] **Step 2: Add GET /api/feedback/pulse endpoint**

```python
@router.get("/feedback/pulse", response_model=AdaptationPulse)
async def feedback_pulse(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """L0 observability — always-visible adaptation status."""
    from app.services.adaptation_engine import load_adaptation
    adaptation = await load_adaptation(current_user.id, db)
    return compute_adaptation_pulse(adaptation)
```

- [ ] **Step 3: Add GET /api/feedback/summary endpoint**

```python
@router.get("/feedback/summary", response_model=AdaptationSummary)
async def feedback_summary(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """L2 observability — human-readable adaptation dashboard."""
    # Load adaptation + framework performance + recent issues
    # Return AdaptationSummary with priorities, guardrails, framework prefs
```

- [ ] **Step 4: Fix /api/feedback/stats to use SQL aggregation**

Replace the `limit=1000` Python re-aggregation with a `COUNT/GROUP BY` query.

- [ ] **Step 5: Return FeedbackConfirmation from submit endpoint**

Update return type to include effects and stage note.

- [ ] **Step 6: Create framework router**

```python
# backend/app/routers/framework.py
"""Framework performance and profiles REST endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api", tags=["framework"])

@router.get("/framework-profiles")
async def get_framework_profiles():
    """Static framework validation profiles."""
    from app.services.framework_profiles import FRAMEWORK_PROFILES
    return FRAMEWORK_PROFILES

@router.get("/framework-performance/{task_type}")
async def get_framework_performance(
    task_type: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """User's framework performance data for a task type."""
    # Query framework_performance table filtered by user_id + task_type
```

- [ ] **Step 7: Register framework router in main.py**

- [ ] **Step 8: Run all feedback tests**

Run: `cd backend && pytest tests/ -k feedback -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git commit -m "feat: add pulse/summary endpoints, corrected_issues flow, framework router"
```

---

### Task 11: MCP Tool Updates

**Files:**
- Modify: `backend/app/mcp_server.py:1175-1306`

- [ ] **Step 1: Update synthesis_submit_feedback to accept corrected_issues**

- [ ] **Step 2: Use schedule_adaptation_recompute instead of asyncio.create_task**

- [ ] **Step 3: Add synthesis_get_framework_performance tool**

- [ ] **Step 4: Add synthesis_get_adaptation_summary tool**

- [ ] **Step 5: Run MCP integration tests**

Run: `cd backend && pytest tests/integration/test_mcp_api.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: MCP tools — corrected_issues, framework performance, adaptation summary"
```

---

### Task 12: Result Intelligence Service

**Files:**
- Create: `backend/app/services/result_intelligence.py`
- Test: `backend/tests/test_result_intelligence.py`

- [ ] **Step 1: Write tests for verdict computation**

```python
# backend/tests/test_result_intelligence.py
from app.services.result_intelligence import compute_verdict, Verdict, Confidence

def test_high_score_high_confidence_strong_verdict():
    verdict, confidence, headline = compute_verdict(
        overall_score=8.5, threshold=6.0, framework_avg=7.0,
        user_weights={"clarity_score": 0.3}, scores={"clarity_score": 9.0},
        gate_triggered="THRESHOLD_MET",
    )
    assert verdict == Verdict.STRONG
    assert confidence == Confidence.HIGH

def test_below_threshold_weak_verdict():
    verdict, confidence, headline = compute_verdict(
        overall_score=4.0, threshold=6.0, framework_avg=7.0,
        user_weights=None, scores={"clarity_score": 4.0},
        gate_triggered="BUDGET_EXHAUSTED",
    )
    assert verdict == Verdict.WEAK
    assert confidence == Confidence.LOW
```

- [ ] **Step 2: Implement result_intelligence.py with all 8 computations**

Full implementation of: `compute_verdict`, `compute_dimension_insights`, `detect_trade_offs`, `compute_retry_journey`, `compute_framework_fit`, `compute_improvement_potential`, `compute_next_actions`, `compute_result_assessment` (the orchestrator).

- [ ] **Step 3: Wire into pipeline.py — emit result_assessment SSE event**

- [ ] **Step 4: Run tests, commit**

```bash
git commit -m "feat: result intelligence service — verdict, insights, trade-offs, actions"
```

---

### Task 13: Session Compaction Activation

**Files:**
- Modify: `backend/app/services/session_context.py:1-109`
- Modify: `backend/app/services/refinement_service.py:156-268`

- [ ] **Step 1: Fix session_context.py — reset turn_count, compression guard, provider-aware budget**

- [ ] **Step 2: Wire compaction into refinement_service.py refine()**

- [ ] **Step 3: Test compaction triggers correctly**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: wire session compaction into refinement service"
```

---

## Chunk 5: Frontend Phase 2 — Stores, Components, Assessment Engine

### Task 14: Feedback Store Refactor

**Files:**
- Modify: `frontend/src/lib/stores/feedback.svelte.ts:1-120`
- Modify: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Add new API functions to client.ts**

```typescript
// frontend/src/lib/api/client.ts — add new functions
export async function getAdaptationPulse(): Promise<AdaptationPulse> { ... }
export async function getAdaptationSummary(): Promise<AdaptationSummary> { ... }
export async function getFrameworkProfiles(): Promise<Record<string, any>> { ... }
export async function getFrameworkPerformance(taskType: string): Promise<any[]> { ... }
```

- [ ] **Step 2: Refactor feedback store with error handling and new state**

Replace silent `catch {}` with structured error handling. Add `adaptationPulse`, `adaptationSummary`, `error`, `retried` state. Add `loadAdaptationPulse()`, `loadAdaptationSummary()` methods. Return `FeedbackConfirmation` from `submit()`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: refactor feedback store — error handling, adaptation loading, pulse state"
```

---

### Task 15: Tier 1 — Inline Feedback Strip

**Files:**
- Modify: `frontend/src/lib/components/editor/FeedbackInline.svelte:1-159`

- [ ] **Step 1: Redesign FeedbackInline with adaptation pulse, impact delta, Details button**

- Thumbs up → auto-submit + toast
- Thumbs down → expand Tier 2
- Adaptation pulse dot with label
- Impact delta flash (from SSE event)
- Details button → opens Tier 3 in Inspector
- Proper ARIA: `role="radio"`, `aria-pressed`

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: redesign FeedbackInline — pulse, impact delta, tier expansion"
```

---

### Task 16: Tier 2 — Feedback Panel

**Files:**
- Create: `frontend/src/lib/components/editor/FeedbackTier2.svelte`

- [ ] **Step 1: Build FeedbackTier2 component**

- 3-button rating bar synced with Tier 1
- Issue checkboxes in 2-column grid (Fidelity/Quality)
- Proactive suggestions pre-highlighted
- 5-column dimension override grid with +/- controls
- Comment textarea
- SAVE FEEDBACK button → toast with FeedbackConfirmation
- Spring entrance (300ms), accelerating exit (200ms)
- Full keyboard navigation + ARIA

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: add FeedbackTier2 component — issues, overrides, explicit save"
```

---

### Task 17: Tier 3 — Adaptation Intelligence + Result Assessment

**Files:**
- Modify: `frontend/src/lib/components/layout/InspectorAdaptation.svelte:1-149`
- Create: `frontend/src/lib/components/editor/ResultAssessment.svelte`
- Modify: `frontend/src/lib/components/editor/ForgeArtifact.svelte:1-434`

- [ ] **Step 1: Redesign InspectorAdaptation with full L2 dashboard**

- Priority bar chart (5-col grid)
- Active guardrails with trigger counts
- Issue resolution tracking (resolved/monitoring)
- Framework intelligence (4-col grid per row)
- Quality threshold visualization
- L3 expandable technical details
- Load data via `getAdaptationSummary()`

- [ ] **Step 2: Build ResultAssessment component**

- L0 Verdict Bar (always visible): score circle, verdict badge, confidence, headline, sparkline
- L1 Dimension Map (click verdict to expand): dimension rows with priority tags, deltas, elasticity
- L2 Journey + Framework (click dimension): retry bar chart, framework fit, trade-offs
- Actions bar (always visible): guided next steps
- Progressive disclosure via click triggers
- Grid layout per spec: `flex` for verdict, `repeat(5, 1fr)` for dimensions, `1fr 1fr` for L2, `3fr 2fr` for actions

- [ ] **Step 3: Integrate into ForgeArtifact**

- Load `adaptationPulse` after optimization completes
- Render `ResultAssessment` between score display and Tier 1
- Pass `resultAssessment` data from pipeline SSE events
- Load `adaptationSummary` for Tier 3 when inspector opens

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: Tier 3 adaptation intelligence, result assessment engine, ForgeArtifact integration"
```

---

### Task 18: Toast Integration & Final Wiring

**Files:**
- Modify: `frontend/src/lib/components/editor/ForgeArtifact.svelte`
- Modify: `frontend/src/lib/stores/feedback.svelte.ts`

- [ ] **Step 1: Wire toast notifications to feedback submission**

- Quick positive toast (3s, cyan) on thumbs-up
- Detailed toast (5s, purple) on Tier 2 save with effects
- Error toast (persistent, red) with auto-retry + retry button

- [ ] **Step 2: Wire adaptation_impact SSE event to impact card**

- [ ] **Step 3: Full integration test — submit feedback → see toast → see adaptation pulse update**

- [ ] **Step 4: Final commit**

```bash
git commit -m "feat: toast integration, SSE event wiring, feedback loop complete"
```

---

## Chunk 6: Coverage Gap Fixes — Missing Functions From Spec

These tasks fill gaps identified during plan review — functions defined in the spec but missing from the plan above.

### Task 19: Proactive Issue Suggestion Engine

**Files:**
- Create: `backend/app/services/issue_suggestions.py`
- Test: `backend/tests/test_issue_suggestions.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_issue_suggestions.py
"""Tests for proactive issue suggestion engine."""
import pytest
from app.services.issue_suggestions import suggest_likely_issues, SuggestedIssue


class TestSuggestLikelyIssues:
    def test_low_faithfulness_suggests_meaning_issues(self):
        suggestions = suggest_likely_issues(
            scores={"faithfulness_score": 4.0, "clarity_score": 8.0,
                    "specificity_score": 7.0, "structure_score": 7.0, "conciseness_score": 7.0},
            framework="chain-of-thought",
            framework_issue_freq=None,
            user_issue_freq=None,
        )
        issue_ids = [s.issue_id for s in suggestions]
        assert "changed_meaning" in issue_ids or "hallucinated_content" in issue_ids

    def test_framework_history_suggests_recurring_issues(self):
        suggestions = suggest_likely_issues(
            scores={"faithfulness_score": 8.0, "clarity_score": 8.0,
                    "specificity_score": 8.0, "structure_score": 8.0, "conciseness_score": 8.0},
            framework="chain-of-thought",
            framework_issue_freq={"lost_key_terms": 3},
            user_issue_freq=None,
        )
        issue_ids = [s.issue_id for s in suggestions]
        assert "lost_key_terms" in issue_ids

    def test_max_three_suggestions(self):
        suggestions = suggest_likely_issues(
            scores={"faithfulness_score": 3.0, "clarity_score": 3.0,
                    "specificity_score": 3.0, "structure_score": 3.0, "conciseness_score": 3.0},
            framework="chain-of-thought",
            framework_issue_freq={"lost_key_terms": 5, "too_verbose": 4},
            user_issue_freq={"changed_meaning": 6},
        )
        assert len(suggestions) <= 3

    def test_no_suggestions_when_all_scores_high(self):
        suggestions = suggest_likely_issues(
            scores={"faithfulness_score": 9.0, "clarity_score": 9.0,
                    "specificity_score": 9.0, "structure_score": 9.0, "conciseness_score": 9.0},
            framework="chain-of-thought",
            framework_issue_freq=None,
            user_issue_freq=None,
        )
        assert len(suggestions) == 0
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && pytest tests/test_issue_suggestions.py -v`

- [ ] **Step 3: Implement**

```python
# backend/app/services/issue_suggestions.py
"""Proactive issue suggestion engine — suggests likely issues based on scores + history."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.framework_profiles import SCORE_ISSUE_MAP


@dataclass
class SuggestedIssue:
    issue_id: str
    reason: str
    confidence: float  # 0.0 to 1.0


def suggest_likely_issues(
    scores: dict[str, float],
    framework: str,
    framework_issue_freq: dict[str, int] | None,
    user_issue_freq: dict[str, int] | None,
) -> list[SuggestedIssue]:
    """Analyze scores and history to suggest likely issues.

    Three signal sources:
    1. Low dimension scores → mapped issues
    2. Framework-specific issue history
    3. User-global issue patterns

    Returns top 3 deduplicated by highest confidence.
    """
    suggestions: list[SuggestedIssue] = []

    # Signal 1: Low scores suggest specific issues
    for dim, issues in SCORE_ISSUE_MAP.items():
        score = scores.get(dim, 10.0)
        if score < 6.0:
            for issue_id in issues:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"scored {score:.1f}/10 on {dim.replace('_score', '')}",
                    confidence=min(0.9, (6.0 - score) / 4.0),
                ))

    # Signal 2: Framework history
    if framework_issue_freq:
        for issue_id, count in framework_issue_freq.items():
            if count >= 2:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"reported {count}x previously with {framework}",
                    confidence=min(0.85, count * 0.2),
                ))

    # Signal 3: User-global patterns
    if user_issue_freq:
        for issue_id, count in user_issue_freq.items():
            if count >= 3:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"reported {count}x across optimizations",
                    confidence=min(0.8, count * 0.15),
                ))

    # Deduplicate: keep highest confidence per issue_id
    best: dict[str, SuggestedIssue] = {}
    for s in suggestions:
        if s.issue_id not in best or s.confidence > best[s.issue_id].confidence:
            best[s.issue_id] = s

    # Return top 3 by confidence
    return sorted(best.values(), key=lambda s: -s.confidence)[:3]
```

- [ ] **Step 4: Run tests, verify pass, commit**

```bash
git commit -m "feat: proactive issue suggestion engine"
```

---

### Task 20: Issue Guardrails & Verification Prompts

**Files:**
- Create: `backend/app/services/issue_guardrails.py`
- Test: `backend/tests/test_issue_guardrails.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_issue_guardrails.py
"""Tests for issue guardrail and verification prompt builders."""
import pytest
from app.services.issue_guardrails import build_issue_guardrails, build_issue_verification_prompt


class TestBuildIssueGuardrails:
    def test_no_guardrails_below_threshold(self):
        result = build_issue_guardrails({"lost_key_terms": 1}, None)
        assert result == ""

    def test_guardrails_at_threshold(self):
        result = build_issue_guardrails({"lost_key_terms": 2}, None)
        assert "PRESERVE" in result
        assert "terminology" in result.lower()

    def test_max_four_guardrails(self):
        freq = {k: 5 for k in [
            "lost_key_terms", "changed_meaning", "hallucinated_content",
            "too_verbose", "too_vague", "broken_structure",
        ]}
        result = build_issue_guardrails(freq, None)
        assert result.count("- ") <= 4

    def test_merges_user_and_framework_freq(self):
        result = build_issue_guardrails(
            {"lost_key_terms": 1},
            {"lost_key_terms": 1},  # combined = 2 → threshold
        )
        assert "PRESERVE" in result


class TestBuildIssueVerificationPrompt:
    def test_no_verification_below_threshold(self):
        result = build_issue_verification_prompt({"lost_key_terms": 1})
        assert result is None

    def test_term_check_for_lost_key_terms(self):
        result = build_issue_verification_prompt({"lost_key_terms": 2})
        assert result is not None
        assert "TERM CHECK" in result

    def test_intent_check_for_changed_meaning(self):
        result = build_issue_verification_prompt({"changed_meaning": 3})
        assert "INTENT CHECK" in result

    def test_addition_check_for_hallucinated(self):
        result = build_issue_verification_prompt({"hallucinated_content": 2})
        assert "ADDITION CHECK" in result
```

- [ ] **Step 2: Implement**

```python
# backend/app/services/issue_guardrails.py
"""Issue guardrail and verification prompt builders.

Guardrails are injected into the optimizer prompt.
Verification prompts are injected into the validator prompt.
"""
from __future__ import annotations

from app.config import settings
from app.services.framework_profiles import ISSUE_GUARDRAILS


def _merge_issue_counts(
    user_freq: dict[str, int],
    framework_freq: dict[str, int] | None,
) -> dict[str, int]:
    """Merge user-global and framework-specific issue frequencies."""
    merged = dict(user_freq)
    if framework_freq:
        for issue_id, count in framework_freq.items():
            merged[issue_id] = merged.get(issue_id, 0) + count
    return merged


def build_issue_guardrails(
    issue_frequency: dict[str, int],
    framework_issue_freq: dict[str, int] | None,
) -> str:
    """Generate optimizer prompt guardrails from issue history.

    Only fires for issues reported >= MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL times.
    Returns empty string if no guardrails needed.
    """
    merged = _merge_issue_counts(issue_frequency, framework_issue_freq)
    threshold = settings.MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL

    guardrails = []
    for issue_id, count in sorted(merged.items(), key=lambda x: -x[1]):
        if count >= threshold and issue_id in ISSUE_GUARDRAILS:
            guardrails.append(ISSUE_GUARDRAILS[issue_id])

    if not guardrails:
        return ""

    capped = guardrails[: settings.MAX_ISSUE_GUARDRAILS]
    return (
        "\n\n## Quality Guardrails (from user feedback history)\n"
        + "\n".join(f"- {g}" for g in capped)
    )


def build_issue_verification_prompt(
    issue_frequency: dict[str, int],
) -> str | None:
    """Build targeted verification checks for the validator prompt.

    Returns None if no issues warrant extra checking.
    """
    threshold = settings.MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL
    checks = []

    if issue_frequency.get("lost_key_terms", 0) >= threshold:
        checks.append(
            "TERM CHECK: Extract key technical terms from the original. "
            "Verify each appears in the optimized version (or a precise synonym)."
        )
    if issue_frequency.get("changed_meaning", 0) >= threshold:
        checks.append(
            "INTENT CHECK: Summarize what the original prompt asks an LLM to do. "
            "Verify the optimized version asks for the same thing."
        )
    if issue_frequency.get("hallucinated_content", 0) >= threshold:
        checks.append(
            "ADDITION CHECK: Identify any requirements, constraints, or examples "
            "in the optimized version not present in the original. Flag them."
        )
    if issue_frequency.get("too_verbose", 0) >= threshold:
        checks.append(
            "CONCISENESS CHECK: Count sentences that add no new information. "
            "Flag redundancy."
        )

    if not checks:
        return None

    return (
        "\n\n## Issue Verification (user-reported patterns)\n"
        + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(checks))
        + "\nDeduct from faithfulness_score for each failed check."
    )
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git commit -m "feat: issue guardrails for optimizer, verification prompts for validator"
```

---

### Task 21: Adaptation Pulse & Event Purge

**Files:**
- Add to: `backend/app/services/adaptation_engine.py`
- Test: `backend/tests/test_adaptation_pulse.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_adaptation_pulse.py
"""Tests for adaptation pulse (L0 observability) and event purge."""
import pytest
from app.services.adaptation_engine import compute_adaptation_pulse


def test_pulse_inactive_no_adaptation():
    pulse = compute_adaptation_pulse(None)
    assert pulse["status"] == "inactive"
    assert "No feedback yet" in pulse["label"]

def test_pulse_learning_low_count():
    adaptation = type("A", (), {"feedback_count": 2, "dimension_weights": '{"clarity_score": 0.2}'})()
    pulse = compute_adaptation_pulse(adaptation)
    assert pulse["status"] == "learning"
    assert "2" in pulse["label"]

def test_pulse_active_sufficient_count():
    adaptation = type("A", (), {
        "feedback_count": 8,
        "dimension_weights": '{"clarity_score": 0.28, "specificity_score": 0.22, '
                             '"structure_score": 0.18, "faithfulness_score": 0.18, '
                             '"conciseness_score": 0.14}',
    })()
    pulse = compute_adaptation_pulse(adaptation)
    assert pulse["status"] == "active"
    assert "8" in pulse["label"]
```

- [ ] **Step 2: Implement compute_adaptation_pulse and _purge_old_events**

```python
# backend/app/services/adaptation_engine.py — add functions

import json
from datetime import datetime, timedelta, timezone


def compute_adaptation_pulse(adaptation) -> dict:
    """Compute L0 adaptation status pulse for frontend display."""
    if not adaptation:
        return {
            "status": "inactive",
            "label": "No feedback yet",
            "detail": "Rate an optimization to start personalizing",
        }

    n = adaptation.feedback_count
    if n < 3:
        return {
            "status": "learning",
            "label": f"Learning ({n}/3 feedbacks)",
            "detail": "Early adaptation active with conservative adjustments",
        }

    # Find top priority dimension
    try:
        weights = json.loads(adaptation.dimension_weights) if adaptation.dimension_weights else {}
    except (json.JSONDecodeError, TypeError):
        weights = {}

    if weights:
        default = 1.0 / len(weights)
        divergence = sum(abs(w - default) for w in weights.values()) / len(weights)

        if divergence < 0.02:
            detail = "Using default balance — your feedback is consistent with defaults"
        else:
            top_dim = max(weights, key=weights.get)
            top_name = top_dim.replace("_score", "").title()
            detail = f"Prioritizing {top_name}"
    else:
        detail = "Adaptation active"

    return {
        "status": "active",
        "label": f"Adapted ({n} feedbacks)",
        "detail": detail,
    }


async def _purge_old_events(user_id: str, db) -> int:
    """Delete adaptation events older than ADAPTATION_EVENT_RETENTION_DAYS.

    Called during recompute_adaptation() to piggyback on existing writes.
    Returns number of deleted rows.
    """
    from app.config import settings
    from app.models.adaptation_event import AdaptationEvent
    from sqlalchemy import delete

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ADAPTATION_EVENT_RETENTION_DAYS)
    result = await db.execute(
        delete(AdaptationEvent).where(
            AdaptationEvent.user_id == user_id,
            AdaptationEvent.created_at < cutoff,
        )
    )
    return result.rowcount


async def _record_adaptation_event(
    user_id: str, db, event_type: str, payload: dict
) -> None:
    """Record an adaptation event for audit trail (L3 diagnostics)."""
    from app.models.adaptation_event import AdaptationEvent

    event = AdaptationEvent(
        user_id=user_id,
        event_type=event_type,
        payload=json.dumps(payload),
    )
    db.add(event)
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git commit -m "feat: adaptation pulse (L0), event recording and 90-day purge"
```

---

### Task 22: Result Assessment Schemas & First-Time Fallbacks

**Files:**
- Add to: `backend/app/schemas/feedback.py`
- Create: `backend/app/schemas/result_assessment.py`
- Test: `backend/tests/test_result_assessment_schemas.py`

- [ ] **Step 1: Create result assessment schemas with fallback constructors**

```python
# backend/app/schemas/result_assessment.py
"""Schemas for the Result Assessment Engine — verdict, insights, actions."""
from __future__ import annotations

from enum import Enum
from datetime import datetime

from pydantic import BaseModel


class Verdict(str, Enum):
    STRONG = "strong"
    SOLID = "solid"
    MIXED = "mixed"
    WEAK = "weak"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DimensionInsight(BaseModel):
    name: str
    score: float
    user_weight: float
    priority_label: str  # "TOP PRIORITY" / "HIGH" / "BALANCED" / "LOW PRIORITY"
    context: str  # "Top 10% of your optimizations" / "First optimization"
    delta_from_previous: float | None = None
    framework_benchmark: float | None = None
    elasticity: float | None = None
    status: str  # "strong" / "adequate" / "weak"


class TradeOff(BaseModel):
    gained: str  # "Structure +2.1"
    lost: str  # "Conciseness -0.8"
    net_impact: str
    framework_typical: bool = False


class RetryJourney(BaseModel):
    total_attempts: int
    accepted_at: str
    best_attempt: int
    journey_type: str  # "first_try" / "improved" / "struggled" / "plateau"
    narrative: str
    attempt_scores: list[float] = []


class FrameworkFitReport(BaseModel):
    framework: str
    task_type: str
    fit_score: str  # "excellent" / "good" / "fair" / "poor" / "unknown"
    historical_avg: float | None = None
    current_delta: str | None = None
    sample_count: int = 0
    recommendation: str | None = None


class ImprovementSignal(BaseModel):
    dimension: str
    current_score: float
    elasticity: float
    potential_label: str  # "High potential" / "Moderate" / "Near ceiling"
    explanation: str


class ActionSuggestion(BaseModel):
    action: str  # "thumbs_up" / "refine" / "change_framework" / etc.
    label: str
    reasoning: str
    priority: int  # 1 = primary, 2 = secondary


class PercentileContext(BaseModel):
    rank: int
    total: int
    percentile: float
    label: str


class TrendAnalysis(BaseModel):
    direction: str  # "improving" / "stable" / "declining" / "insufficient_data"
    window: int = 0
    avg_recent: float = 0.0
    avg_older: float = 0.0
    label: str = ""


class AdaptationImpactReport(BaseModel):
    """Post-optimization comparison — shows what changed since last feedback."""
    improvements: list[dict] = []   # [{dim, prev, curr}]
    regressions: list[dict] = []    # [{dim, prev, curr}]
    resolved_issues: list[str] = []
    active_guardrails: list[str] = []
    has_meaningful_change: bool = False


class ResultAssessment(BaseModel):
    """Complete result assessment — drives the progressive disclosure UI."""
    verdict: Verdict
    confidence: Confidence
    headline: str
    dimensions: list[DimensionInsight] = []
    trade_offs: list[TradeOff] = []
    journey: RetryJourney | None = None
    framework_fit: FrameworkFitReport | None = None
    improvement_potential: list[ImprovementSignal] = []
    percentile: PercentileContext | None = None
    trend: TrendAnalysis | None = None
    next_actions: list[ActionSuggestion] = []

    @classmethod
    def first_time_fallback(
        cls,
        verdict: Verdict,
        confidence: Confidence,
        headline: str,
        dimensions: list[DimensionInsight],
        journey: RetryJourney | None = None,
    ) -> "ResultAssessment":
        """Construct a ResultAssessment with explicit first-time-user defaults."""
        return cls(
            verdict=verdict,
            confidence=confidence,
            headline=headline,
            dimensions=dimensions,
            trade_offs=[],
            journey=journey,
            framework_fit=FrameworkFitReport(
                framework="unknown", task_type="unknown",
                fit_score="unknown", sample_count=0,
            ),
            improvement_potential=[],
            percentile=PercentileContext(
                rank=1, total=1, percentile=1.0, label="First optimization",
            ),
            trend=TrendAnalysis(
                direction="insufficient_data", window=0,
                label="Need 3+ optimizations for trend",
            ),
            next_actions=[],
        )
```

- [ ] **Step 2: Write tests for fallback construction**

```python
# backend/tests/test_result_assessment_schemas.py
from app.schemas.result_assessment import ResultAssessment, Verdict, Confidence

def test_first_time_fallback_has_all_fields():
    ra = ResultAssessment.first_time_fallback(
        verdict=Verdict.SOLID,
        confidence=Confidence.MEDIUM,
        headline="First result",
        dimensions=[],
    )
    assert ra.percentile.label == "First optimization"
    assert ra.trend.direction == "insufficient_data"
    assert ra.framework_fit.fit_score == "unknown"
    assert ra.trade_offs == []
    assert ra.improvement_potential == []
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git commit -m "feat: result assessment schemas with first-time user fallbacks"
```

---

### Task 23: Structured Logging Across All Services

**Files:**
- Modify: `backend/app/services/feedback_service.py`
- Modify: `backend/app/services/prompt_diff.py`
- Modify: `backend/app/services/session_context.py`
- Modify: `backend/app/services/retry_oracle.py`
- Modify: `backend/app/services/pipeline.py`

- [ ] **Step 1: Add structured logging to each service**

Add `logger = logging.getLogger(__name__)` and parameterized log calls per the spec's logging table:

| Service | Event | Level | When |
|---------|-------|-------|------|
| feedback_service | `feedback_submitted` | INFO | After upsert |
| feedback_service | `feedback_loaded` | DEBUG | After get |
| prompt_diff | `cycle_detected` | INFO | When cycle found |
| session_context | `session_compacted` | INFO | After compaction |
| session_context | `compaction_rejected` | WARN | When output too short |
| retry_oracle | `retry_gate_decision` | INFO | Each should_retry call |
| retry_oracle | `elasticity_updated` | DEBUG | Each record_attempt |
| retry_oracle | `focus_selected` | INFO | After focus selection |
| pipeline | `adaptation_injected` | INFO | After load_adaptation |
| pipeline | `framework_performance_updated` | INFO | After post-validation update |

All use `logger.info("event_name", extra={...})` pattern.

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd backend && pytest -x -v`

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: structured parameterized logging across all feedback loop services"
```

---

### Task 24: (merged into Task 5 — compound soft cycle detection is now built in from the start)

---

### Task 25: Framework Performance Composite Score

**Files:**
- Create: `backend/app/services/framework_scoring.py`
- Test: `backend/tests/test_framework_scoring.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_framework_scoring.py
"""Tests for framework composite scoring in strategy selection."""
import pytest
from datetime import datetime, timezone, timedelta
from app.services.framework_scoring import compute_framework_composite_score


def test_recent_high_rated_framework_scores_high():
    score = compute_framework_composite_score(
        avg_overall=7.5,
        user_rating_avg=0.8,
        last_updated=datetime.now(timezone.utc),
        user_weights=None,
        avg_scores=None,
    )
    assert score > 7.0

def test_old_framework_decays():
    recent = compute_framework_composite_score(
        avg_overall=7.5, user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None, avg_scores=None,
    )
    old = compute_framework_composite_score(
        avg_overall=7.5, user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc) - timedelta(days=90),
        user_weights=None, avg_scores=None,
    )
    assert recent > old

def test_negative_rating_penalizes():
    positive = compute_framework_composite_score(
        avg_overall=7.0, user_rating_avg=0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None, avg_scores=None,
    )
    negative = compute_framework_composite_score(
        avg_overall=7.0, user_rating_avg=-0.5,
        last_updated=datetime.now(timezone.utc),
        user_weights=None, avg_scores=None,
    )
    assert positive > negative
```

- [ ] **Step 2: Implement**

```python
# backend/app/services/framework_scoring.py
"""Framework composite scoring for strategy selection."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from math import exp

from app.config import settings


def compute_framework_composite_score(
    avg_overall: float,
    user_rating_avg: float,
    last_updated: datetime,
    user_weights: dict[str, float] | None,
    avg_scores: str | dict | None,
) -> float:
    """Compute composite score for framework ranking in strategy selection.

    composite = weighted_avg × satisfaction_factor × recency_decay

    - satisfaction_factor: [-1,1] → [0.7, 1.3]
    - recency_decay: exp(-0.01 × days) → 0 days=1.0, 30 days=0.74, 90 days=0.41
    """
    # Base score: weighted average if user_weights provided, else raw average
    if user_weights and avg_scores:
        scores = json.loads(avg_scores) if isinstance(avg_scores, str) else avg_scores
        total_w = sum(user_weights.get(d, 0.2) for d in scores)
        if total_w > 0:
            base = sum(
                scores[d] * user_weights.get(d, 0.2) for d in scores
            ) / total_w
        else:
            base = avg_overall
    else:
        base = avg_overall

    # Satisfaction factor: maps [-1, 1] → [0.7, 1.3]
    satisfaction = 1.0 + 0.3 * user_rating_avg

    # Recency decay
    if last_updated:
        now = datetime.now(timezone.utc)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        days = (now - last_updated).total_seconds() / 86400
        recency = exp(-settings.FRAMEWORK_PERF_RECENCY_DECAY * days)
    else:
        recency = 0.5  # unknown recency → moderate penalty

    return base * satisfaction * recency
```

- [ ] **Step 3: Run tests, verify pass, commit**

```bash
git commit -m "feat: framework composite scoring with satisfaction × recency decay"
```
