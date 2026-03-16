# Phase 1b: Supporting Services + Full Coverage — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all supporting services (context resolver, trace logger, optimization/feedback CRUD, adaptation tracker, heuristic scorer), rate limiting, remaining routers, and pipeline integration — producing a fully curl-testable API with ≥90% coverage.

**Architecture:** Services layer between routers and models. Context resolver assembles per-source capped context. Adaptation tracker maintains simple strategy affinity counters. Heuristic scorer applies bias correction for MCP passthrough. Trace logger writes JSONL to `data/traces/`. Rate limiting uses in-memory token bucket via `limits` library.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy async, textstat (readability), limits (rate limiting)

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md` (Sections 3, 4, 5, 6, 10, 11)

**Phase 1a Handoff:** `docs/superpowers/plans/handoffs/handoff-phase-1a.json` (all_passed: true, 68 tests, 93% coverage)

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/services/context_resolver.py` | Per-source char caps, priority truncation, untrusted-context wrapping |
| `backend/app/services/trace_logger.py` | JSONL trace writing to `data/traces/`, reading by trace_id |
| `backend/app/services/optimization_service.py` | Optimization CRUD, sort/filter, score distribution stats |
| `backend/app/services/adaptation_tracker.py` | Strategy affinity tracking, seed data, degenerate detection |
| `backend/app/services/feedback_service.py` | Feedback CRUD, aggregation, sync adaptation update |
| `backend/app/services/heuristic_scorer.py` | Bias correction, structural/readability heuristics |
| `backend/app/dependencies/rate_limit.py` | In-memory rate limiting FastAPI dependency |
| `backend/app/routers/history.py` | `GET /api/history` with sort/filter |
| `backend/app/routers/feedback.py` | `POST /api/feedback`, `GET /api/feedback` |
| `backend/app/routers/providers.py` | `GET /api/providers` |
| `backend/app/routers/settings.py` | `GET /api/settings` (read-only) |
| `backend/app/routers/github_auth.py` | 501 stubs |
| `backend/app/routers/github_repos.py` | 501 stubs |
| `backend/tests/test_context_resolver.py` | Context assembly, truncation, injection hardening |
| `backend/tests/test_trace_logger.py` | Trace write/read |
| `backend/tests/test_optimization_service.py` | CRUD, sort/filter |
| `backend/tests/test_adaptation_tracker.py` | Affinity updates, degenerate detection |
| `backend/tests/test_feedback_service.py` | Feedback CRUD, aggregation |
| `backend/tests/test_heuristic_scorer.py` | Bias correction, heuristics |
| `backend/tests/test_score_calibration.py` | Score clustering detection |

### Modify

| File | Changes |
|------|---------|
| `backend/app/services/pipeline.py` | Integrate context_resolver, trace_logger, faithfulness warning |
| `backend/app/routers/health.py` | Add score_health and avg_duration_ms metrics |
| `backend/app/routers/optimize.py` | Add rate limiting |
| `backend/app/main.py` | Include all new routers |
| `backend/tests/conftest.py` | Additional fixtures as needed |

---

## Chunk 1: Core Services

### Task 1: Context Resolver

**Files:**
- Create: `backend/app/services/context_resolver.py`
- Create: `backend/tests/test_context_resolver.py`

- [ ] **Step 1: Write context resolver tests**

```python
# backend/tests/test_context_resolver.py
"""Tests for context resolution with per-source caps and truncation."""

import pytest
from app.services.context_resolver import ContextResolver


class TestContextResolver:
    def test_resolve_minimal(self):
        """Raw prompt only — all optional sources None."""
        ctx = ContextResolver.resolve(raw_prompt="Write a function")
        assert ctx.raw_prompt == "Write a function"
        assert ctx.codebase_guidance is None
        assert ctx.codebase_context is None
        assert ctx.adaptation_state is None

    def test_prompt_too_short_rejected(self):
        with pytest.raises(ValueError, match="too short"):
            ContextResolver.resolve(raw_prompt="hi")

    def test_prompt_too_long_rejected(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            ContextResolver.resolve(raw_prompt="x" * 200_001)

    def test_guidance_truncated_at_cap(self):
        long_guidance = "a" * 25_000  # exceeds MAX_GUIDANCE_CHARS (20000)
        ctx = ContextResolver.resolve(raw_prompt="Write a function", codebase_guidance=long_guidance)
        assert len(ctx.codebase_guidance) <= 20_000

    def test_codebase_context_truncated_at_cap(self):
        long_context = "b" * 110_000  # exceeds MAX_CODEBASE_CONTEXT_CHARS (100000)
        ctx = ContextResolver.resolve(raw_prompt="Write a function", codebase_context=long_context)
        assert len(ctx.codebase_context) <= 100_000

    def test_adaptation_truncated_at_cap(self):
        long_adapt = "c" * 6_000  # exceeds MAX_ADAPTATION_CHARS (5000)
        ctx = ContextResolver.resolve(raw_prompt="Write a function", adaptation_state=long_adapt)
        assert len(ctx.adaptation_state) <= 5_000

    def test_untrusted_context_wrapping(self):
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function",
            codebase_guidance="CLAUDE.md content here",
        )
        assert "<untrusted-context" in ctx.codebase_guidance
        assert "</untrusted-context>" in ctx.codebase_guidance

    def test_context_sources_tracking(self):
        ctx = ContextResolver.resolve(
            raw_prompt="test",
            codebase_guidance="guidance",
            adaptation_state="affinities",
        )
        assert ctx.context_sources["codebase_guidance"] is True
        assert ctx.context_sources["codebase_context"] is False
        assert ctx.context_sources["adaptation"] is True

    def test_trace_id_generated(self):
        ctx = ContextResolver.resolve(raw_prompt="test prompt here")
        assert ctx.trace_id  # non-empty string
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_context_resolver.py -v`

- [ ] **Step 3: Implement context resolver**

```python
# backend/app/services/context_resolver.py
"""Unified context assembly with per-source character caps.

Resolves all context sources for an optimization request.
Enforces caps, wraps external content in <untrusted-context>, tracks sources.
"""

import logging
import uuid

from app.config import settings
from app.schemas.pipeline_contracts import ResolvedContext

logger = logging.getLogger(__name__)

MIN_PROMPT_CHARS = 20


class ContextResolver:
    """Assembles and validates pipeline context."""

    @staticmethod
    def resolve(
        raw_prompt: str,
        strategy_override: str | None = None,
        codebase_guidance: str | None = None,
        codebase_context: str | None = None,
        adaptation_state: str | None = None,
    ) -> ResolvedContext:
        # Validate prompt length
        if len(raw_prompt) < MIN_PROMPT_CHARS:
            raise ValueError(
                f"Prompt too short ({len(raw_prompt)} chars). "
                f"Minimum is {MIN_PROMPT_CHARS} characters."
            )
        if len(raw_prompt) > settings.MAX_RAW_PROMPT_CHARS:
            raise ValueError(
                f"Prompt exceeds maximum length ({len(raw_prompt)} chars). "
                f"Maximum is {settings.MAX_RAW_PROMPT_CHARS} characters."
            )

        # Truncate and wrap per-source
        if codebase_guidance:
            codebase_guidance = codebase_guidance[: settings.MAX_GUIDANCE_CHARS]
            codebase_guidance = (
                '<untrusted-context source="codebase-guidance">\n'
                f"{codebase_guidance}\n"
                "</untrusted-context>"
            )

        if codebase_context:
            codebase_context = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]
            codebase_context = (
                '<untrusted-context source="github-explore">\n'
                f"{codebase_context}\n"
                "</untrusted-context>"
            )

        if adaptation_state:
            adaptation_state = adaptation_state[: settings.MAX_ADAPTATION_CHARS]

        return ResolvedContext(
            raw_prompt=raw_prompt,
            strategy_override=strategy_override,
            codebase_guidance=codebase_guidance,
            codebase_context=codebase_context,
            adaptation_state=adaptation_state,
            context_sources={
                "codebase_guidance": codebase_guidance is not None,
                "codebase_context": codebase_context is not None,
                "adaptation": adaptation_state is not None,
            },
            trace_id=str(uuid.uuid4()),
        )
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_context_resolver.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/context_resolver.py tests/test_context_resolver.py
git commit -m "feat: implement context resolver with per-source caps and injection hardening"
```

---

### Task 2: Trace Logger

**Files:**
- Create: `backend/app/services/trace_logger.py`
- Create: `backend/tests/test_trace_logger.py`

- [ ] **Step 1: Write trace logger tests**

```python
# backend/tests/test_trace_logger.py
"""Tests for JSONL trace logging."""

import json
import pytest
from pathlib import Path
from app.services.trace_logger import TraceLogger


@pytest.fixture
def trace_dir(tmp_path):
    d = tmp_path / "traces"
    d.mkdir()
    return d


class TestTraceLogger:
    def test_write_and_read(self, trace_dir):
        tl = TraceLogger(trace_dir)
        tl.log_phase(
            trace_id="test-trace-1",
            phase="analyze",
            duration_ms=2340,
            tokens_in=1200,
            tokens_out=450,
            model="claude-sonnet-4-6",
            provider="cli",
            result={"task_type": "coding"},
        )
        entries = tl.read_trace("test-trace-1")
        assert len(entries) == 1
        assert entries[0]["phase"] == "analyze"
        assert entries[0]["duration_ms"] == 2340

    def test_multiple_phases(self, trace_dir):
        tl = TraceLogger(trace_dir)
        for phase in ["analyze", "optimize", "score"]:
            tl.log_phase(trace_id="t2", phase=phase, duration_ms=100,
                         tokens_in=50, tokens_out=30, model="m", provider="p")
        entries = tl.read_trace("t2")
        assert len(entries) == 3
        assert [e["phase"] for e in entries] == ["analyze", "optimize", "score"]

    def test_read_nonexistent_trace(self, trace_dir):
        tl = TraceLogger(trace_dir)
        entries = tl.read_trace("nonexistent")
        assert entries == []

    def test_jsonl_format(self, trace_dir):
        tl = TraceLogger(trace_dir)
        tl.log_phase(trace_id="t3", phase="analyze", duration_ms=100,
                     tokens_in=50, tokens_out=30, model="m", provider="p")
        # Read raw file and verify it's valid JSONL
        files = list(trace_dir.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        for line in lines:
            json.loads(line)  # should not raise
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement trace logger**

```python
# backend/app/services/trace_logger.py
"""Per-phase JSONL trace logging to data/traces/.

Each optimization gets a trace_id. Phases append entries to a daily JSONL file.
Traces are readable by scanning for matching trace_id.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class TraceLogger:
    """Writes and reads per-phase trace entries in JSONL format."""

    def __init__(self, traces_dir: Path) -> None:
        self.traces_dir = traces_dir
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def _daily_file(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.traces_dir / f"traces-{date_str}.jsonl"

    def log_phase(
        self,
        trace_id: str,
        phase: str,
        duration_ms: int,
        tokens_in: int = 0,
        tokens_out: int = 0,
        model: str = "",
        provider: str = "",
        result: dict | None = None,
    ) -> None:
        """Append a phase entry to the daily trace file."""
        entry = {
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "provider": provider,
        }
        if result:
            entry["result"] = result

        path = self._daily_file()
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug("Trace %s phase=%s logged to %s", trace_id, phase, path.name)

    def read_trace(self, trace_id: str) -> list[dict]:
        """Read all phase entries for a given trace_id across all files."""
        entries = []
        for path in sorted(self.traces_dir.glob("*.jsonl")):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("trace_id") == trace_id:
                        entries.append(entry)
        return entries
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/trace_logger.py tests/test_trace_logger.py
git commit -m "feat: implement JSONL trace logger"
```

---

### Task 3: Optimization Service

**Files:**
- Create: `backend/app/services/optimization_service.py`
- Create: `backend/tests/test_optimization_service.py`

- [ ] **Step 1: Write optimization service tests**

```python
# backend/tests/test_optimization_service.py
"""Tests for optimization CRUD, sort/filter, and score distribution."""

import pytest
from app.models import Optimization
from app.services.optimization_service import OptimizationService

_VALID_SORT_COLUMNS = {"created_at", "overall_score", "task_type", "status", "duration_ms", "strategy_used"}


@pytest.fixture
async def svc(db_session):
    return OptimizationService(db_session)


@pytest.fixture
async def sample_opts(db_session):
    """Insert 3 sample optimizations."""
    opts = []
    for i, (task, score, strategy) in enumerate([
        ("coding", 7.5, "chain-of-thought"),
        ("writing", 6.0, "few-shot"),
        ("coding", 8.5, "structured-output"),
    ]):
        opt = Optimization(
            id=f"opt-{i}", raw_prompt=f"prompt {i}", optimized_prompt=f"better {i}",
            task_type=task, strategy_used=strategy, overall_score=score,
            status="completed", trace_id=f"trace-{i}", provider="mock",
        )
        db_session.add(opt)
        opts.append(opt)
    await db_session.commit()
    return opts


class TestOptimizationService:
    async def test_get_by_id(self, svc, sample_opts):
        opt = await svc.get_by_id("opt-0")
        assert opt is not None
        assert opt.task_type == "coding"

    async def test_get_by_id_not_found(self, svc):
        assert await svc.get_by_id("nonexistent") is None

    async def test_list_all(self, svc, sample_opts):
        result = await svc.list_optimizations()
        assert result["total"] == 3
        assert len(result["items"]) == 3

    async def test_list_with_offset_limit(self, svc, sample_opts):
        result = await svc.list_optimizations(offset=1, limit=1)
        assert result["total"] == 3
        assert len(result["items"]) == 1
        assert result["has_more"] is True

    async def test_list_sort_by_score_desc(self, svc, sample_opts):
        result = await svc.list_optimizations(sort_by="overall_score", sort_order="desc")
        scores = [item.overall_score for item in result["items"]]
        assert scores == sorted(scores, reverse=True)

    async def test_list_filter_by_task_type(self, svc, sample_opts):
        result = await svc.list_optimizations(task_type="coding")
        assert result["total"] == 2
        assert all(item.task_type == "coding" for item in result["items"])

    async def test_list_filter_by_status(self, svc, sample_opts):
        result = await svc.list_optimizations(status="completed")
        assert result["total"] == 3

    async def test_invalid_sort_column_rejected(self, svc, sample_opts):
        with pytest.raises(ValueError, match="Invalid sort column"):
            await svc.list_optimizations(sort_by="raw_prompt")

    async def test_score_distribution(self, svc, sample_opts):
        stats = await svc.get_score_distribution()
        assert "overall_score" in stats
        assert "mean" in stats["overall_score"]
        assert "stddev" in stats["overall_score"]
        assert "count" in stats["overall_score"]
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement optimization service**

```python
# backend/app/services/optimization_service.py
"""Optimization CRUD, sort/filter, and score distribution tracking."""

import logging
import math

from sqlalchemy import func, select, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization

logger = logging.getLogger(__name__)

_VALID_SORT_COLUMNS = {
    "created_at", "overall_score", "task_type", "status", "duration_ms", "strategy_used",
}


class OptimizationService:
    """CRUD and query operations for optimizations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, optimization_id: str) -> Optimization | None:
        result = await self.db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        return result.scalar_one_or_none()

    async def get_by_trace_id(self, trace_id: str) -> Optimization | None:
        result = await self.db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        return result.scalar_one_or_none()

    async def list_optimizations(
        self,
        offset: int = 0,
        limit: int = 50,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        task_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        if sort_by not in _VALID_SORT_COLUMNS:
            raise ValueError(f"Invalid sort column: {sort_by}. Valid: {_VALID_SORT_COLUMNS}")

        query = select(Optimization)
        count_query = select(func.count(Optimization.id))

        if task_type:
            query = query.where(Optimization.task_type == task_type)
            count_query = count_query.where(Optimization.task_type == task_type)
        if status:
            query = query.where(Optimization.status == status)
            count_query = count_query.where(Optimization.status == status)

        col = getattr(Optimization, sort_by)
        query = query.order_by(desc(col) if sort_order == "desc" else asc(col))
        query = query.offset(offset).limit(limit)

        total = (await self.db.execute(count_query)).scalar() or 0
        items = list((await self.db.execute(query)).scalars().all())

        return {
            "total": total,
            "count": len(items),
            "offset": offset,
            "items": items,
            "has_more": offset + len(items) < total,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
        }

    async def get_score_distribution(self) -> dict:
        """Get mean/stddev per dimension for score clustering detection."""
        dimensions = ["overall_score", "score_clarity", "score_specificity",
                       "score_structure", "score_faithfulness", "score_conciseness"]
        stats = {}
        for dim in dimensions:
            col = getattr(Optimization, dim)
            result = await self.db.execute(
                select(
                    func.count(col),
                    func.avg(col),
                    func.sum(col * col),  # for stddev calc
                    func.sum(col),
                ).where(col.isnot(None))
            )
            row = result.one()
            count, avg_val, sum_sq, sum_val = row
            count = count or 0
            avg_val = avg_val or 0.0

            if count > 1:
                variance = (sum_sq / count) - (avg_val ** 2)
                stddev = math.sqrt(max(0, variance))
            else:
                stddev = 0.0

            stats[dim] = {"count": count, "mean": round(avg_val, 2), "stddev": round(stddev, 2)}
        return stats
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/optimization_service.py tests/test_optimization_service.py
git commit -m "feat: implement optimization service with CRUD, sort/filter, and score stats"
```

---

## Chunk 2: Adaptation and Scoring

### Task 4: Adaptation Tracker

**Files:**
- Create: `backend/app/services/adaptation_tracker.py`
- Create: `backend/tests/test_adaptation_tracker.py`

- [ ] **Step 1: Write adaptation tracker tests**

```python
# backend/tests/test_adaptation_tracker.py
"""Tests for strategy affinity tracking."""

import pytest
from app.models import StrategyAffinity
from app.services.adaptation_tracker import AdaptationTracker


@pytest.fixture
async def tracker(db_session):
    return AdaptationTracker(db_session)


class TestAdaptationTracker:
    async def test_update_affinity_thumbs_up(self, tracker, db_session):
        await tracker.update_affinity("coding", "chain-of-thought", "thumbs_up")
        result = await db_session.execute(
            __import__("sqlalchemy").select(StrategyAffinity).where(
                StrategyAffinity.task_type == "coding",
                StrategyAffinity.strategy == "chain-of-thought",
            )
        )
        aff = result.scalar_one()
        assert aff.thumbs_up == 1
        assert aff.thumbs_down == 0
        assert aff.approval_rate == 1.0

    async def test_update_affinity_thumbs_down(self, tracker, db_session):
        await tracker.update_affinity("coding", "few-shot", "thumbs_down")
        result = await db_session.execute(
            __import__("sqlalchemy").select(StrategyAffinity).where(
                StrategyAffinity.task_type == "coding",
                StrategyAffinity.strategy == "few-shot",
            )
        )
        aff = result.scalar_one()
        assert aff.thumbs_up == 0
        assert aff.thumbs_down == 1
        assert aff.approval_rate == 0.0

    async def test_approval_rate_computed(self, tracker):
        await tracker.update_affinity("coding", "cot", "thumbs_up")
        await tracker.update_affinity("coding", "cot", "thumbs_up")
        await tracker.update_affinity("coding", "cot", "thumbs_down")
        state = await tracker.get_affinities("coding")
        assert abs(state["cot"]["approval_rate"] - 0.667) < 0.01

    async def test_get_affinities_empty(self, tracker):
        state = await tracker.get_affinities("unknown_type")
        assert state == {}

    async def test_render_adaptation_state(self, tracker):
        await tracker.update_affinity("coding", "chain-of-thought", "thumbs_up")
        rendered = await tracker.render_adaptation_state("coding")
        assert rendered is not None
        assert "chain-of-thought" in rendered

    async def test_render_returns_none_when_no_data(self, tracker):
        rendered = await tracker.render_adaptation_state("unknown")
        assert rendered is None

    async def test_degenerate_detection(self, tracker):
        """After 10+ same-rating feedbacks, degenerate pattern detected."""
        for _ in range(11):
            await tracker.update_affinity("coding", "cot", "thumbs_up")
        is_degenerate = await tracker.check_degenerate("coding", "cot")
        assert is_degenerate is True

    async def test_not_degenerate_with_mixed_feedback(self, tracker):
        for _ in range(8):
            await tracker.update_affinity("coding", "cot", "thumbs_up")
        for _ in range(3):
            await tracker.update_affinity("coding", "cot", "thumbs_down")
        is_degenerate = await tracker.check_degenerate("coding", "cot")
        assert is_degenerate is False
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement adaptation tracker**

```python
# backend/app/services/adaptation_tracker.py
"""Strategy affinity tracking — simple counter-based adaptation.

Tracks thumbs up/down per strategy per task type. Renders adaptation state
for the optimizer prompt. Detects degenerate patterns (>90% same rating
over 10+ feedbacks).
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StrategyAffinity
from app.services.prompt_loader import PromptLoader
from app.config import PROMPTS_DIR

logger = logging.getLogger(__name__)

DEGENERATE_THRESHOLD = 10  # minimum feedbacks before checking
DEGENERATE_RATE = 0.9  # >90% same rating = degenerate


class AdaptationTracker:
    """Tracks strategy affinities from user feedback."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update_affinity(self, task_type: str, strategy: str, rating: str) -> None:
        """Increment thumbs_up or thumbs_down for a strategy+task_type pair."""
        result = await self.db.execute(
            select(StrategyAffinity).where(
                StrategyAffinity.task_type == task_type,
                StrategyAffinity.strategy == strategy,
            )
        )
        aff = result.scalar_one_or_none()

        if aff is None:
            aff = StrategyAffinity(task_type=task_type, strategy=strategy)
            self.db.add(aff)

        if rating == "thumbs_up":
            aff.thumbs_up += 1
        elif rating == "thumbs_down":
            aff.thumbs_down += 1

        total = aff.thumbs_up + aff.thumbs_down
        aff.approval_rate = round(aff.thumbs_up / total, 3) if total > 0 else 0.0

        await self.db.commit()

    async def get_affinities(self, task_type: str) -> dict:
        """Get all strategy affinities for a task type."""
        result = await self.db.execute(
            select(StrategyAffinity).where(StrategyAffinity.task_type == task_type)
        )
        affinities = {}
        for aff in result.scalars().all():
            affinities[aff.strategy] = {
                "thumbs_up": aff.thumbs_up,
                "thumbs_down": aff.thumbs_down,
                "approval_rate": aff.approval_rate,
            }
        return affinities

    async def render_adaptation_state(self, task_type: str) -> str | None:
        """Render adaptation state for the optimizer prompt, or None if no data."""
        affinities = await self.get_affinities(task_type)
        if not affinities:
            return None
        loader = PromptLoader(PROMPTS_DIR)
        return loader.render("adaptation.md", {
            "task_type_affinities": json.dumps(affinities, indent=2),
        })

    async def check_degenerate(self, task_type: str, strategy: str) -> bool:
        """Check for degenerate pattern: >90% same rating over 10+ feedbacks."""
        result = await self.db.execute(
            select(StrategyAffinity).where(
                StrategyAffinity.task_type == task_type,
                StrategyAffinity.strategy == strategy,
            )
        )
        aff = result.scalar_one_or_none()
        if aff is None:
            return False
        total = aff.thumbs_up + aff.thumbs_down
        if total < DEGENERATE_THRESHOLD:
            return False
        rate = max(aff.approval_rate, 1 - aff.approval_rate)
        if rate >= DEGENERATE_RATE:
            logger.warning(
                "Degenerate pattern: task_type=%s strategy=%s rate=%.2f total=%d",
                task_type, strategy, rate, total,
            )
            return True
        return False
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/adaptation_tracker.py tests/test_adaptation_tracker.py
git commit -m "feat: implement adaptation tracker with affinity counters and degenerate detection"
```

---

### Task 5: Feedback Service

**Files:**
- Create: `backend/app/services/feedback_service.py`
- Create: `backend/tests/test_feedback_service.py`

- [ ] **Step 1: Write feedback service tests**

```python
# backend/tests/test_feedback_service.py
"""Tests for feedback CRUD and adaptation integration."""

import pytest
from app.models import Optimization, Feedback
from app.services.feedback_service import FeedbackService


@pytest.fixture
async def opt_id(db_session):
    opt = Optimization(
        id="fb-opt-1", raw_prompt="test", optimized_prompt="better",
        task_type="coding", strategy_used="chain-of-thought",
        status="completed", trace_id="fb-trace-1", provider="mock",
    )
    db_session.add(opt)
    await db_session.commit()
    return "fb-opt-1"


@pytest.fixture
async def svc(db_session):
    return FeedbackService(db_session)


class TestFeedbackService:
    async def test_create_feedback(self, svc, opt_id):
        fb = await svc.create_feedback(opt_id, "thumbs_up", "Great result!")
        assert fb.optimization_id == opt_id
        assert fb.rating == "thumbs_up"
        assert fb.comment == "Great result!"

    async def test_create_feedback_invalid_optimization(self, svc):
        with pytest.raises(ValueError, match="not found"):
            await svc.create_feedback("nonexistent", "thumbs_up")

    async def test_create_feedback_invalid_rating(self, svc, opt_id):
        with pytest.raises(ValueError, match="Invalid rating"):
            await svc.create_feedback(opt_id, "five_stars")

    async def test_get_feedback_for_optimization(self, svc, opt_id):
        await svc.create_feedback(opt_id, "thumbs_up")
        await svc.create_feedback(opt_id, "thumbs_down", "Could be better")
        feedbacks = await svc.get_for_optimization(opt_id)
        assert len(feedbacks) == 2

    async def test_get_aggregation(self, svc, opt_id):
        await svc.create_feedback(opt_id, "thumbs_up")
        await svc.create_feedback(opt_id, "thumbs_up")
        await svc.create_feedback(opt_id, "thumbs_down")
        agg = await svc.get_aggregation(opt_id)
        assert agg["thumbs_up"] == 2
        assert agg["thumbs_down"] == 1
        assert agg["total"] == 3
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement feedback service**

```python
# backend/app/services/feedback_service.py
"""Feedback CRUD with synchronous adaptation tracker update."""

import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Optimization
from app.services.adaptation_tracker import AdaptationTracker

logger = logging.getLogger(__name__)

_VALID_RATINGS = {"thumbs_up", "thumbs_down"}


class FeedbackService:
    """Feedback CRUD — persists feedback and updates adaptation tracker."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_feedback(
        self, optimization_id: str, rating: str, comment: str | None = None,
    ) -> Feedback:
        if rating not in _VALID_RATINGS:
            raise ValueError(f"Invalid rating: {rating}. Must be one of {_VALID_RATINGS}")

        # Verify optimization exists
        opt = (await self.db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )).scalar_one_or_none()
        if opt is None:
            raise ValueError(f"Optimization not found: {optimization_id}")

        fb = Feedback(optimization_id=optimization_id, rating=rating, comment=comment)
        self.db.add(fb)
        await self.db.commit()
        await self.db.refresh(fb)

        # Synchronous adaptation update (non-fatal)
        if opt.task_type and opt.strategy_used:
            try:
                tracker = AdaptationTracker(self.db)
                await tracker.update_affinity(opt.task_type, opt.strategy_used, rating)
            except Exception:
                logger.warning("Adaptation update failed for feedback %s", fb.id, exc_info=True)

        return fb

    async def get_for_optimization(self, optimization_id: str) -> list[Feedback]:
        result = await self.db.execute(
            select(Feedback)
            .where(Feedback.optimization_id == optimization_id)
            .order_by(Feedback.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_aggregation(self, optimization_id: str) -> dict:
        result = await self.db.execute(
            select(
                func.count(Feedback.id).label("total"),
                func.sum(func.iif(Feedback.rating == "thumbs_up", 1, 0)).label("up"),
                func.sum(func.iif(Feedback.rating == "thumbs_down", 1, 0)).label("down"),
            ).where(Feedback.optimization_id == optimization_id)
        )
        row = result.one()
        return {
            "total": row.total or 0,
            "thumbs_up": row.up or 0,
            "thumbs_down": row.down or 0,
        }
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/feedback_service.py tests/test_feedback_service.py
git commit -m "feat: implement feedback service with CRUD and adaptation integration"
```

---

### Task 6: Heuristic Scorer

**Files:**
- Create: `backend/app/services/heuristic_scorer.py`
- Create: `backend/tests/test_heuristic_scorer.py`

- [ ] **Step 1: Write heuristic scorer tests**

```python
# backend/tests/test_heuristic_scorer.py
"""Tests for passthrough bias correction and heuristic scoring."""

import pytest
from app.services.heuristic_scorer import HeuristicScorer


class TestBiasCorrection:
    def test_applies_default_discount(self):
        scores = {"clarity": 8.0, "specificity": 9.0, "structure": 7.0,
                  "faithfulness": 8.5, "conciseness": 7.5}
        corrected = HeuristicScorer.apply_bias_correction(scores)
        # Default 15% discount: 8.0 * 0.85 = 6.8
        assert corrected["clarity"] == pytest.approx(6.8, abs=0.01)

    def test_custom_factor(self):
        scores = {"clarity": 10.0, "specificity": 10.0, "structure": 10.0,
                  "faithfulness": 10.0, "conciseness": 10.0}
        corrected = HeuristicScorer.apply_bias_correction(scores, factor=0.9)
        assert corrected["clarity"] == pytest.approx(9.0, abs=0.01)

    def test_scores_clamped_to_minimum(self):
        scores = {"clarity": 1.0, "specificity": 1.0, "structure": 1.0,
                  "faithfulness": 1.0, "conciseness": 1.0}
        corrected = HeuristicScorer.apply_bias_correction(scores)
        # 1.0 * 0.85 = 0.85, but clamped to 1.0
        assert all(v >= 1.0 for v in corrected.values())


class TestStructuralHeuristics:
    def test_structure_score_with_headers(self):
        prompt = "# Task\nDo something\n## Details\n- item 1\n- item 2"
        score = HeuristicScorer.heuristic_structure(prompt)
        assert score > 5.0  # has headers and lists

    def test_structure_score_wall_of_text(self):
        prompt = "do the thing with the stuff and also handle the other thing"
        score = HeuristicScorer.heuristic_structure(prompt)
        assert score < 5.0

    def test_conciseness_verbose(self):
        prompt = ("I would really like you to please write me a very nice function "
                  "that would be able to take in some numbers and add them together")
        score = HeuristicScorer.heuristic_conciseness(prompt)
        assert score < 6.0

    def test_conciseness_tight(self):
        prompt = "Write sum(numbers: list[float]) -> float. Return the sum."
        score = HeuristicScorer.heuristic_conciseness(prompt)
        assert score > 5.0

    def test_specificity_with_constraints(self):
        prompt = ("Write a Python function validate_email(addr: str) -> bool "
                  "using RFC 5322 regex. Raise ValueError on None.")
        score = HeuristicScorer.heuristic_specificity(prompt)
        assert score > 5.0

    def test_divergence_detection(self):
        llm_scores = {"clarity": 9.0, "structure": 3.0}
        heuristic_scores = {"clarity": 4.0, "structure": 7.0}
        flags = HeuristicScorer.detect_divergence(llm_scores, heuristic_scores, threshold=2.0)
        assert "clarity" in flags
        assert "structure" in flags
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement heuristic scorer**

```python
# backend/app/services/heuristic_scorer.py
"""Passthrough bias correction and heuristic scoring.

Used for MCP passthrough where the IDE's LLM self-rates.
Applies systematic discount + structural sanity checks.
"""

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)


class HeuristicScorer:
    """Bias correction and structural heuristics for passthrough scoring."""

    @staticmethod
    def apply_bias_correction(
        scores: dict[str, float],
        factor: float | None = None,
    ) -> dict[str, float]:
        """Apply systematic bias correction (default 15% discount).

        Scores are clamped to [1.0, 10.0] after correction.
        """
        f = factor if factor is not None else settings.BIAS_CORRECTION_FACTOR
        return {
            dim: round(max(1.0, min(10.0, val * f)), 2)
            for dim, val in scores.items()
        }

    @staticmethod
    def heuristic_structure(prompt: str) -> float:
        """Structural analysis: headers, lists, XML tags, output format."""
        score = 3.0  # baseline
        lines = prompt.split("\n")
        headers = sum(1 for l in lines if l.strip().startswith("#"))
        lists = sum(1 for l in lines if re.match(r"\s*[-*\d]+[.)]\s", l))
        xml_tags = len(re.findall(r"<[\w-]+>", prompt))
        has_output_format = bool(re.search(r"(?i)(output|format|return|response).*:", prompt))

        if headers >= 2:
            score += 2.0
        elif headers >= 1:
            score += 1.0
        if lists >= 2:
            score += 1.5
        elif lists >= 1:
            score += 0.5
        if xml_tags >= 2:
            score += 1.0
        if has_output_format:
            score += 1.0

        return min(10.0, score)

    @staticmethod
    def heuristic_conciseness(prompt: str) -> float:
        """Conciseness via type-token ratio and filler detection."""
        words = prompt.lower().split()
        if not words:
            return 5.0

        # Type-token ratio (unique words / total words)
        ttr = len(set(words)) / len(words)

        # Filler phrase detection
        filler_phrases = [
            "i would like", "please", "if you could", "would be able to",
            "very", "really", "just", "actually", "basically",
        ]
        filler_count = sum(1 for f in filler_phrases if f in prompt.lower())

        score = 5.0 + (ttr - 0.5) * 6  # TTR 0.5 → 5.0, TTR 0.8 → 6.8
        score -= filler_count * 0.5

        return round(max(1.0, min(10.0, score)), 1)

    @staticmethod
    def heuristic_specificity(prompt: str) -> float:
        """Specificity via constraint density."""
        indicators = [
            r"(?i)\b(must|shall|should|required)\b",
            r"(?i)\b(return|raise|throw|error)\b",
            r"(?i)\b(type|int|str|float|bool|list|dict)\b",
            r"(?i)\b(format|json|csv|xml|html)\b",
            r"(?i)(example|e\.g\.|for instance)",
            r"\b\d+\b",  # numbers (often constraints)
        ]
        hits = sum(1 for pattern in indicators if re.search(pattern, prompt))
        score = 2.0 + hits * 1.3
        return round(min(10.0, score), 1)

    @staticmethod
    def detect_divergence(
        llm_scores: dict[str, float],
        heuristic_scores: dict[str, float],
        threshold: float = 2.0,
    ) -> list[str]:
        """Flag dimensions where LLM and heuristic diverge by > threshold."""
        flagged = []
        for dim in llm_scores:
            if dim in heuristic_scores:
                diff = abs(llm_scores[dim] - heuristic_scores[dim])
                if diff > threshold:
                    flagged.append(dim)
                    logger.info(
                        "Score divergence: dim=%s llm=%.1f heuristic=%.1f diff=%.1f",
                        dim, llm_scores[dim], heuristic_scores[dim], diff,
                    )
        return flagged
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/heuristic_scorer.py tests/test_heuristic_scorer.py
git commit -m "feat: implement heuristic scorer with bias correction and structural analysis"
```

---

## Chunk 3: Infrastructure and Pipeline Integration

### Task 7: Rate Limit Dependency

**Files:**
- Create: `backend/app/dependencies/rate_limit.py`

- [ ] **Step 1: Implement rate limiter**

```python
# backend/app/dependencies/rate_limit.py
"""In-memory rate limiting FastAPI dependency.

Uses the `limits` library with in-memory storage.
Rate strings are configurable via environment variables.
"""

import logging
from collections.abc import Callable

from fastapi import HTTPException, Request
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

logger = logging.getLogger(__name__)

_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)


class RateLimit:
    """FastAPI dependency for rate limiting.

    Usage: Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT))
    """

    def __init__(self, rate_string_factory: Callable[[], str]) -> None:
        self._rate_string_factory = rate_string_factory

    async def __call__(self, request: Request) -> None:
        rate_string = self._rate_string_factory()
        limit = parse(rate_string)
        key = self._get_client_ip(request)

        if not _limiter.hit(limit, key):
            logger.warning("Rate limit exceeded: %s for %s", rate_string, key)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again later.",
                headers={"Retry-After": "60"},
            )

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For from trusted proxies."""
        from app.config import settings

        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")

        if forwarded and client_ip in settings.TRUSTED_PROXIES:
            return forwarded.split(",")[0].strip()
        return client_ip
```

- [ ] **Step 2: Commit**

```bash
cd backend && git add app/dependencies/rate_limit.py
git commit -m "feat: implement in-memory rate limiting dependency"
```

---

### Task 8: Pipeline Integration

Update pipeline.py to use context_resolver and trace_logger, add faithfulness warning.

**Files:**
- Modify: `backend/app/services/pipeline.py`

- [ ] **Step 1: Update pipeline to integrate context resolver and trace logger**

Changes needed:
1. Import `ContextResolver` and `TraceLogger`
2. Use `ContextResolver.resolve()` at pipeline start for prompt validation
3. After each phase, call `trace_logger.log_phase()` with duration and token counts
4. After scoring, check if `optimized_scores.faithfulness < 6.0` → log WARNING and add flag to result
5. Store `tokens_by_phase` dict from trace data

The pipeline `run()` method signature stays the same. Context resolver replaces inline validation. Trace logger adds observability.

Key integration points:
- Before analyze: `ctx = ContextResolver.resolve(raw_prompt, strategy_override, ...)`
- After each `provider.complete_parsed()`: `trace_logger.log_phase(...)`
- After score mapping: `if optimized_scores.faithfulness < 6.0: logger.warning(...)`

- [ ] **Step 2: Run full test suite — verify no regressions**

Run: `cd backend && source .venv/bin/activate && pytest -v`

- [ ] **Step 3: Commit**

```bash
cd backend && git add app/services/pipeline.py
git commit -m "feat: integrate context resolver, trace logger, and faithfulness check into pipeline"
```

---

### Task 9: Score Calibration Tests

**Files:**
- Create: `backend/tests/test_score_calibration.py`

- [ ] **Step 1: Write score calibration tests**

```python
# backend/tests/test_score_calibration.py
"""Tests for score clustering detection via optimization_service."""

import pytest
from app.models import Optimization
from app.services.optimization_service import OptimizationService


@pytest.fixture
async def svc_with_data(db_session):
    """Insert enough optimizations for clustering detection."""
    for i in range(15):
        opt = Optimization(
            id=f"cal-{i}", raw_prompt=f"p{i}", optimized_prompt=f"b{i}",
            task_type="coding", strategy_used="auto",
            overall_score=7.8 + (i % 3) * 0.1,  # tight cluster: 7.8, 7.9, 8.0
            score_clarity=7.8, score_specificity=7.9, score_structure=7.8,
            score_faithfulness=8.0, score_conciseness=7.7,
            status="completed", trace_id=f"ct-{i}", provider="mock",
        )
        db_session.add(opt)
    await db_session.commit()
    return OptimizationService(db_session)


class TestScoreCalibration:
    async def test_clustering_detected_low_stddev(self, svc_with_data):
        stats = await svc_with_data.get_score_distribution()
        # With tight scores (7.8-8.0), stddev should be < 0.3
        assert stats["overall_score"]["stddev"] < 0.5
        assert stats["overall_score"]["count"] == 15

    async def test_no_clustering_with_spread(self, db_session):
        for i in range(15):
            opt = Optimization(
                id=f"spread-{i}", raw_prompt=f"p{i}", optimized_prompt=f"b{i}",
                task_type="coding", strategy_used="auto",
                overall_score=2.0 + i * 0.5,  # spread: 2.0 to 9.0
                score_clarity=2.0 + i * 0.5,
                status="completed", trace_id=f"st-{i}", provider="mock",
            )
            db_session.add(opt)
        await db_session.commit()
        svc = OptimizationService(db_session)
        stats = await svc.get_score_distribution()
        assert stats["overall_score"]["stddev"] > 1.0
```

- [ ] **Step 2: Run tests — verify they pass**

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_score_calibration.py
git commit -m "test: add score clustering detection tests"
```

---

## Chunk 4: Routers and Final Integration

### Task 10: History and Feedback Routers

**Files:**
- Create: `backend/app/routers/history.py`
- Create: `backend/app/routers/feedback.py`

- [ ] **Step 1: Implement history router**

```python
# backend/app/routers/history.py
"""History endpoint — sorted/filtered optimization list."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.optimization_service import OptimizationService

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def get_history(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = OptimizationService(db)
    result = await svc.list_optimizations(
        offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order,
        task_type=task_type, status=status,
    )
    return {
        "total": result["total"],
        "count": result["count"],
        "offset": result["offset"],
        "has_more": result["has_more"],
        "next_offset": result["next_offset"],
        "items": [
            {
                "id": opt.id, "created_at": opt.created_at.isoformat() if opt.created_at else None,
                "task_type": opt.task_type, "strategy_used": opt.strategy_used,
                "overall_score": opt.overall_score, "status": opt.status,
                "duration_ms": opt.duration_ms, "provider": opt.provider,
                "raw_prompt": opt.raw_prompt[:100],  # truncated preview
            }
            for opt in result["items"]
        ],
    }
```

- [ ] **Step 2: Implement feedback router**

```python
# backend/app/routers/feedback.py
"""Feedback endpoints — submit and list feedback."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.feedback_service import FeedbackService

router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackRequest(BaseModel):
    optimization_id: str
    rating: str = Field(..., pattern="^(thumbs_up|thumbs_down)$")
    comment: str | None = None


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    svc = FeedbackService(db)
    try:
        fb = await svc.create_feedback(body.optimization_id, body.rating, body.comment)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "id": fb.id,
        "optimization_id": fb.optimization_id,
        "rating": fb.rating,
        "comment": fb.comment,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }


@router.get("/feedback")
async def get_feedback(
    optimization_id: str,
    db: AsyncSession = Depends(get_db),
):
    svc = FeedbackService(db)
    feedbacks = await svc.get_for_optimization(optimization_id)
    agg = await svc.get_aggregation(optimization_id)
    return {
        "aggregation": agg,
        "items": [
            {
                "id": fb.id, "rating": fb.rating, "comment": fb.comment,
                "created_at": fb.created_at.isoformat() if fb.created_at else None,
            }
            for fb in feedbacks
        ],
    }
```

- [ ] **Step 3: Commit**

```bash
cd backend && git add app/routers/history.py app/routers/feedback.py
git commit -m "feat: implement history and feedback routers"
```

---

### Task 11: Provider, Settings, and Stub Routers

**Files:**
- Create: `backend/app/routers/providers.py`
- Create: `backend/app/routers/settings.py`
- Create: `backend/app/routers/github_auth.py`
- Create: `backend/app/routers/github_repos.py`

- [ ] **Step 1: Implement providers router**

```python
# backend/app/routers/providers.py
"""Provider info endpoint."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
async def get_providers(request: Request):
    provider = getattr(request.app.state, "provider", None)
    return {
        "active_provider": provider.name if provider else None,
        "available": ["claude_cli", "anthropic_api", "mcp_passthrough"],
    }
```

- [ ] **Step 2: Implement settings router**

```python
# backend/app/routers/settings.py
"""Read-only settings endpoint."""

from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def get_settings():
    return {
        "max_raw_prompt_chars": settings.MAX_RAW_PROMPT_CHARS,
        "max_context_tokens": settings.MAX_CONTEXT_TOKENS,
        "optimize_rate_limit": settings.OPTIMIZE_RATE_LIMIT,
        "feedback_rate_limit": settings.FEEDBACK_RATE_LIMIT,
        "embedding_model": settings.EMBEDDING_MODEL,
        "trace_retention_days": settings.TRACE_RETENTION_DAYS,
    }
```

- [ ] **Step 3: Implement GitHub stub routers (501)**

```python
# backend/app/routers/github_auth.py
"""GitHub OAuth stubs — returns 501 until Phase 2."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/auth/login")
@router.get("/auth/callback")
@router.get("/auth/me")
@router.post("/auth/logout")
async def github_auth_stub():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")
```

```python
# backend/app/routers/github_repos.py
"""GitHub repos stubs — returns 501 until Phase 2."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/repos")
@router.post("/repos/link")
@router.get("/repos/linked")
@router.delete("/repos/unlink")
async def github_repos_stub():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")
```

- [ ] **Step 4: Update main.py to include all new routers**

Add to `backend/app/main.py` (in the router include section):
```python
# Add these alongside existing health and optimize router includes
from app.routers.history import router as history_router
from app.routers.feedback import router as feedback_router
from app.routers.providers import router as providers_router
from app.routers.settings import router as settings_router
from app.routers.github_auth import router as github_auth_router
from app.routers.github_repos import router as github_repos_router

app.include_router(history_router)
app.include_router(feedback_router)
app.include_router(providers_router)
app.include_router(settings_router)
app.include_router(github_auth_router)
app.include_router(github_repos_router)
```

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/routers/ app/main.py
git commit -m "feat: implement provider, settings, and GitHub stub routers (8 total)"
```

---

### Task 12: Health Metrics + Rate Limiting on Optimize

**Files:**
- Modify: `backend/app/routers/health.py`
- Modify: `backend/app/routers/optimize.py`

- [ ] **Step 1: Update health router with pipeline metrics**

Add `score_health` and `avg_duration_ms` to the health endpoint:
- Query last 50 optimizations for score stats (mean, stddev, clustering warning)
- Compute avg duration per phase from recent optimizations
- Return `status: "healthy"` if provider present, else `"degraded"`

- [ ] **Step 2: Add rate limiting to optimize router**

Add `Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT))` to `POST /api/optimize`.

- [ ] **Step 3: Add rate limiting to feedback router**

Add `Depends(RateLimit(lambda: settings.FEEDBACK_RATE_LIMIT))` to `POST /api/feedback`.

- [ ] **Step 4: Run full suite — verify everything passes**

Run: `cd backend && source .venv/bin/activate && pytest -v`

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/routers/health.py app/routers/optimize.py app/routers/feedback.py
git commit -m "feat: add pipeline metrics to health endpoint and rate limiting"
```

---

### Task 13: Router Tests + Full Coverage + Handoff

**Files:**
- Modify: `backend/tests/test_routers.py` — add tests for new endpoints
- Create: `docs/superpowers/plans/handoffs/handoff-phase-1b.json`

- [ ] **Step 1: Write tests for all new router endpoints**

Add to existing `test_routers.py`:
- `GET /api/history` — returns list with pagination
- `POST /api/feedback` — creates feedback
- `GET /api/feedback?optimization_id=X` — returns feedback list
- `GET /api/providers` — returns active provider
- `GET /api/settings` — returns config values
- `GET /api/health` — returns score_health and avg_duration_ms
- GitHub stubs return 501

- [ ] **Step 2: Run full test suite with coverage**

Run: `cd backend && source .venv/bin/activate && pytest --cov=app --cov-report=term-missing -v`
Target: ≥90% coverage

- [ ] **Step 3: Generate handoff artifact**

Write `docs/superpowers/plans/handoffs/handoff-phase-1b.json` with actual test counts, coverage, and verification results.

- [ ] **Step 4: Commit and finalize**

```bash
cd backend && git add tests/ docs/
git commit -m "test: full Phase 1b test coverage and handoff artifact"
```

---

## Exit Conditions Checklist

| # | Condition | Task |
|---|-----------|------|
| 1 | context_resolver.py enforces per-source caps | Task 1 |
| 2 | Priority-based truncation verified | Task 1 |
| 3 | Prompt min (20 chars) / max (MAX_RAW_PROMPT_CHARS) rejection | Task 1 |
| 4 | External content wrapped in `<untrusted-context>` | Task 1 |
| 5 | trace_logger.py writes JSONL, readable by trace_id | Task 2 |
| 6 | optimization_service.py — CRUD, sort/filter, score distribution | Task 3 |
| 7 | feedback_service.py — CRUD, aggregation, sync adaptation update | Task 5 |
| 8 | adaptation_tracker.py — affinity, seed data, degenerate detection | Task 4 |
| 9 | heuristic_scorer.py — bias correction, structural analysis | Task 6 |
| 10 | Pipeline faithfulness check (warning if < 6.0) | Task 8 |
| 11 | Score clustering detection (10 early, 50 full) | Task 9 |
| 12 | POST /api/feedback persists + updates adaptation | Task 10 |
| 13 | GET /api/history returns sorted/filtered results | Task 10 |
| 14 | GET /api/health returns score_health + avg_duration_ms | Task 12 |
| 15 | All 8 routers implemented (GitHub stubs = 501) | Task 11 |
| 16 | In-memory rate limiting | Task 7, 12 |
| 17 | Backend coverage ≥ 90% | Task 13 |
| 18 | handoff-phase-1b.json written | Task 13 |
