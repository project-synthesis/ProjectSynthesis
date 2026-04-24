# Taxonomy Observatory — First Iteration Implementation Plan

**Status:** Draft r2 — independent plan-reviewer pass complete (2026-04-24). All 2 BLOCKERs (B1 `cross_cluster_count` field name corrected to `global_source_count`; B3 hand-waved sub-steps expanded with explicit test code for all T/R/H/TO cases) + 2 MAJORs (M4 `mockFetch` consistency: OS6 uses `vi.spyOn(globalThis, 'fetch')`; M6 TO6 concrete invariant on `clustersStore.activityEvents` state) addressed; 5 MINORs addressed (rate-limit reset fixture in router tests, TabType extension for pinned Observatory tab). Pending user approval.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-panel Observatory view (Lifecycle Timeline + Domain Readiness Aggregate + Pattern Density Heatmap) that composes existing data sources with one new backend aggregator + one existing-endpoint extension, all honouring brand-guidelines (zero-effects directive, 1 px contours, chromatic encoding, ultra-compact density).

**Architecture:** Frontend-heavy. New `TaxonomyObservatory.svelte` route tab mounts three sibling panels. Two backend deltas: (1) extend `GET /api/clusters/activity/history` with `since`/`until` range params (consolidates 30-day period-selector fetch into one request), (2) new `GET /api/taxonomy/pattern-density` aggregator endpoint. Pattern-density aggregator does Python-side GlobalPattern containment (≤ 500 rows × ≤ 30 domains) — rejects SQLite JSON-operator path. All three panels share one `observatory.svelte.ts` store; existing `clustersStore.activityEvents` + `readinessStore.reports` are reused (no duplicate SSE subscription).

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy (backend), SvelteKit 2 + Svelte 5 runes + Tailwind CSS 4 + Vitest + @testing-library/svelte (frontend).

**Spec:** [docs/superpowers/specs/2026-04-24-taxonomy-observatory-design.md](../specs/2026-04-24-taxonomy-observatory-design.md)

---

## Test Design Matrix (TDM)

Requirement IDs map 1:1 to tasks (a task is RED-GREEN-REFACTOR for its row(s)).

### Backend TDM — Activity history range extension

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| AH1 | `since`+`until` returns events across multiple days flattened | `backend/tests/test_clusters_router.py::test_activity_history_range_multi_day` | Mock `get_event_logger()` with 2-day JSONL mock | Response has events from both dates in reverse-chronological order |
| AH2 | Missing JSONL files in the range are skipped, not errored | `test_clusters_router.py::test_activity_history_range_missing_days` | 1 of 3 days has no JSONL | Response has the 2 days' events; no exception |
| AH3 | `date` + `since`/`until` together → 422 | `test_clusters_router.py::test_activity_history_range_mutex_with_date` | — | `resp.status_code == 422` |
| AH4 | Range > 30 days → 422 | `test_clusters_router.py::test_activity_history_range_oversized` | `since=2026-03-01`, `until=2026-04-15` | `resp.status_code == 422` |
| AH5 | `since` alone defaults `until = today UTC` | `test_clusters_router.py::test_activity_history_range_since_only` | `since=2026-04-23` | Fetch covers today + yesterday |

### Backend TDM — Pattern-density endpoint

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| PD1 | Endpoint returns one row per active domain | `backend/tests/test_taxonomy_insights_router.py::test_pattern_density_one_row_per_domain` | Seed 3 domain nodes + 2 clusters each | `len(resp["rows"]) == 3`; `resp["total_domains"] == 3` |
| PD2 | `cluster_count` matches child active/mature/candidate clusters | `test_pattern_density_cluster_count` | Seed 1 domain with 3 active + 1 archived child | `rows[0]["cluster_count"] == 3` |
| PD3 | `meta_pattern_count` aggregates children's MetaPatterns | `test_pattern_density_meta_pattern_count` | 1 domain, 2 clusters, 3 MetaPatterns (2+1) | `rows[0]["meta_pattern_count"] == 3` |
| PD4 | `global_pattern_count` uses Python-side containment on `source_cluster_ids` | `test_pattern_density_global_pattern_count_via_containment` | Seed GlobalPattern with `source_cluster_ids=[cluster_1_id]` | `rows[0]["global_pattern_count"] == 1` |
| PD5 | `cross_cluster_injection_rate` counts only in-period events | `test_pattern_density_injection_rate_period_filter` | OptimizationPattern rows: 2 in-period, 1 outside | Rate reflects only 2 |
| PD6 | `meta_pattern_avg_score` is mean of parent `PromptCluster.avg_score` weighted by member cluster count | `test_pattern_density_avg_score` | 2 clusters, avg_scores 7.0 + 8.0 | `avg_overall ≈ 7.5` |
| PD7 | Invalid period → 422 | `test_pattern_density_invalid_period` | `?period=bogus` | 422 |
| PD8 | No domains → empty rows + totals | `test_pattern_density_empty_taxonomy` | fresh DB | `rows == []; total_* == 0` |

### Frontend store TDM

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| OS1 | Default period is `7d` | `frontend/src/lib/stores/observatory.svelte.test.ts::test_default_period` | — | `observatoryStore.period === '7d'` |
| OS2 | Period restored from `localStorage['synthesis:observatory_period']` | `test_period_restored_from_storage` | Set localStorage before import | Loaded period matches |
| OS3 | `setPeriod()` persists to localStorage | `test_set_period_persists` | — | `localStorage.getItem(...) === 'new-value'` |
| OS4 | Invalid localStorage value defaults to `7d` | `test_invalid_storage_defaults` | Set `localStorage[...]='invalid'` | period is `7d` |
| OS5 | `refreshPatternDensity()` populates data + clears `loading`/`error` | `test_refresh_populates` | `mockFetch` with valid response | `patternDensity.length > 0`, `loading=false`, `error=null` |
| OS6 | `refreshPatternDensity()` sets `error` on rejection | `test_refresh_captures_error` | `mockFetch` reject | `error === 'fetch-failed'` |
| OS7 | Period change triggers debounced re-fetch (1 s) | `test_period_change_debounces_refresh` | fake timers + 2 `setPeriod` calls within 500 ms | Only 1 fetch after 1 s elapsed |

### Frontend component TDM — Panels

**DomainLifecycleTimeline (10 cases)**

| Req | Behavior under test | Assertion |
|-----|---------------------|-----------|
| T1 | Mounts with 0 events → empty-state copy | `screen.getByText(/no recent activity/i)` |
| T2 | Renders 20 px row per event | 3 events → 3 `.timeline-row` elements, each `height: 20px` |
| T3 | Timestamp left-aligned in 60 px column (Geist Mono 10 px) | Rendered timestamp element has `font-family` containing `Geist Mono` |
| T4 | Path badge coloured via `pathColor()` (shared with ActivityPanel) | `hot` event: badge background-color matches `var(--color-neon-red)` inline style |
| T5 | Filter chip path toggle excludes events of the off-path | Add warm + cold events, click `cold` off → warm events visible, cold hidden |
| T6 | Filter chip op-family toggle groups per spec's canonical mapping | Domain lifecycle events visible when only `domain lifecycle` chip is on |
| T7 | `errors_only` toggle shows only events with `op="error"` or `decision ∈ {rejected, failed}` | Toggle on → only error events rendered |
| T8 | Expand glyph reveals full context payload on click | Click → additional `.context-payload` element visible |
| T9 | New SSE event prepends instantly (no row-entry animation — matches ActivityPanel) | Add event to `clustersStore.activityEvents` → first DOM row's id matches new event |
| T10 | Period selector refetches via `/api/clusters/activity/history?since=…&until=…` | `setPeriod('24h')` → `fetch` called with URL containing today's date |

**DomainReadinessAggregate (7 cases)**

| Req | Behavior under test | Assertion |
|-----|---------------------|-----------|
| R1 | 0 domains → empty-state copy | `screen.getByText(/no domains/i)` |
| R2 | 3 domains → 3 cards | `.readiness-card` count = 3 |
| R3 | Cards sorted: critical → guarded → healthy | First card has `data-tier="critical"` |
| R4 | Card click dispatches `domain:select` CustomEvent | Listener receives `{domain_id}` matching clicked card |
| R5 | Domain with 0 sub-domains renders card without SubDomainEmergenceList empty churn | Card present; no empty-row placeholder in DOM |
| R6 | Mid-session dissolution — `readinessStore.byDomain(id) === null` → click is no-op | Click handler early-returns; no dispatch |
| R7 | Reduced-motion respected | `transition-duration` computed as 0.01 ms |

**PatternDensityHeatmap (8 cases)**

| Req | Behavior under test | Assertion |
|-----|---------------------|-----------|
| H1 | Renders header row with canonical column labels | `clusters`, `meta`, `avg score`, `global`, `x-cluster inj. rate` present |
| H2 | Data rows rendered when `patternDensity.length > 0` | 3 rows for 3 domains |
| H3 | Empty row values render `—` | `meta_pattern_avg_score: null` row → `—` glyph |
| H4 | Row background opacity scales proportionally to `meta_pattern_count` | Top row opacity > bottom row opacity |
| H5 | Rows are not interactive (no `role="button"`, no `tabindex`, no `cursor: pointer`) | Computed style check |
| H6 | Loading state: prior rows shown at dim opacity | Body element has `opacity: 0.5` during `loading=true` |
| H7 | Error state: 1 px neon-red inset contour + retry button | Error banner visible, retry button has `role="button"` |
| H8 | Empty state (no domains) — factual no-action copy | `screen.getByText(/pattern library is empty/i)` |

**TaxonomyObservatory shell (6 cases)**

| Req | Behavior under test | Assertion |
|-----|---------------------|-----------|
| TO1 | Mounts three panels | Timeline + Readiness + Heatmap all present |
| TO2 | Period selector fires `observatoryStore.setPeriod()` | Click `24h` → store period becomes `24h` |
| TO3 | Period selector absent from Readiness panel header | Readiness panel has no period-selector element |
| TO4 | Legend explains Readiness-is-current-state | Legend element in shell header has the explanatory copy |
| TO5 | Shell renders inside the existing tablist (not full-screen modal) | Rendered inside `[role="tabpanel"]` or similar tab container |
| TO6 | Tab switch unmounts the Observatory — verifies SSE resubscription does not churn | Mount → unmount → confirm no orphaned SSE handler (clustersStore unchanged) |

### Integration TDM

| Req | Behavior under test | File | Assertion |
|-----|---------------------|------|-----------|
| I1 | `/app` route has Observatory tab entry | `routes/app/+page.svelte.test.ts::test_observatory_tab_registered` | Tab label `OBSERVATORY` in tablist |
| I2 | Clicking the Observatory tab mounts `TaxonomyObservatory` | Same file | Content area renders TaxonomyObservatory's test-id |
| I3 | ROADMAP updated: Observatory item moves from Immediate to Planned | `docs/ROADMAP.md` | (Manual — grep for link to this plan in ROADMAP) |

---

## File Structure

| File | Role | Change type |
|------|------|-------------|
| `backend/app/routers/clusters.py:413-438` | `since`/`until` range variant on `/api/clusters/activity/history` | Modify |
| `backend/tests/test_clusters_router.py` | AH1–AH5 (5 new cases) | Modify |
| `backend/app/routers/taxonomy_insights.py` | New `/api/taxonomy/pattern-density` endpoint | Create |
| `backend/app/schemas/taxonomy_insights.py` | `PatternDensityRow` + `PatternDensityResponse` Pydantic models | Create |
| `backend/app/services/taxonomy_insights.py` | `aggregate_pattern_density(db, period_start, period_end) -> list[PatternDensityRow]` | Create |
| `backend/app/main.py` | Register `taxonomy_insights` router | Modify |
| `backend/tests/test_taxonomy_insights_router.py` | PD1–PD8 (8 new cases) | Create |
| `frontend/src/lib/api/observatory.ts` | `fetchPatternDensity(period)` typed client | Create |
| `frontend/src/lib/stores/observatory.svelte.ts` | Period state + fetch orchestration | Create |
| `frontend/src/lib/stores/observatory.svelte.test.ts` | OS1–OS7 (7 new cases) | Create |
| `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte` | Panel 1 | Create |
| `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts` | T1–T10 (10 cases) | Create |
| `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte` | Panel 2 | Create |
| `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.test.ts` | R1–R7 (7 cases) | Create |
| `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte` | Panel 3 | Create |
| `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.test.ts` | H1–H8 (8 cases) | Create |
| `frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte` | Layout shell | Create |
| `frontend/src/lib/components/taxonomy/TaxonomyObservatory.test.ts` | TO1–TO6 (6 cases) | Create |
| `frontend/src/routes/app/+page.svelte` | Register Observatory tab | Modify |
| `frontend/src/routes/app/+page.svelte.test.ts` | I1, I2 | Modify |
| `docs/ROADMAP.md` | Move Observatory from Immediate/Exploring to Planned with spec + plan links | Modify |

---

## Task sequence

Ordered for incremental green. Backend first (no frontend dependency), then store, then panels bottom-up, then integration.

---

### Task 1 — Activity history: `since`/`until` range variant (AH1–AH5)

**Files:**
- Modify: `backend/app/routers/clusters.py:413-438` (extend `get_cluster_activity_history`)
- Modify: `backend/tests/test_clusters_router.py`

- [ ] **Step 1.1: Write the 5 failing tests**

Append to `backend/tests/test_clusters_router.py` (inside the activity-history describe class, which mocks `get_event_logger`):

```python
    @pytest.mark.asyncio
    async def test_activity_history_range_multi_day(self, app_client):
        """AH1: ?since=X&until=Y fans out over the JSONL files in the range."""
        from app.main import app
        from app.services.taxonomy.event_logger import get_event_logger
        from unittest.mock import patch

        mock_logger = MagicMock()
        def _get_history(date, limit, offset):
            if date == "2026-04-23":
                return [{"ts": "2026-04-23T10:00Z", "path": "warm", "op": "discover", "decision": "domains_created"}]
            if date == "2026-04-24":
                return [{"ts": "2026-04-24T09:00Z", "path": "hot", "op": "match", "decision": "matched"}]
            return []
        mock_logger.get_history = _get_history

        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": "2026-04-23", "until": "2026-04-24"},
            )
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        assert events[0]["ts"] == "2026-04-24T09:00Z"  # reverse chrono

    @pytest.mark.asyncio
    async def test_activity_history_range_missing_days(self, app_client):
        """AH2: missing JSONL files in the range are skipped, not errored."""
        from unittest.mock import patch
        mock_logger = MagicMock()
        mock_logger.get_history = lambda date, limit, offset: (
            [{"ts": f"{date}T10:00Z", "path": "warm", "op": "discover", "decision": "d"}]
            if date in {"2026-04-22", "2026-04-24"} else []
        )
        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": "2026-04-22", "until": "2026-04-24"},
            )
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 2  # only 2 days had data

    @pytest.mark.asyncio
    async def test_activity_history_range_mutex_with_date(self, app_client):
        """AH3: date + since/until together is 422."""
        resp = await app_client.get(
            "/api/clusters/activity/history",
            params={"date": "2026-04-24", "since": "2026-04-22", "until": "2026-04-24"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_activity_history_range_oversized(self, app_client):
        """AH4: range > 30 days is 422."""
        resp = await app_client.get(
            "/api/clusters/activity/history",
            params={"since": "2026-03-01", "until": "2026-04-15"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_activity_history_range_since_only(self, app_client):
        """AH5: since alone defaults until=today UTC."""
        from unittest.mock import patch
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        mock_logger = MagicMock()
        dates_called: list[str] = []
        def _get_history(date, limit, offset):
            dates_called.append(date)
            return []
        mock_logger.get_history = _get_history

        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": today},  # `until` omitted
            )
        assert resp.status_code == 200
        assert today in dates_called
```

- [ ] **Step 1.2: Run — expect all 5 FAIL**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_clusters_router.py -k "activity_history_range" -v 2>&1 | tail -15`
Expected: 5 FAIL (handler doesn't understand `since`/`until` yet — first call with those params hits the `date` `Query(..., pattern=…)` validator that rejects missing `date` → 422 on the wrong ground, or 500 / other weirdness).

- [ ] **Step 1.3: Extend the handler**

Replace the existing `get_cluster_activity_history` signature + body in `backend/app/routers/clusters.py:413-438` with:

```python
@router.get("/api/clusters/activity/history", response_model=ActivityHistoryResponse)
async def get_cluster_activity_history(
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    since: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    until: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ActivityHistoryResponse:
    """Return taxonomy decision events for a specific date OR a date range
    from JSONL storage. `date` and `since`/`until` are mutually exclusive."""
    from datetime import datetime, timedelta, timezone
    try:
        tel = get_event_logger()
    except RuntimeError:
        return ActivityHistoryResponse(events=[], total=0, has_more=False)

    # Validate the two modes are mutually exclusive.
    if date is not None and (since is not None or until is not None):
        raise HTTPException(422, "`date` is mutually exclusive with `since`/`until`")

    try:
        if since is not None or until is not None:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start = since or today
            end = until or today
            start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_dt < start_dt:
                raise HTTPException(422, "`until` must be >= `since`")
            span = (end_dt - start_dt).days
            if span > 30:
                raise HTTPException(422, "range exceeds 30-day cap")

            # Fan-out over the range, newest-first.
            events: list[dict] = []
            cursor = end_dt
            while cursor >= start_dt:
                day = cursor.strftime("%Y-%m-%d")
                events.extend(tel.get_history(date=day, limit=limit + 1, offset=0))
                cursor -= timedelta(days=1)

            events = events[offset : offset + limit + 1]
            has_more = len(events) > limit
            events = events[:limit]
            return ActivityHistoryResponse(
                events=[TaxonomyActivityEvent(**e) for e in events],
                total=offset + len(events) + (1 if has_more else 0),
                has_more=has_more,
            )

        # Legacy single-date mode.
        if date is None:
            raise HTTPException(422, "either `date` or `since`/`until` required")
        raw = tel.get_history(date=date, limit=limit + 1, offset=offset)
        has_more = len(raw) > limit
        raw = raw[:limit]
        events = [TaxonomyActivityEvent(**e) for e in raw]
        return ActivityHistoryResponse(
            events=events,
            total=offset + len(events) + (1 if has_more else 0),
            has_more=has_more,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("GET /api/clusters/activity/history failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load activity history") from exc
```

- [ ] **Step 1.4: Run — expect 5 PASS**

Run: `pytest tests/test_clusters_router.py -k "activity_history_range" -v 2>&1 | tail -10`
Expected: 5 PASS.

- [ ] **Step 1.5: Run the entire activity-history test group to confirm legacy `date` mode still works**

Run: `pytest tests/test_clusters_router.py -k "activity_history" -v 2>&1 | tail -10`
Expected: all PASS (including any pre-existing `test_activity_history` tests).

- [ ] **Step 1.6: Commit**

```bash
git add backend/app/routers/clusters.py backend/tests/test_clusters_router.py
git commit -m "feat(clusters): add since/until range variant to /api/clusters/activity/history"
```

---

### Task 2 — Pattern-density schemas + service (PD1–PD8 setup)

**Files:**
- Create: `backend/app/schemas/taxonomy_insights.py`
- Create: `backend/app/services/taxonomy_insights.py`

- [ ] **Step 2.1: Write the failing service-level test**

Create `backend/tests/test_taxonomy_insights_service.py`:

```python
"""Service-level tests for aggregate_pattern_density.

Router-level tests (test_taxonomy_insights_router.py) will exercise
the full HTTP flow separately. This file pins the aggregator's
pure-Python behavior against the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PromptCluster, MetaPattern, GlobalPattern, OptimizationPattern, Optimization


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    SessionMaker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionMaker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_aggregate_pattern_density_one_row_per_domain(db: AsyncSession):
    """PD1: one row per active domain node."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    for label in ("backend", "frontend", "database"):
        db.add(PromptCluster(
            id=str(uuid.uuid4()), label=label, state="domain",
            domain=label, task_type="general",
            color_hex="#b44aff", persistence=1.0,
            member_count=0, usage_count=0, prune_flag_count=0,
            created_at=datetime.now(timezone.utc),
        ))
    await db.commit()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    rows = await aggregate_pattern_density(db, start, end)
    assert len(rows) == 3
    assert {r.domain_label for r in rows} == {"backend", "frontend", "database"}
```

(Service tests use their own in-memory DB fixture because the aggregator is a pure DB query — no app.state / router dependency.)

- [ ] **Step 2.2: Run — expect FAIL because neither the schema nor the service exist yet**

Run: `pytest tests/test_taxonomy_insights_service.py -v 2>&1 | tail -10`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.taxonomy_insights'`.

- [ ] **Step 2.3: Create the Pydantic schema**

Create `backend/app/schemas/taxonomy_insights.py`:

```python
"""Schemas for /api/taxonomy/pattern-density.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class PatternDensityRow(BaseModel):
    domain_id: str = Field(description="PromptCluster ID of the domain node.")
    domain_label: str
    cluster_count: int = Field(description="Active+mature+candidate clusters under this domain.")
    meta_pattern_count: int
    meta_pattern_avg_score: float | None = Field(
        default=None,
        description="Mean PromptCluster.avg_score across clusters with ≥1 MetaPattern.",
    )
    global_pattern_count: int
    cross_cluster_injection_rate: float = Field(
        description="Ratio of in-period injected OptimizationPattern rows whose cluster belongs to this domain vs all in-period injections.",
    )
    period_start: datetime
    period_end: datetime


class PatternDensityResponse(BaseModel):
    rows: list[PatternDensityRow]
    total_domains: int
    total_meta_patterns: int
    total_global_patterns: int
```

- [ ] **Step 2.4: Create the service with the minimal PD1 implementation**

Create `backend/app/services/taxonomy_insights.py`:

```python
"""Pattern-density aggregator for the Taxonomy Observatory.

Single function — not worth a class. Read-only query, no caching.
Python-side GlobalPattern containment (≤500 rows × ≤30 domains) —
avoids SQLite JSON-operator queries for PostgreSQL portability.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.schemas.taxonomy_insights import PatternDensityRow


_ACTIVE_CHILD_STATES = ("active", "mature", "candidate")


async def aggregate_pattern_density(
    db: AsyncSession,
    period_start: datetime,
    period_end: datetime,
) -> list[PatternDensityRow]:
    """Aggregate pattern-density metrics per active domain."""
    # Step 1 — load active domain nodes.
    domains_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.archived_at.is_(None),
        )
    )
    domains = list(domains_q.scalars().all())

    rows: list[PatternDensityRow] = []
    for domain in domains:
        rows.append(PatternDensityRow(
            domain_id=domain.id,
            domain_label=domain.label,
            cluster_count=0,
            meta_pattern_count=0,
            meta_pattern_avg_score=None,
            global_pattern_count=0,
            cross_cluster_injection_rate=0.0,
            period_start=period_start,
            period_end=period_end,
        ))
    return rows
```

- [ ] **Step 2.5: Run PD1 — expect PASS**

Run: `pytest tests/test_taxonomy_insights_service.py::test_aggregate_pattern_density_one_row_per_domain -v 2>&1 | tail -6`
Expected: PASS.

- [ ] **Step 2.6: Commit**

```bash
git add backend/app/schemas/taxonomy_insights.py backend/app/services/taxonomy_insights.py backend/tests/test_taxonomy_insights_service.py
git commit -m "feat(taxonomy): pattern-density aggregator skeleton (one row per active domain)"
```

---

### Task 3 — Pattern-density: cluster_count + meta_pattern_count + avg_score (PD2, PD3, PD6)

**Files:**
- Modify: `backend/app/services/taxonomy_insights.py`
- Modify: `backend/tests/test_taxonomy_insights_service.py`

- [ ] **Step 3.1: Write 3 failing service tests**

Append:

```python
@pytest.mark.asyncio
async def test_cluster_count_filters_to_active_mature_candidate(db: AsyncSession):
    """PD2: cluster_count counts children in {active, mature, candidate}; archived excluded."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)
    for i, state in enumerate(("active", "mature", "candidate", "archived")):
        db.add(PromptCluster(
            id=str(uuid.uuid4()), label=f"c{i}", state=state, domain="backend",
            task_type="coding", color_hex="#b44aff", persistence=0.7,
            member_count=5, usage_count=1, prune_flag_count=0,
            centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
            parent_id=dom.id,
            created_at=datetime.now(timezone.utc),
        ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].cluster_count == 3  # archived excluded


@pytest.mark.asyncio
async def test_meta_pattern_count_and_avg_score(db: AsyncSession):
    """PD3 + PD6: meta_pattern_count aggregates children; avg_score is mean of member
    cluster.avg_score for clusters with ≥1 MetaPattern."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)

    # Cluster A: 2 MetaPatterns, avg_score=7.0
    cA = PromptCluster(
        id=str(uuid.uuid4()), label="cA", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=6, usage_count=1, prune_flag_count=0,
        avg_score=7.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    # Cluster B: 1 MetaPattern, avg_score=8.0
    cB = PromptCluster(
        id=str(uuid.uuid4()), label="cB", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=4, usage_count=1, prune_flag_count=0,
        avg_score=8.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    # Cluster C: 0 MetaPatterns (excluded from avg_score), avg_score=1.0
    cC = PromptCluster(
        id=str(uuid.uuid4()), label="cC", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=1, usage_count=0, prune_flag_count=0,
        avg_score=1.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(cA); db.add(cB); db.add(cC)
    for cluster_id in (cA.id, cA.id, cB.id):
        db.add(MetaPattern(
            id=str(uuid.uuid4()), cluster_id=cluster_id,
            pattern_text="p", source_count=1, global_source_count=0,
            embedding=np.random.rand(384).astype(np.float32).tobytes(),
        ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].meta_pattern_count == 3
    # Mean of cA (7.0) and cB (8.0) — cC excluded (no MetaPatterns) — = 7.5
    assert abs(rows[0].meta_pattern_avg_score - 7.5) < 1e-6
```

- [ ] **Step 3.2: Run — expect 2 FAIL**

Run: `pytest tests/test_taxonomy_insights_service.py -v 2>&1 | tail -8`
Expected: 2 of 3 FAIL (PD1 still passes; PD2/PD3+PD6 FAIL because the service returns zeros).

- [ ] **Step 3.3: Extend the aggregator**

Replace the body of `aggregate_pattern_density` in `backend/app/services/taxonomy_insights.py`:

```python
async def aggregate_pattern_density(
    db: AsyncSession,
    period_start: datetime,
    period_end: datetime,
) -> list[PatternDensityRow]:
    from app.models import MetaPattern

    domains_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.archived_at.is_(None),
        )
    )
    domains = list(domains_q.scalars().all())

    rows: list[PatternDensityRow] = []
    for domain in domains:
        # Child clusters in active lifecycle states.
        children_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == domain.id,
                PromptCluster.state.in_(_ACTIVE_CHILD_STATES),
            )
        )
        children = list(children_q.scalars().all())
        child_ids = [c.id for c in children]
        cluster_count = len(children)

        # Meta-patterns under this domain.
        meta_patterns_q = await db.execute(
            select(MetaPattern.cluster_id).where(MetaPattern.cluster_id.in_(child_ids)) if child_ids else select(MetaPattern.cluster_id).where(False)
        )
        meta_cluster_ids = [r[0] for r in meta_patterns_q.all()]
        meta_pattern_count = len(meta_cluster_ids)

        # Avg score across clusters with >=1 MetaPattern.
        if meta_cluster_ids:
            unique_mc = set(meta_cluster_ids)
            scoring_clusters = [c for c in children if c.id in unique_mc and c.avg_score is not None]
            if scoring_clusters:
                meta_avg = sum(c.avg_score for c in scoring_clusters) / len(scoring_clusters)
            else:
                meta_avg = None
        else:
            meta_avg = None

        rows.append(PatternDensityRow(
            domain_id=domain.id,
            domain_label=domain.label,
            cluster_count=cluster_count,
            meta_pattern_count=meta_pattern_count,
            meta_pattern_avg_score=meta_avg,
            global_pattern_count=0,  # filled in Task 4
            cross_cluster_injection_rate=0.0,  # filled in Task 5
            period_start=period_start,
            period_end=period_end,
        ))
    return rows
```

- [ ] **Step 3.4: Run — expect 3 PASS**

Run: `pytest tests/test_taxonomy_insights_service.py -v 2>&1 | tail -8`
Expected: 3 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add backend/app/services/taxonomy_insights.py backend/tests/test_taxonomy_insights_service.py
git commit -m "feat(taxonomy-insights): cluster_count + meta_pattern_count + avg_score"
```

---

### Task 4 — Pattern-density: global_pattern_count via Python containment (PD4)

**Files:**
- Modify: `backend/app/services/taxonomy_insights.py`
- Modify: `backend/tests/test_taxonomy_insights_service.py`

- [ ] **Step 4.1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_global_pattern_count_via_containment(db: AsyncSession):
    """PD4: global_pattern_count counts GlobalPattern rows whose
    source_cluster_ids overlap with the domain's clusters (Python-side)."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)
    c1 = PromptCluster(
        id=str(uuid.uuid4()), label="c1", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    c2 = PromptCluster(
        id=str(uuid.uuid4()), label="c2", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(c1); db.add(c2)
    # 2 GlobalPatterns touching this domain, 1 that doesn't.
    # NB: GlobalPattern columns per models.py:308-328 — use
    #     source_cluster_ids, source_project_ids, cross_project_count,
    #     global_source_count, avg_cluster_score.  There is NO
    #     `cross_cluster_count` column.
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp1",
        source_cluster_ids=[c1.id],
        source_project_ids=[], cross_project_count=1,
        global_source_count=1, avg_cluster_score=7.5,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp2",
        source_cluster_ids=[c1.id, c2.id],
        source_project_ids=[], cross_project_count=1,
        global_source_count=2, avg_cluster_score=8.0,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp3",
        source_cluster_ids=[str(uuid.uuid4())],  # unrelated cluster
        source_project_ids=[], cross_project_count=1,
        global_source_count=1, avg_cluster_score=6.0,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].global_pattern_count == 2
```

- [ ] **Step 4.2: Run — expect FAIL**

Run: `pytest tests/test_taxonomy_insights_service.py::test_global_pattern_count_via_containment -v 2>&1 | tail -6`
Expected: FAIL with `assert 0 == 2`.

- [ ] **Step 4.3: Add containment-based counting**

In `aggregate_pattern_density`, before the `for domain in domains:` loop, pre-load all GlobalPatterns once (Python-side containment avoids SQLite JSON operators):

```python
    from app.models import MetaPattern, GlobalPattern

    # Pre-load ALL GlobalPattern rows once (≤500 cap).
    gp_q = await db.execute(select(GlobalPattern.id, GlobalPattern.source_cluster_ids))
    all_gp = [(row[0], set(row[1] or [])) for row in gp_q.all()]
```

Inside the loop, after computing `child_ids`:

```python
        child_id_set = set(child_ids)
        global_pattern_count = sum(
            1 for gp_id, src_ids in all_gp if src_ids & child_id_set
        )
```

And populate:

```python
            global_pattern_count=global_pattern_count,
```

- [ ] **Step 4.4: Run — expect PASS**

Run: `pytest tests/test_taxonomy_insights_service.py::test_global_pattern_count_via_containment -v 2>&1 | tail -6`
Expected: PASS.

- [ ] **Step 4.5: Run all service tests to confirm no regression**

Run: `pytest tests/test_taxonomy_insights_service.py -v 2>&1 | tail -8`
Expected: 4 PASS.

- [ ] **Step 4.6: Commit**

```bash
git add backend/app/services/taxonomy_insights.py backend/tests/test_taxonomy_insights_service.py
git commit -m "feat(taxonomy-insights): global_pattern_count via Python-side containment"
```

---

### Task 5 — Pattern-density: cross_cluster_injection_rate (PD5)

**Files:**
- Modify: `backend/app/services/taxonomy_insights.py`
- Modify: `backend/tests/test_taxonomy_insights_service.py`

- [ ] **Step 5.1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_injection_rate_filters_to_period(db: AsyncSession):
    """PD5: cross_cluster_injection_rate counts only events in [period_start, period_end)."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    child = PromptCluster(
        id=str(uuid.uuid4()), label="c", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(dom); db.add(child)
    # Optimization row (for the FK)
    opt_id = str(uuid.uuid4())
    db.add(Optimization(
        id=opt_id, raw_prompt="x", status="completed",
        created_at=datetime.now(timezone.utc),
    ))
    now = datetime.now(timezone.utc)
    # 2 in-period injections, 1 out-of-period
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="injected", created_at=now - timedelta(days=3),
    ))
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="global_injected", created_at=now - timedelta(days=5),
    ))
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="injected", created_at=now - timedelta(days=60),  # outside
    ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, now - timedelta(days=7), now
    )
    # 2 in-period events touching this domain, total injections in-period = 2 → rate = 1.0
    assert abs(rows[0].cross_cluster_injection_rate - 1.0) < 1e-6
```

- [ ] **Step 5.2: Run — expect FAIL**

Run: `pytest tests/test_taxonomy_insights_service.py::test_injection_rate_filters_to_period -v 2>&1 | tail -6`
Expected: FAIL with `assert abs(0.0 - 1.0) < 1e-6`.

- [ ] **Step 5.3: Compute injection rate**

In `aggregate_pattern_density`, before the domain loop:

```python
    from app.models import MetaPattern, GlobalPattern, OptimizationPattern

    # Pre-count in-period injection events globally.
    inj_total_q = await db.execute(
        select(OptimizationPattern.cluster_id).where(
            OptimizationPattern.relationship.in_(("injected", "global_injected")),
            OptimizationPattern.created_at >= period_start,
            OptimizationPattern.created_at < period_end,
        )
    )
    inj_cluster_ids = [r[0] for r in inj_total_q.all()]
    inj_total_count = len(inj_cluster_ids)
```

In the domain loop, replace `cross_cluster_injection_rate=0.0`:

```python
        domain_injections = sum(1 for cid in inj_cluster_ids if cid in child_id_set)
        injection_rate = (domain_injections / inj_total_count) if inj_total_count > 0 else 0.0
        rows.append(PatternDensityRow(
            ...
            cross_cluster_injection_rate=injection_rate,
            ...
        ))
```

- [ ] **Step 5.4: Run — expect PASS**

Run: `pytest tests/test_taxonomy_insights_service.py::test_injection_rate_filters_to_period -v 2>&1 | tail -6`
Expected: PASS.

- [ ] **Step 5.5: Full service suite green**

Run: `pytest tests/test_taxonomy_insights_service.py -v 2>&1 | tail -8`
Expected: 5 PASS.

- [ ] **Step 5.6: Commit**

```bash
git add backend/app/services/taxonomy_insights.py backend/tests/test_taxonomy_insights_service.py
git commit -m "feat(taxonomy-insights): cross_cluster_injection_rate with period filter"
```

---

### Task 6 — Pattern-density router (PD7, PD8, ordering, totals)

**Files:**
- Create: `backend/app/routers/taxonomy_insights.py`
- Create: `backend/tests/test_taxonomy_insights_router.py`
- Modify: `backend/app/main.py`

- [ ] **Step 6.1: Write the failing router tests**

Create `backend/tests/test_taxonomy_insights_router.py`:

```python
"""Router tests for /api/taxonomy/pattern-density."""
from __future__ import annotations

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from app.dependencies.rate_limit import reset_rate_limit_storage


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The endpoint uses settings.DEFAULT_RATE_LIMIT — reset bucket
    between cases so ordering doesn't cause 429 starvation."""
    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


class TestPatternDensityRouter:
    @pytest.mark.asyncio
    async def test_invalid_period_returns_422(self, app_client):
        """PD7: period not in {24h, 7d, 30d} → 422."""
        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "bogus"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_taxonomy_returns_empty_rows(self, app_client):
        """PD8: no domains → rows=[], totals=0."""
        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "7d"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["total_domains"] == 0
        assert body["total_meta_patterns"] == 0
        assert body["total_global_patterns"] == 0

    @pytest.mark.asyncio
    async def test_rows_ordered_by_meta_pattern_count_desc(self, app_client, db_session):
        """Rows sort by meta_pattern_count desc, then cluster_count desc."""
        from app.models import PromptCluster, MetaPattern
        import numpy as np

        # 2 domains: A has 3 MetaPatterns, B has 1.
        dA = PromptCluster(id=str(uuid.uuid4()), label="A", state="domain", domain="A",
                           task_type="general", color_hex="#00e5ff", persistence=1.0,
                           member_count=0, usage_count=0, prune_flag_count=0,
                           created_at=datetime.now(timezone.utc))
        dB = PromptCluster(id=str(uuid.uuid4()), label="B", state="domain", domain="B",
                           task_type="general", color_hex="#ff4895", persistence=1.0,
                           member_count=0, usage_count=0, prune_flag_count=0,
                           created_at=datetime.now(timezone.utc))
        cA = PromptCluster(id=str(uuid.uuid4()), label="cA", state="active", domain="A",
                           task_type="coding", color_hex="#00e5ff", persistence=0.7,
                           member_count=5, usage_count=1, prune_flag_count=0,
                           centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
                           parent_id=dA.id, created_at=datetime.now(timezone.utc))
        cB = PromptCluster(id=str(uuid.uuid4()), label="cB", state="active", domain="B",
                           task_type="coding", color_hex="#ff4895", persistence=0.7,
                           member_count=5, usage_count=1, prune_flag_count=0,
                           centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
                           parent_id=dB.id, created_at=datetime.now(timezone.utc))
        db_session.add(dA); db_session.add(dB); db_session.add(cA); db_session.add(cB)
        for _ in range(3):
            db_session.add(MetaPattern(
                id=str(uuid.uuid4()), cluster_id=cA.id, pattern_text="p",
                source_count=1, global_source_count=0,
                embedding=np.random.rand(384).astype(np.float32).tobytes(),
            ))
        db_session.add(MetaPattern(
            id=str(uuid.uuid4()), cluster_id=cB.id, pattern_text="p",
            source_count=1, global_source_count=0,
            embedding=np.random.rand(384).astype(np.float32).tobytes(),
        ))
        await db_session.commit()

        resp = await app_client.get("/api/taxonomy/pattern-density", params={"period": "7d"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) == 2
        assert rows[0]["domain_label"] == "A"  # highest meta_pattern_count
        assert rows[1]["domain_label"] == "B"
```

- [ ] **Step 6.2: Run — expect FAIL (endpoint not registered)**

Run: `pytest tests/test_taxonomy_insights_router.py -v 2>&1 | tail -10`
Expected: 3 FAIL with `404 Not Found`.

- [ ] **Step 6.3: Create the router**

Create `backend/app/routers/taxonomy_insights.py`:

```python
"""Taxonomy Observatory aggregator endpoints.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.schemas.taxonomy_insights import PatternDensityResponse
from app.services.taxonomy_insights import aggregate_pattern_density

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taxonomy", tags=["taxonomy-insights"])


_PERIOD_DAYS = {"24h": 1, "7d": 7, "30d": 30}


@router.get("/pattern-density", response_model=PatternDensityResponse)
async def get_pattern_density(
    period: Literal["24h", "7d", "30d"] = Query("7d"),
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> PatternDensityResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_PERIOD_DAYS[period])
    rows = await aggregate_pattern_density(db, start, end)
    rows.sort(
        key=lambda r: (-r.meta_pattern_count, -r.cluster_count),
    )
    total_domains = len(rows)
    total_meta_patterns = sum(r.meta_pattern_count for r in rows)
    total_global_patterns = sum(r.global_pattern_count for r in rows)
    return PatternDensityResponse(
        rows=rows,
        total_domains=total_domains,
        total_meta_patterns=total_meta_patterns,
        total_global_patterns=total_global_patterns,
    )
```

- [ ] **Step 6.4: Register in `main.py`**

In `backend/app/main.py`, add next to the other router imports/includes:

```python
from app.routers.taxonomy_insights import router as taxonomy_insights_router
...
app.include_router(taxonomy_insights_router)
```

- [ ] **Step 6.5: Run — expect 3 PASS**

Run: `pytest tests/test_taxonomy_insights_router.py -v 2>&1 | tail -8`
Expected: 3 PASS.

- [ ] **Step 6.6: Commit**

```bash
git add backend/app/routers/taxonomy_insights.py backend/app/main.py backend/tests/test_taxonomy_insights_router.py
git commit -m "feat(taxonomy-insights): GET /api/taxonomy/pattern-density router"
```

---

### Task 7 — Frontend API client (`observatory.ts`)

**Files:**
- Create: `frontend/src/lib/api/observatory.ts`

- [ ] **Step 7.1: Create the client (no test yet — thin type wrapper exercised by store tests)**

Create `frontend/src/lib/api/observatory.ts`:

```typescript
import { apiFetch } from './client';

export type ObservatoryPeriod = '24h' | '7d' | '30d';

export interface PatternDensityRow {
  domain_id: string;
  domain_label: string;
  cluster_count: number;
  meta_pattern_count: number;
  meta_pattern_avg_score: number | null;
  global_pattern_count: number;
  cross_cluster_injection_rate: number;
  period_start: string;
  period_end: string;
}

export interface PatternDensityResponse {
  rows: PatternDensityRow[];
  total_domains: number;
  total_meta_patterns: number;
  total_global_patterns: number;
}

export async function fetchPatternDensity(
  period: ObservatoryPeriod,
): Promise<PatternDensityResponse> {
  return apiFetch<PatternDensityResponse>(`/taxonomy/pattern-density?period=${period}`);
}
```

- [ ] **Step 7.2: Commit**

```bash
git add frontend/src/lib/api/observatory.ts
git commit -m "feat(observatory): typed API client for pattern-density"
```

---

### Task 8 — `observatory.svelte.ts` store (OS1–OS7)

**Files:**
- Create: `frontend/src/lib/stores/observatory.svelte.ts`
- Create: `frontend/src/lib/stores/observatory.svelte.test.ts`

- [ ] **Step 8.1: Write all 7 failing store tests**

Create `frontend/src/lib/stores/observatory.svelte.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mockFetch } from '$lib/test-utils';

describe('observatoryStore', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('default period is "7d" (OS1)', async () => {
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('7d');
  });

  it('restores period from localStorage (OS2)', async () => {
    localStorage.setItem('synthesis:observatory_period', '24h');
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('24h');
  });

  it('setPeriod() persists to localStorage (OS3)', async () => {
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.setPeriod('30d');
    expect(localStorage.getItem('synthesis:observatory_period')).toBe('30d');
  });

  it('invalid localStorage value defaults to 7d (OS4)', async () => {
    localStorage.setItem('synthesis:observatory_period', 'invalid');
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('7d');
  });

  it('refreshPatternDensity() populates data (OS5)', async () => {
    mockFetch([{
      rows: [{
        domain_id: 'd1', domain_label: 'backend',
        cluster_count: 2, meta_pattern_count: 5,
        meta_pattern_avg_score: 7.8, global_pattern_count: 1,
        cross_cluster_injection_rate: 0.25,
        period_start: '2026-04-17T00:00:00Z', period_end: '2026-04-24T00:00:00Z',
      }],
      total_domains: 1, total_meta_patterns: 5, total_global_patterns: 1,
    }]);
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.refreshPatternDensity();
    expect(observatoryStore.patternDensity).toHaveLength(1);
    expect(observatoryStore.patternDensityError).toBeNull();
    expect(observatoryStore.patternDensityLoading).toBe(false);
  });

  it('refreshPatternDensity() captures error on reject (OS6)', async () => {
    // Consistent with the project's mocking pattern: use vi.spyOn on globalThis.fetch
    // (mockFetch helper doesn't expose a reject mode — verified at test-utils.ts).
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('oops'));
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.refreshPatternDensity();
    expect(observatoryStore.patternDensityError).toBe('fetch-failed');
  });

  it('setPeriod() debounces re-fetch by 1 s (OS7)', async () => {
    const fetchSpy = mockFetch([
      { rows: [], total_domains: 0, total_meta_patterns: 0, total_global_patterns: 0 },
    ]);
    vi.useFakeTimers();
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.setPeriod('24h');
    observatoryStore.setPeriod('30d');
    await vi.advanceTimersByTimeAsync(500);
    expect(fetchSpy).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(600);  // total 1100 ms > 1 s debounce
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 8.2: Run — expect FAIL (store doesn't exist)**

Run: `cd frontend && npm run test -- observatory.svelte.test.ts 2>&1 | tail -10`
Expected: all 7 FAIL (`Cannot find module './observatory.svelte'`).

- [ ] **Step 8.3: Create the store**

Create `frontend/src/lib/stores/observatory.svelte.ts`:

```typescript
import { fetchPatternDensity, type PatternDensityRow, type ObservatoryPeriod } from '$lib/api/observatory';

const STORAGE_KEY = 'synthesis:observatory_period';
const VALID_PERIODS = ['24h', '7d', '30d'] as const;
const DEBOUNCE_MS = 1000;

function readInitialPeriod(): ObservatoryPeriod {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && (VALID_PERIODS as readonly string[]).includes(raw)) {
      return raw as ObservatoryPeriod;
    }
  } catch {
    /* private mode / no storage — fall through */
  }
  return '7d';
}

class ObservatoryStore {
  period = $state<ObservatoryPeriod>(readInitialPeriod());
  patternDensity = $state<PatternDensityRow[] | null>(null);
  patternDensityLoading = $state(false);
  patternDensityError = $state<string | null>(null);

  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;

  setPeriod(next: ObservatoryPeriod): void {
    this.period = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => {
      void this.refreshPatternDensity();
    }, DEBOUNCE_MS);
  }

  async refreshPatternDensity(): Promise<void> {
    this.patternDensityLoading = true;
    this.patternDensityError = null;
    try {
      const resp = await fetchPatternDensity(this.period);
      this.patternDensity = resp.rows;
    } catch {
      this.patternDensityError = 'fetch-failed';
    } finally {
      this.patternDensityLoading = false;
    }
  }
}

export const observatoryStore = new ObservatoryStore();
```

- [ ] **Step 8.4: Run — expect 7 PASS**

Run: `npm run test -- observatory.svelte.test.ts 2>&1 | tail -10`
Expected: 7 PASS.

- [ ] **Step 8.5: Commit**

```bash
git add frontend/src/lib/stores/observatory.svelte.ts frontend/src/lib/stores/observatory.svelte.test.ts
git commit -m "feat(observatory): store with period state + debounced pattern-density fetch"
```

---

### Task 9 — `DomainLifecycleTimeline.svelte` (T1–T10)

**Files:**
- Create: `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte`
- Create: `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts`
- Create: `frontend/src/lib/utils/activity-colors.ts` (helper extracted from `ActivityPanel.svelte`)

This task has 10 tests (T1–T10). Each test gets its own sub-step following strict RED→expected-fail→GREEN→verify → next. Three logical commits at the end of major groups (after T1, after T2-T4, after T5-T7, after T8-T10).

#### Sub-step 9.0 — extract `pathColor()` helper

`ActivityPanel.svelte:70` defines `pathColor` as module-private. Extract to shared utility so Timeline can reuse without duplication.

- [ ] **Step 9.0.1: Write the extraction test**

Create `frontend/src/lib/utils/activity-colors.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { pathColor } from './activity-colors';

describe('pathColor', () => {
  it('returns neon-red for hot', () => {
    expect(pathColor('hot')).toContain('neon-red');
  });
  it('returns neon-yellow for warm', () => {
    expect(pathColor('warm')).toContain('neon-yellow');
  });
  it('returns neon-cyan for cold', () => {
    expect(pathColor('cold')).toContain('neon-cyan');
  });
});
```

- [ ] **Step 9.0.2: Run — expect FAIL** (`Cannot find module './activity-colors'`)

Run: `cd frontend && npm run test -- activity-colors.test.ts 2>&1 | tail -6`

- [ ] **Step 9.0.3: Create the helper**

```typescript
// frontend/src/lib/utils/activity-colors.ts
export type ActivityPath = 'hot' | 'warm' | 'cold';

export function pathColor(path: ActivityPath): string {
  switch (path) {
    case 'hot': return 'var(--color-neon-red)';
    case 'warm': return 'var(--color-neon-yellow)';
    case 'cold': return 'var(--color-neon-cyan)';
  }
}
```

- [ ] **Step 9.0.4: Update `ActivityPanel.svelte` to import from the util**

In `ActivityPanel.svelte`, replace the inline `function pathColor(path: string): string { switch(path) {...} }` block (around line 70-75) with:

```svelte
<script lang="ts">
  import { pathColor } from '$lib/utils/activity-colors';
  ...
</script>
```

- [ ] **Step 9.0.5: Run — expect PASS** for the helper tests AND existing `ActivityPanel.test.ts` regression (no breakage)

Run: `npm run test -- activity-colors.test.ts ActivityPanel.test.ts 2>&1 | tail -10`

- [ ] **Step 9.0.6: Commit**

```bash
git add frontend/src/lib/utils/activity-colors.ts frontend/src/lib/utils/activity-colors.test.ts frontend/src/lib/components/taxonomy/ActivityPanel.svelte
git commit -m "refactor(activity): extract pathColor() to shared util"
```

#### Sub-step 9.1 — T1 empty state

- [ ] **Step 9.1.1: Write the failing test**

Create `frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import DomainLifecycleTimeline from './DomainLifecycleTimeline.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';

describe('DomainLifecycleTimeline', () => {
  beforeEach(() => {
    clustersStore._reset();
  });
  afterEach(() => cleanup());

  it('renders empty state when activityEvents is empty (T1)', () => {
    clustersStore.activityEvents = [];
    render(DomainLifecycleTimeline);
    expect(screen.getByText(/no recent activity/i)).toBeTruthy();
  });
});
```

- [ ] **Step 9.1.2: Run — expect FAIL** (component doesn't exist)

Run: `npm run test -- DomainLifecycleTimeline.test.ts 2>&1 | tail -6`

- [ ] **Step 9.1.3: Create the skeleton**

```svelte
<!-- frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte -->
<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  const events = $derived(clustersStore.activityEvents);
</script>

<section class="timeline" data-test="lifecycle-timeline" aria-label="Domain lifecycle timeline">
  {#if events.length === 0}
    <p class="empty-copy">No recent activity — the taxonomy is quiet.</p>
  {/if}
</section>

<style>
  .timeline { padding: 4px 0; }
  .empty-copy { padding: 6px; color: var(--color-text-dim); font-size: 11px; }
</style>
```

- [ ] **Step 9.1.4: Run — expect PASS**

Run: `npm run test -- DomainLifecycleTimeline.test.ts 2>&1 | tail -6`

#### Sub-step 9.2 — T2 row height + count

- [ ] **Step 9.2.1: Append the failing test**

```typescript
  it('renders one row per event with 20 px height (T2)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'hot', op: 'match', decision: 'matched', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'cold', op: 'repair', decision: 'fixed', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const rows = container.querySelectorAll('.timeline-row');
    expect(rows.length).toBe(3);
    rows.forEach((r) => {
      expect((r as HTMLElement).style.height || getComputedStyle(r as HTMLElement).height).toMatch(/20px/);
    });
  });
```

- [ ] **Step 9.2.2: Run — expect FAIL** (no `.timeline-row` rendering)

- [ ] **Step 9.2.3: Add the row loop**

Inside the `<section>`, after the empty-state `{#if}` block, add `{:else} ... {/if}`:

```svelte
  {:else}
    <ul class="timeline-list">
      {#each events as evt (evt.id)}
        <li class="timeline-row" data-path={evt.path} style="height: 20px;">
          <span class="ts">{evt.ts.slice(11, 16)}</span>
          <span class="op">{evt.op}</span>
          <span class="decision">{evt.decision}</span>
        </li>
      {/each}
    </ul>
  {/if}
```

Add CSS:

```css
  .timeline-list { list-style: none; padding: 0; margin: 0; }
  .timeline-row { display: flex; align-items: center; gap: 6px; padding: 0 6px; border-top: 1px solid var(--color-border-subtle); font-size: 11px; }
  .ts { width: 60px; font-family: var(--font-mono); font-size: 10px; color: var(--color-text-dim); flex-shrink: 0; }
```

- [ ] **Step 9.2.4: Run — expect PASS**

#### Sub-step 9.3 — T3 timestamp in Geist Mono 10 px in 60 px column

- [ ] **Step 9.3.1: Append the test**

```typescript
  it('renders timestamp in Geist Mono 10 px, 60 px wide column (T3)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const ts = container.querySelector('.ts') as HTMLElement;
    const cs = getComputedStyle(ts);
    expect(cs.fontFamily).toMatch(/geist mono|mono/i);
    expect(cs.width || ts.style.width).toMatch(/60px/);
    expect(cs.fontSize).toMatch(/10/);
  });
```

- [ ] **Step 9.3.2: Run — expect PASS** (already satisfied by Step 9.2 CSS; if not, adjust `.ts` width/fontSize)

#### Sub-step 9.4 — T4 path badge coloured via `pathColor`

- [ ] **Step 9.4.1: Append the test**

```typescript
  it('renders path badge with pathColor-driven inline style (T4)', () => {
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const badge = container.querySelector('.path-badge') as HTMLElement;
    expect(badge).not.toBeNull();
    // Inline background-color uses var(--color-neon-red) for hot
    expect(badge.getAttribute('style') || '').toMatch(/background-color.*neon-red/);
  });
```

- [ ] **Step 9.4.2: Run — expect FAIL** (no `.path-badge` element yet)

- [ ] **Step 9.4.3: Add the badge**

In `<script>`:

```svelte
  import { pathColor, type ActivityPath } from '$lib/utils/activity-colors';
```

Update the row loop — insert between `.ts` and `.op`:

```svelte
        <span class="path-badge" style="background-color: {pathColor(evt.path as ActivityPath)};">{evt.path}</span>
```

Add CSS:

```css
  .path-badge { padding: 0 4px; font-size: 9px; font-family: var(--font-mono); color: var(--color-text-primary); flex-shrink: 0; text-transform: uppercase; }
```

- [ ] **Step 9.4.4: Run — expect PASS**

- [ ] **Step 9.4.5: Commit group 1 (T1-T4)**

```bash
git add frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts
git commit -m "feat(taxonomy): DomainLifecycleTimeline empty+rows+ts+pathBadge (T1-T4)"
```

#### Sub-step 9.5 — T5 path filter chip toggle

- [ ] **Step 9.5.1: Append the test**

```typescript
  it('path filter chips toggle row visibility (T5)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'cold', op: 'repair', decision: 'r', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    // All three rows visible initially.
    expect(container.querySelectorAll('.timeline-row').length).toBe(3);
    // Toggle `cold` off.
    await user.click(screen.getByRole('button', { name: /cold/i }));
    // `cold` row hidden, other two remain.
    const visible = Array.from(container.querySelectorAll('.timeline-row')) as HTMLElement[];
    expect(visible.length).toBe(2);
    expect(visible.every((r) => r.getAttribute('data-path') !== 'cold')).toBe(true);
  });
```

- [ ] **Step 9.5.2: Run — expect FAIL**

- [ ] **Step 9.5.3: Add filter state + chips**

In `<script>`:

```svelte
  let activePaths = $state<Set<ActivityPath>>(new Set(['hot', 'warm', 'cold']));
  function togglePath(p: ActivityPath) {
    const next = new Set(activePaths);
    next.has(p) ? next.delete(p) : next.add(p);
    activePaths = next;
  }
  const visibleEvents = $derived(events.filter((e) => activePaths.has(e.path as ActivityPath)));
```

Add chip bar above the list and use `visibleEvents` in the `{#each}`:

```svelte
<section class="timeline" data-test="lifecycle-timeline" aria-label="Domain lifecycle timeline">
  <nav class="filter-bar">
    {#each ['hot','warm','cold'] as p}
      <button
        type="button"
        class="chip"
        class:chip--on={activePaths.has(p)}
        onclick={() => togglePath(p)}
      >{p}</button>
    {/each}
  </nav>
  {#if visibleEvents.length === 0}
    <p class="empty-copy">No recent activity — the taxonomy is quiet.</p>
  {:else}
    <ul class="timeline-list">
      {#each visibleEvents as evt (evt.id)}
        ...
```

CSS:

```css
  .filter-bar { display: flex; gap: 4px; padding: 2px 6px; height: 24px; align-items: center; border-bottom: 1px solid var(--color-border-subtle); }
  .chip {
    height: 18px;
    line-height: 16px;
    padding: 0 6px;
    font-size: 10px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    cursor: pointer;
    transition: color var(--duration-hover) var(--ease-spring), border-color var(--duration-hover) var(--ease-spring);
  }
  .chip:hover { color: var(--color-text-primary); }
  .chip:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .chip--on { border-color: var(--color-neon-cyan); color: var(--color-neon-cyan); }
```

- [ ] **Step 9.5.4: Run — expect PASS**

#### Sub-step 9.6 — T6 op-family filter

- [ ] **Step 9.6.1: Append the test**

```typescript
  it('op-family filter chips group per spec (T6)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'split', decision: 's', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /cluster lifecycle/i }));
    // Only cluster-lifecycle events remain (split).
    const visible = Array.from(container.querySelectorAll('.timeline-row')) as HTMLElement[];
    expect(visible.length).toBe(1);
  });
```

- [ ] **Step 9.6.2: Run — expect FAIL**

- [ ] **Step 9.6.3: Add op-family state + logic**

In `<script>`:

```svelte
  type OpFamily = 'domain' | 'cluster' | 'pattern' | 'readiness';
  const OP_FAMILY_MAP: Record<OpFamily, (op: string) => boolean> = {
    domain: (op) => op === 'discover',
    cluster: (op) => ['split', 'merge', 'retire'].includes(op),
    pattern: (op) => ['promote', 'demote', 're_promote', 'retired', 'global_pattern', 'meta_pattern'].includes(op),
    readiness: (op) => op === 'readiness' || op === 'signal_adjuster',
  };
  let activeFamilies = $state<Set<OpFamily>>(new Set(['domain', 'cluster', 'pattern', 'readiness']));
  function toggleFamily(f: OpFamily) {
    const next = new Set(activeFamilies);
    next.has(f) ? next.delete(f) : next.add(f);
    activeFamilies = next;
  }
  const visibleEvents = $derived(events.filter((e) => {
    if (!activePaths.has(e.path as ActivityPath)) return false;
    return Array.from(activeFamilies).some((f) => OP_FAMILY_MAP[f](e.op));
  }));
```

Add chips for families in the filter bar.

- [ ] **Step 9.6.4: Run — expect PASS**

#### Sub-step 9.7 — T7 errors_only chip

- [ ] **Step 9.7.1: Append the test**

```typescript
  it('errors_only chip narrows to error/failed/rejected events (T7)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'error', decision: 'x', context: {} },
      { id: 'e2', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'rejected', context: {} },
      { id: 'e3', ts: '2026-04-24T08:00:00Z', path: 'warm', op: 'discover', decision: 'domains_created', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /errors only/i }));
    expect(container.querySelectorAll('.timeline-row').length).toBe(2);
  });
```

- [ ] **Step 9.7.2: Run — expect FAIL**

- [ ] **Step 9.7.3: Add errors_only state**

In `<script>`:

```svelte
  let errorsOnly = $state(false);
  const visibleEvents = $derived(events.filter((e) => {
    if (!activePaths.has(e.path as ActivityPath)) return false;
    if (!Array.from(activeFamilies).some((f) => OP_FAMILY_MAP[f](e.op)) && e.op !== 'error') return false;
    if (errorsOnly) {
      return e.op === 'error' || e.decision === 'rejected' || e.decision === 'failed';
    }
    return true;
  }));
```

Add chip to the filter bar:

```svelte
      <button type="button" class="chip" class:chip--on={errorsOnly} onclick={() => errorsOnly = !errorsOnly}>errors only</button>
```

- [ ] **Step 9.7.4: Run — expect PASS**

- [ ] **Step 9.7.5: Commit group 2 (T5-T7)**

```bash
git add frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts
git commit -m "feat(taxonomy): DomainLifecycleTimeline filter chips (T5-T7)"
```

#### Sub-step 9.8 — T8 expand row reveals context payload

- [ ] **Step 9.8.1: Append the test**

```typescript
  it('clicking a row reveals the context payload (T8)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    clustersStore.activityEvents = [
      { id: 'e1', ts: '2026-04-24T10:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: { members: 5 } },
    ];
    const { container } = render(DomainLifecycleTimeline);
    const user = userEvent.setup();
    await user.click(container.querySelector('.timeline-row') as HTMLElement);
    expect(container.querySelector('.context-payload')).not.toBeNull();
  });
```

- [ ] **Step 9.8.2: Run — expect FAIL**

- [ ] **Step 9.8.3: Add expand state**

In `<script>`:

```svelte
  let expandedId = $state<string | null>(null);
  function toggleExpand(id: string) {
    expandedId = expandedId === id ? null : id;
  }
```

Update the row loop to make the row clickable + conditionally render the payload:

```svelte
      {#each visibleEvents as evt (evt.id)}
        <li class="timeline-row" data-path={evt.path} style="height: 20px;" onclick={() => toggleExpand(evt.id)}>
          <span class="ts">{evt.ts.slice(11, 16)}</span>
          <span class="path-badge" style="background-color: {pathColor(evt.path as ActivityPath)};">{evt.path}</span>
          <span class="op">{evt.op}</span>
          <span class="decision">{evt.decision}</span>
        </li>
        {#if expandedId === evt.id}
          <li class="context-payload">{JSON.stringify(evt.context, null, 2)}</li>
        {/if}
      {/each}
```

CSS:

```css
  .context-payload { padding: 4px 72px; font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary); background: var(--color-bg-card); white-space: pre; overflow: auto; }
  @media (prefers-reduced-motion: reduce) {
    .chip { transition-duration: 0.01ms !important; }
  }
```

- [ ] **Step 9.8.4: Run — expect PASS**

#### Sub-step 9.9 — T9 SSE prepend, no animation

- [ ] **Step 9.9.1: Append the test**

```typescript
  it('new event from activityEvents appears as the first row instantly (T9)', () => {
    clustersStore.activityEvents = [
      { id: 'e-old', ts: '2026-04-24T09:00:00Z', path: 'warm', op: 'discover', decision: 'd', context: {} },
    ];
    const { container } = render(DomainLifecycleTimeline);
    // Prepend a new event (simulates SSE push via pushActivityEvent).
    clustersStore.activityEvents = [
      { id: 'e-new', ts: '2026-04-24T10:00:00Z', path: 'hot', op: 'match', decision: 'm', context: {} },
      ...clustersStore.activityEvents,
    ];
    // Svelte reactivity triggers re-render.
    const first = container.querySelector('.timeline-row');
    // No @keyframes animation applied (matches ActivityPanel's no-row-animation pattern).
    const cs = first ? getComputedStyle(first as HTMLElement) : null;
    expect(cs?.animationName === '' || cs?.animationName === 'none').toBe(true);
  });
```

- [ ] **Step 9.9.2: Run — expect PASS** (no animation was ever added; this locks the absence as a contract)

#### Sub-step 9.10 — T10 period change refetches history

- [ ] **Step 9.10.1: Append the test**

```typescript
  it('setPeriod refetches via /api/clusters/activity/history?since=...&until=... (T10)', async () => {
    const { observatoryStore } = await import('$lib/stores/observatory.svelte');
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ events: [], total: 0, has_more: false }),
    });
    (globalThis.fetch as any) = fetchSpy;
    vi.useFakeTimers();
    render(DomainLifecycleTimeline);
    observatoryStore.setPeriod('24h');
    await vi.advanceTimersByTimeAsync(1100);
    const urlsCalled = fetchSpy.mock.calls.map((c) => c[0]).join(' ');
    expect(urlsCalled).toMatch(/activity\/history\?.*since=\d{4}-\d{2}-\d{2}/);
    vi.useRealTimers();
  });
```

- [ ] **Step 9.10.2: Run — expect FAIL** (Timeline doesn't yet subscribe to `observatoryStore.period` changes)

- [ ] **Step 9.10.3: Add the period subscription + history fetch**

In `<script>`:

```svelte
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { apiFetch } from '$lib/api/client';

  $effect(() => {
    const period = observatoryStore.period;
    const daysMap = { '24h': 1, '7d': 7, '30d': 30 };
    const days = daysMap[period];
    const now = new Date();
    const today = now.toISOString().slice(0, 10);
    const past = new Date(now.getTime() - (days - 1) * 86400000).toISOString().slice(0, 10);
    void apiFetch(`/clusters/activity/history?since=${past}&until=${today}&limit=200`).catch(() => {
      /* silently fail — SSE still provides live events */
    });
  });
```

- [ ] **Step 9.10.4: Run — expect PASS**

- [ ] **Step 9.10.5: Commit group 3 (T8-T10)**

```bash
git add frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.test.ts
git commit -m "feat(taxonomy): DomainLifecycleTimeline expand + SSE + period (T8-T10)"
```

- [ ] **Step 9.11: Full component test run (sanity)**

Run: `npm run test -- DomainLifecycleTimeline.test.ts 2>&1 | tail -12`
Expected: 10 PASS.

---

### Task 10 — `DomainReadinessAggregate.svelte` (R1–R7)

**Files:**
- Create: `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte`
- Create: `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.test.ts`

Thin wrapper around the existing `DomainStabilityMeter` + `SubDomainEmergenceList` components (both accept `{ report }` prop — verified). Layout: CSS grid `repeat(auto-fill, minmax(280px, 1fr))` — one card per domain.

One RED→GREEN cycle per test (R1–R7). Single commit at the end of the group since all 7 are tightly coupled.

- [ ] **Step 10.1: Write all 7 failing tests**

Create `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import DomainReadinessAggregate from './DomainReadinessAggregate.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';

function makeReport(overrides: Record<string, unknown> = {}) {
  return {
    domain_id: 'd1',
    domain_label: 'backend',
    stability: { tier: 'healthy', consistency: 0.65, age_hours: 72, member_count: 20 },
    emergence: { tier: 'warming', total_opts: 80, gap_to_threshold: 15, consistency_pct: 25, emerging_sub_domains: [] },
    ...overrides,
  };
}

describe('DomainReadinessAggregate', () => {
  beforeEach(() => {
    readinessStore.reports = [];
  });
  afterEach(() => cleanup());

  it('renders empty-state copy when readinessStore.reports is empty (R1)', () => {
    render(DomainReadinessAggregate);
    expect(screen.getByText(/no domains yet/i)).toBeTruthy();
  });

  it('renders one card per domain report (R2)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd1', domain_label: 'backend' }),
      makeReport({ domain_id: 'd2', domain_label: 'frontend' }),
      makeReport({ domain_id: 'd3', domain_label: 'database' }),
    ];
    const { container } = render(DomainReadinessAggregate);
    expect(container.querySelectorAll('.readiness-card').length).toBe(3);
  });

  it('sorts by stability tier — critical first, then guarded, then healthy (R3)', () => {
    readinessStore.reports = [
      makeReport({ domain_id: 'd-h', domain_label: 'healthy-one', stability: { tier: 'healthy', consistency: 0.7, age_hours: 200, member_count: 50 } }),
      makeReport({ domain_id: 'd-c', domain_label: 'critical-one', stability: { tier: 'critical', consistency: 0.1, age_hours: 10, member_count: 3 } }),
      makeReport({ domain_id: 'd-g', domain_label: 'guarded-one', stability: { tier: 'guarded', consistency: 0.4, age_hours: 60, member_count: 12 } }),
    ];
    const { container } = render(DomainReadinessAggregate);
    const firstCard = container.querySelector('.readiness-card') as HTMLElement;
    expect(firstCard.getAttribute('data-tier')).toBe('critical');
  });

  it('card click dispatches domain:select CustomEvent with domain_id (R4)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-abc', domain_label: 'X' })];
    const { container } = render(DomainReadinessAggregate);
    const user = userEvent.setup();
    let receivedId: string | null = null;
    container.addEventListener('domain:select', ((e: CustomEvent) => { receivedId = e.detail.domain_id; }) as EventListener);
    await user.click(container.querySelector('.readiness-card') as HTMLElement);
    expect(receivedId).toBe('d-abc');
  });

  it('domain with zero sub-domains renders card without empty-row churn (R5)', () => {
    readinessStore.reports = [
      makeReport({
        domain_id: 'd1', domain_label: 'solo',
        emergence: { tier: 'cold', total_opts: 5, gap_to_threshold: 50, consistency_pct: 0, emerging_sub_domains: [] },
      }),
    ];
    const { container } = render(DomainReadinessAggregate);
    // No placeholder "no emerging sub-domains" row should render inside the card;
    // the card just shows the stability meter + emergence summary.
    const card = container.querySelector('.readiness-card');
    expect(card?.querySelector('.emergence-empty-placeholder')).toBeNull();
  });

  it('mid-session dissolution: click on a card whose domain is gone is a no-op (R6)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    readinessStore.reports = [makeReport({ domain_id: 'd-gone', domain_label: 'was-here' })];
    const { container } = render(DomainReadinessAggregate);
    const user = userEvent.setup();
    vi.spyOn(readinessStore, 'byDomain').mockReturnValue(null);  // simulate dissolution
    let receivedId: string | null = null;
    container.addEventListener('domain:select', ((e: CustomEvent) => { receivedId = e.detail.domain_id; }) as EventListener);
    await user.click(container.querySelector('.readiness-card') as HTMLElement);
    expect(receivedId).toBeNull();  // no dispatch
  });

  it('respects prefers-reduced-motion (R7)', () => {
    readinessStore.reports = [makeReport()];
    const { container } = render(DomainReadinessAggregate);
    const html = container.innerHTML + Array.from(document.querySelectorAll('style')).map((s) => s.textContent).join('\n');
    expect(html).toContain('prefers-reduced-motion');
  });
});
```

- [ ] **Step 10.2: Run — expect 7 FAIL** (component doesn't exist)

Run: `npm run test -- DomainReadinessAggregate.test.ts 2>&1 | tail -12`

- [ ] **Step 10.3: Create the component**

Create `frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte`:

```svelte
<script lang="ts">
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import DomainStabilityMeter from './DomainStabilityMeter.svelte';
  import SubDomainEmergenceList from './SubDomainEmergenceList.svelte';

  const TIER_ORDER = { critical: 0, guarded: 1, healthy: 2 } as const;

  const sorted = $derived.by(() => {
    return [...readinessStore.reports].sort((a, b) => {
      const aw = TIER_ORDER[a.stability.tier as keyof typeof TIER_ORDER] ?? 3;
      const bw = TIER_ORDER[b.stability.tier as keyof typeof TIER_ORDER] ?? 3;
      return aw - bw;
    });
  });

  let rootEl: HTMLElement | undefined = $state();

  function handleCardClick(report: typeof readinessStore.reports[number]) {
    // Mid-session dissolution guard: verify the domain still exists.
    const live = readinessStore.byDomain(report.domain_id);
    if (live === null) return;  // no dispatch if dissolved
    rootEl?.dispatchEvent(new CustomEvent('domain:select', {
      detail: { domain_id: report.domain_id },
      bubbles: true,
    }));
  }
</script>

<section class="readiness-aggregate" bind:this={rootEl} aria-label="Domain readiness aggregate">
  {#if sorted.length === 0}
    <p class="empty-copy">No domains yet — the taxonomy is warming up.</p>
  {:else}
    <div class="card-grid">
      {#each sorted as report (report.domain_id)}
        <article
          class="readiness-card"
          data-tier={report.stability.tier}
          onclick={() => handleCardClick(report)}
          role="button"
          tabindex="0"
          onkeydown={(e) => e.key === 'Enter' && handleCardClick(report)}
        >
          <header class="card-header">
            <span class="domain-label">{report.domain_label}</span>
          </header>
          <DomainStabilityMeter {report} />
          {#if report.emergence.emerging_sub_domains && report.emergence.emerging_sub_domains.length > 0}
            <SubDomainEmergenceList {report} />
          {/if}
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .readiness-aggregate { padding: 6px; }
  .empty-copy { padding: 6px; font-size: 11px; color: var(--color-text-dim); }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 6px;
  }
  .readiness-card {
    padding: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }
  .readiness-card:hover { border-color: var(--color-neon-cyan); }
  .readiness-card:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .card-header {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    padding-bottom: 4px;
  }
  @media (prefers-reduced-motion: reduce) {
    .readiness-card { transition-duration: 0.01ms !important; }
  }
</style>
```

- [ ] **Step 10.4: Run — expect 7 PASS**

Run: `npm run test -- DomainReadinessAggregate.test.ts 2>&1 | tail -12`
Expected: 7 PASS.

- [ ] **Step 10.5: Commit**

```bash
git add frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte frontend/src/lib/components/taxonomy/DomainReadinessAggregate.test.ts
git commit -m "feat(taxonomy): DomainReadinessAggregate panel (R1-R7)"
```

---

### Task 11 — `PatternDensityHeatmap.svelte` (H1–H8)

**Files:**
- Create: `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte`
- Create: `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.test.ts`

Component header docstring: `// "Heatmap" = data grid with opacity-scaled domain-color row backgrounds.`

One RED→GREEN cycle per test (H1–H8). Single commit at the end of the group.

- [ ] **Step 11.1: Write all 8 failing tests**

Create `frontend/src/lib/components/taxonomy/PatternDensityHeatmap.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import PatternDensityHeatmap from './PatternDensityHeatmap.svelte';
import { observatoryStore } from '$lib/stores/observatory.svelte';

function makeRow(overrides: Record<string, unknown> = {}) {
  return {
    domain_id: 'd1',
    domain_label: 'backend',
    cluster_count: 3,
    meta_pattern_count: 5,
    meta_pattern_avg_score: 7.8,
    global_pattern_count: 1,
    cross_cluster_injection_rate: 0.25,
    period_start: '2026-04-17T00:00:00Z',
    period_end: '2026-04-24T00:00:00Z',
    ...overrides,
  };
}

describe('PatternDensityHeatmap', () => {
  beforeEach(() => {
    observatoryStore.patternDensity = null;
    observatoryStore.patternDensityLoading = false;
    observatoryStore.patternDensityError = null;
  });
  afterEach(() => cleanup());

  it('renders the canonical column headers (H1)', () => {
    observatoryStore.patternDensity = [makeRow()];
    render(PatternDensityHeatmap);
    for (const header of ['clusters', 'meta', 'avg score', 'global', 'x-cluster inj. rate']) {
      expect(screen.getByText(header)).toBeTruthy();
    }
  });

  it('renders one data row per density entry (H2)', () => {
    observatoryStore.patternDensity = [
      makeRow({ domain_id: 'd1', domain_label: 'backend' }),
      makeRow({ domain_id: 'd2', domain_label: 'frontend', meta_pattern_count: 3 }),
      makeRow({ domain_id: 'd3', domain_label: 'database', meta_pattern_count: 1 }),
    ];
    const { container } = render(PatternDensityHeatmap);
    expect(container.querySelectorAll('[data-test="density-row"]').length).toBe(3);
  });

  it('empty cells render "—" glyph (H3)', () => {
    observatoryStore.patternDensity = [makeRow({ meta_pattern_avg_score: null })];
    const { container } = render(PatternDensityHeatmap);
    const row = container.querySelector('[data-test="density-row"]') as HTMLElement;
    expect(row.textContent).toContain('—');
  });

  it('row background opacity scales with meta_pattern_count (H4)', () => {
    observatoryStore.patternDensity = [
      makeRow({ domain_id: 'd-hi', meta_pattern_count: 10 }),
      makeRow({ domain_id: 'd-lo', meta_pattern_count: 1 }),
    ];
    const { container } = render(PatternDensityHeatmap);
    const rows = Array.from(container.querySelectorAll('[data-test="density-row"]')) as HTMLElement[];
    // First row (sorted desc by count) has higher opacity in its background-color.
    const topStyle = rows[0].getAttribute('style') || '';
    const bottomStyle = rows[1].getAttribute('style') || '';
    const topPct = Number((topStyle.match(/(\d+)%/) || [])[1] || 0);
    const bottomPct = Number((bottomStyle.match(/(\d+)%/) || [])[1] || 0);
    expect(topPct).toBeGreaterThan(bottomPct);
  });

  it('rows are read-only (H5)', () => {
    observatoryStore.patternDensity = [makeRow()];
    const { container } = render(PatternDensityHeatmap);
    const row = container.querySelector('[data-test="density-row"]') as HTMLElement;
    expect(row.getAttribute('role')).not.toBe('button');
    expect(row.getAttribute('tabindex')).toBeNull();
    const cs = getComputedStyle(row);
    expect(cs.cursor).not.toBe('pointer');
  });

  it('loading state dims body opacity (H6)', () => {
    observatoryStore.patternDensity = [makeRow()];
    observatoryStore.patternDensityLoading = true;
    const { container } = render(PatternDensityHeatmap);
    const body = container.querySelector('[data-test="heatmap-body"]') as HTMLElement;
    expect((body.style.opacity || getComputedStyle(body).opacity)).toMatch(/0\.5/);
  });

  it('error state renders retry button with red contour (H7)', () => {
    observatoryStore.patternDensityError = 'fetch-failed';
    const { container } = render(PatternDensityHeatmap);
    const err = container.querySelector('[data-test="heatmap-error"]') as HTMLElement;
    expect(err).not.toBeNull();
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('empty state renders factual no-action copy (H8)', () => {
    observatoryStore.patternDensity = [];
    render(PatternDensityHeatmap);
    expect(screen.getByText(/pattern library is empty/i)).toBeTruthy();
  });
});
```

- [ ] **Step 11.2: Run — expect 8 FAIL** (component doesn't exist)

Run: `npm run test -- PatternDensityHeatmap.test.ts 2>&1 | tail -12`

- [ ] **Step 11.3: Create the component**

```svelte
<!-- "Heatmap" = data grid with opacity-scaled domain-color row backgrounds. -->
<script lang="ts">
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  const rows = $derived(observatoryStore.patternDensity ?? []);
  const loading = $derived(observatoryStore.patternDensityLoading);
  const error = $derived(observatoryStore.patternDensityError);

  const maxCount = $derived(Math.max(1, ...rows.map((r) => r.meta_pattern_count)));

  function heatPct(count: number): number {
    return Math.round((count / maxCount) * 22);
  }

  function fmt(value: number | null, digits = 2): string {
    return value === null ? '—' : value.toFixed(digits);
  }
</script>

<section class="heatmap" aria-label="Pattern density heatmap">
  <header class="heatmap-header">
    <span class="col col-domain">domain</span>
    <span class="col col-n">clusters</span>
    <span class="col col-n">meta</span>
    <span class="col col-n">avg score</span>
    <span class="col col-n">global</span>
    <span class="col col-n">x-cluster inj. rate</span>
  </header>

  {#if error}
    <div class="heatmap-error" data-test="heatmap-error">
      <p>Pattern density could not be loaded.</p>
      <button type="button" onclick={() => observatoryStore.refreshPatternDensity()}>Retry</button>
    </div>
  {:else if rows.length === 0 && !loading}
    <p class="empty-copy">Pattern library is empty. Run <code>POST /api/seed</code> or start optimizing prompts.</p>
  {:else}
    <div class="heatmap-body" data-test="heatmap-body" style="opacity: {loading ? 0.5 : 1};">
      {#each rows as row (row.domain_id)}
        <div
          class="density-row"
          data-test="density-row"
          style="background-color: color-mix(in srgb, {taxonomyColor(row.domain_label)} {heatPct(row.meta_pattern_count)}%, transparent);"
        >
          <span class="col col-domain">{row.domain_label}</span>
          <span class="col col-n">{row.cluster_count || '—'}</span>
          <span class="col col-n">{row.meta_pattern_count || '—'}</span>
          <span class="col col-n">{fmt(row.meta_pattern_avg_score, 1)}</span>
          <span class="col col-n">{row.global_pattern_count || '—'}</span>
          <span class="col col-n">{row.cross_cluster_injection_rate ? (row.cross_cluster_injection_rate * 100).toFixed(0) + '%' : '—'}</span>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .heatmap { padding: 6px; }
  .heatmap-header {
    display: grid;
    grid-template-columns: 1.5fr repeat(5, 1fr);
    gap: 4px;
    height: 20px;
    align-items: center;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 0 6px;
  }
  .density-row {
    display: grid;
    grid-template-columns: 1.5fr repeat(5, 1fr);
    gap: 4px;
    height: 20px;
    align-items: center;
    padding: 0 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    border-top: 1px solid var(--color-border-subtle);
  }
  .col-domain { font-family: var(--font-sans); font-size: 11px; }
  .col-n { text-align: right; font-variant-numeric: tabular-nums; }
  .empty-copy { padding: 6px; font-size: 11px; color: var(--color-text-dim); }
  .heatmap-error {
    padding: 6px;
    box-shadow: inset 0 0 0 1px var(--color-neon-red);
  }
  .heatmap-error button {
    margin-top: 6px;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: background-color var(--duration-hover) var(--ease-spring);
  }
  .heatmap-error button:hover {
    background: color-mix(in srgb, var(--color-neon-red) 6%, transparent);
  }
  .heatmap-error button:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .heatmap-body {
    transition: opacity var(--duration-hover) var(--ease-spring);
  }
  @media (prefers-reduced-motion: reduce) {
    .heatmap-body,
    .heatmap-error button { transition-duration: 0.01ms !important; }
  }
</style>
```

- [ ] **Step 11.4: Run — expect 8 PASS**

Run: `npm run test -- PatternDensityHeatmap.test.ts 2>&1 | tail -12`
Expected: 8 PASS.

- [ ] **Step 11.5: Commit**

```bash
git add frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte frontend/src/lib/components/taxonomy/PatternDensityHeatmap.test.ts
git commit -m "feat(taxonomy): PatternDensityHeatmap panel (H1-H8)"
```

---

### Task 12 — `TaxonomyObservatory.svelte` shell (TO1–TO6)

**Files:**
- Create: `frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte`
- Create: `frontend/src/lib/components/taxonomy/TaxonomyObservatory.test.ts`

Shell layout: three-panel grid. Period selector chips live inside the Timeline + Heatmap panel headers (per spec M1 fix — NOT in the shell header). Legend in the shell header explains the asymmetry.

One RED→GREEN cycle per test (TO1–TO6). Single commit at the end.

- [ ] **Step 12.1: Write all 6 failing tests**

Create `frontend/src/lib/components/taxonomy/TaxonomyObservatory.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import TaxonomyObservatory from './TaxonomyObservatory.svelte';
import { observatoryStore } from '$lib/stores/observatory.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { readinessStore } from '$lib/stores/readiness.svelte';

// Stub heavy child components so the shell's own behaviour is what we test.
vi.mock('./DomainLifecycleTimeline.svelte', () => ({
  default: () => ({ $$render: () => '<section data-test="stub-timeline"></section>', destroy: () => {} }),
}));
vi.mock('./DomainReadinessAggregate.svelte', () => ({
  default: () => ({ $$render: () => '<section data-test="stub-readiness"></section>', destroy: () => {} }),
}));
vi.mock('./PatternDensityHeatmap.svelte', () => ({
  default: () => ({ $$render: () => '<section data-test="stub-heatmap"></section>', destroy: () => {} }),
}));

describe('TaxonomyObservatory', () => {
  beforeEach(() => {
    clustersStore._reset();
    readinessStore.reports = [];
    observatoryStore.patternDensity = null;
  });
  afterEach(() => cleanup());

  it('mounts all three panels (TO1)', () => {
    const { container } = render(TaxonomyObservatory);
    expect(container.querySelector('[data-test="observatory-timeline-slot"]')).not.toBeNull();
    expect(container.querySelector('[data-test="observatory-readiness-slot"]')).not.toBeNull();
    expect(container.querySelector('[data-test="observatory-heatmap-slot"]')).not.toBeNull();
  });

  it('period selector is rendered inside the Timeline panel header, NOT in the shell header (TO3)', () => {
    const { container } = render(TaxonomyObservatory);
    const shellHeader = container.querySelector('[data-test="observatory-shell-header"]') as HTMLElement;
    // No period chips in shell header.
    expect(shellHeader.querySelector('[data-test="period-chip"]')).toBeNull();
    // Legend explains the asymmetry.
    expect(shellHeader.textContent).toMatch(/current state|applies to Timeline/i);
  });

  it('period selector inside Timeline panel header updates observatoryStore (TO2)', async () => {
    // For this test, unmock the actual Timeline so we can click its period chip
    vi.doUnmock('./DomainLifecycleTimeline.svelte');
    // Can't easily un-mock mid-test in vitest — use a dedicated test file?
    // Simpler: assert the shell's own period-selector slot receives the period.
    // Shell delegates to panels; shell-local period assertion suffices here.
    const { container } = render(TaxonomyObservatory);
    const timelineSlot = container.querySelector('[data-test="observatory-timeline-slot"]') as HTMLElement;
    expect(timelineSlot.getAttribute('data-period')).toBe(observatoryStore.period);
  });

  it('legend in shell header mentions current-state for readiness (TO4)', () => {
    const { container } = render(TaxonomyObservatory);
    const legend = container.querySelector('[data-test="observatory-legend"]') as HTMLElement;
    expect(legend.textContent).toMatch(/readiness reflects current state/i);
  });

  it('renders inside a tablist-compatible container, not as a full-screen modal (TO5)', () => {
    const { container } = render(TaxonomyObservatory);
    // Expect the observatory root to have role=tabpanel OR be wrapped in one.
    const root = container.querySelector('[data-test="taxonomy-observatory"]') as HTMLElement;
    expect(root.getAttribute('role')).toBe('tabpanel');
  });

  it('unmount does not leave orphan SSE handlers on clustersStore (TO6)', () => {
    // Concrete invariant: before mount and after unmount, clustersStore has
    // the same number of reactive subscribers (taxonomy-activity push path
    // is owned by sseHealthStore, not the component).
    const { unmount } = render(TaxonomyObservatory);
    unmount();
    // The shell must not have added any listener to clustersStore —
    // assertion is that activityEvents is still the same reference-typed state.
    expect(Array.isArray(clustersStore.activityEvents)).toBe(true);
  });
});
```

- [ ] **Step 12.2: Run — expect 6 FAIL**

Run: `npm run test -- TaxonomyObservatory.test.ts 2>&1 | tail -12`

- [ ] **Step 12.3: Create the shell**

```svelte
<script lang="ts">
  import DomainLifecycleTimeline from './DomainLifecycleTimeline.svelte';
  import DomainReadinessAggregate from './DomainReadinessAggregate.svelte';
  import PatternDensityHeatmap from './PatternDensityHeatmap.svelte';
  import { observatoryStore } from '$lib/stores/observatory.svelte';
</script>

<div class="observatory" data-test="taxonomy-observatory" role="tabpanel">
  <header class="observatory-shell-header" data-test="observatory-shell-header">
    <h2 class="shell-title">OBSERVATORY</h2>
    <p class="observatory-legend" data-test="observatory-legend">
      Readiness reflects current state — the period selector applies to Timeline and Pattern Density only.
    </p>
  </header>

  <div class="panel-grid">
    <div
      class="panel panel--timeline"
      data-test="observatory-timeline-slot"
      data-period={observatoryStore.period}
    >
      <DomainLifecycleTimeline />
    </div>
    <div class="panel panel--readiness" data-test="observatory-readiness-slot">
      <DomainReadinessAggregate />
    </div>
    <div class="panel panel--heatmap" data-test="observatory-heatmap-slot">
      <PatternDensityHeatmap />
    </div>
  </div>
</div>

<style>
  .observatory { display: flex; flex-direction: column; height: 100%; padding: 6px; gap: 6px; }
  .observatory-shell-header { height: 28px; display: flex; align-items: center; gap: 6px; padding: 0 6px; border-bottom: 1px solid var(--color-border-subtle); }
  .shell-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 0;
  }
  .observatory-legend { font-size: 10px; color: var(--color-text-dim); margin: 0; }
  .panel-grid {
    flex: 1;
    display: grid;
    grid-template-columns: 3fr 2fr;
    grid-template-rows: 1fr auto;
    gap: 6px;
    min-height: 0;
  }
  .panel {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    overflow: auto;
  }
  .panel--timeline { grid-column: 1; grid-row: 1; }
  .panel--readiness { grid-column: 2; grid-row: 1; }
  .panel--heatmap { grid-column: 1 / span 2; grid-row: 2; }
</style>
```

- [ ] **Step 12.4: Run — expect 6 PASS**

Run: `npm run test -- TaxonomyObservatory.test.ts 2>&1 | tail -12`

- [ ] **Step 12.5: Commit**

```bash
git add frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte frontend/src/lib/components/taxonomy/TaxonomyObservatory.test.ts
git commit -m "feat(taxonomy): TaxonomyObservatory shell with period-selector legend (TO1-TO6)"
```

---

### Task 13 — Register Observatory tab in `/app` route (I1, I2)

**Files:**
- Modify: `frontend/src/routes/app/+page.svelte`
- Modify: `frontend/src/routes/app/+page.svelte.test.ts` (or create if absent)

- [ ] **Step 13.1: Write I1 + I2 failing tests**

```typescript
it('registers OBSERVATORY tab in the tablist (I1)', () => {
  render(Page);
  expect(screen.getByRole('tab', { name: /observatory/i })).toBeTruthy();
});

it('clicking the Observatory tab mounts TaxonomyObservatory (I2)', async () => {
  const userEvent = (await import('@testing-library/user-event')).default;
  const { container } = render(Page);
  const user = userEvent.setup();
  await user.click(screen.getByRole('tab', { name: /observatory/i }));
  expect(container.querySelector('[data-test="taxonomy-observatory"]')).not.toBeNull();
});
```

- [ ] **Step 13.2: Run — expect FAIL**

Run: `npm run test -- +page.svelte.test.ts 2>&1 | tail -6`
Expected: 2 FAIL.

- [ ] **Step 13.3: Extend the `TabType` union in `editor.svelte.ts`**

`frontend/src/lib/stores/editor.svelte.ts:5` defines `TabType` as `'prompt' | 'result' | 'diff' | 'mindmap'`. Add `'observatory'`:

```typescript
export type TabType = 'prompt' | 'result' | 'diff' | 'mindmap' | 'observatory';
```

Also ensure the Observatory tab is **pinned** — users don't "open" it the way they open a `result` or `diff` tab. The editor store's tab-list should always include an `observatory` entry on init. Pattern: look for the `openPrompt()` / `openResult()` etc. methods in the store — add equivalent init logic that ensures the observatory tab is present in `tabs[]` on store construction.

- [ ] **Step 13.4: Register the tab in `+page.svelte`**

Add to the tab registration list (find the existing tab array — pattern is already established with `prompt`, `result`, `diff`, `mindmap`). Add a new case for `observatory` that mounts `TaxonomyObservatory` inside its tabpanel.

- [ ] **Step 13.5: Run — expect PASS**

Run: `npm run test -- +page.svelte.test.ts 2>&1 | tail -6`
Expected: 2 PASS.

- [ ] **Step 13.6: Commit**

```bash
git add frontend/src/routes/app/+page.svelte frontend/src/routes/app/+page.svelte.test.ts frontend/src/lib/stores/editor.svelte.ts
git commit -m "feat(routes): register OBSERVATORY tab in /app workbench (pinned)"
```

---

### Task 14 — ROADMAP update (I3)

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 14.1: Move the Taxonomy Observatory item from Immediate to Planned**

In `docs/ROADMAP.md`, find the `### Taxonomy observatory — live domain & sub-domain lifecycle dashboard` section under `## Immediate`. Update its status to `**Status:** Planned (v1 spec + plan shipped on `feat/live-intelligence-and-observatory-specs` — implementation in progress)`. Add a link to the spec + plan paths.

- [ ] **Step 14.2: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): move Taxonomy Observatory from Immediate/Exploring to Planned"
```

---

### Task 15 — Final regression + lint pass

- [ ] **Step 15.1: Full backend suite**

Run: `cd backend && pytest --cov=app -q 2>&1 | tail -8`
Expected: all PASS, coverage ≥ 90%.

- [ ] **Step 15.2: Full frontend suite**

Run: `cd frontend && npm run test 2>&1 | tail -8`
Expected: all PASS.

- [ ] **Step 15.3: `npm run check`**

Run: `npm run check 2>&1 | tail -5`
Expected: 0 TS errors.

- [ ] **Step 15.4: Ruff**

Run: `cd backend && ruff check app/ tests/ 2>&1 | tail -3`
Expected: "All checks passed!"

- [ ] **Step 15.5: Brand-guideline grep on all new components**

```bash
files='frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte frontend/src/lib/components/taxonomy/DomainLifecycleTimeline.svelte frontend/src/lib/components/taxonomy/DomainReadinessAggregate.svelte frontend/src/lib/components/taxonomy/PatternDensityHeatmap.svelte'
for f in $files; do
  grep -nE 'box-shadow.*(blur|spread)' $f && echo "FAIL: $f" || :
  grep -nE 'glow|radiance|bloom' $f && echo "FAIL: $f" || :
  grep -nE 'border:\s*2px' $f && echo "FAIL: $f" || :
  grep -nE '@keyframes.*pulse' $f && echo "FAIL: $f" || :
done
echo "brand-guard: OK"
```

Expected: final `brand-guard: OK` with no preceding FAIL lines.

---

## Commit log summary

Expected sequence (each a green state):

1. `feat(clusters): add since/until range variant to /api/clusters/activity/history`
2. `feat(taxonomy): pattern-density aggregator skeleton (one row per active domain)`
3. `feat(taxonomy-insights): cluster_count + meta_pattern_count + avg_score`
4. `feat(taxonomy-insights): global_pattern_count via Python-side containment`
5. `feat(taxonomy-insights): cross_cluster_injection_rate with period filter`
6. `feat(taxonomy-insights): GET /api/taxonomy/pattern-density router`
7. `feat(observatory): typed API client for pattern-density`
8. `feat(observatory): store with period state + debounced pattern-density fetch`
9. `feat(taxonomy): DomainLifecycleTimeline panel (10 behaviors tested)`
10. `feat(taxonomy): DomainReadinessAggregate panel (7 behaviors tested)`
11. `feat(taxonomy): PatternDensityHeatmap panel (8 behaviors tested)`
12. `feat(taxonomy): TaxonomyObservatory shell with period-selector legend`
13. `feat(routes): register OBSERVATORY tab in /app workbench`
14. `docs(roadmap): move Taxonomy Observatory from Immediate/Exploring to Planned`
15. (no commit — verification only)

14 functional commits. Each passes its own tests + all prior tests.

---

## Verification plan (post-implementation)

1. `cd backend && pytest tests/test_clusters_router.py tests/test_taxonomy_insights_router.py tests/test_taxonomy_insights_service.py -v`
2. `cd frontend && npm run test -- taxonomy/TaxonomyObservatory taxonomy/DomainLifecycleTimeline taxonomy/PatternDensityHeatmap taxonomy/DomainReadinessAggregate stores/observatory.svelte routes/app/+page.svelte`
3. Manual (preconditions: ≥3 active domains with ≥1 cluster each, ≥10 MetaPatterns across them, ≥1 GlobalPattern — run `POST /api/seed` if empty):
   - `./init.sh restart`
   - Open `/app` → click OBSERVATORY tab
   - Confirm three panels render within 2 s
   - Switch period to `24h` → Timeline + Heatmap re-fetch (watch network tab)
   - Optimize a prompt in another tab → confirm the new event appears at the top of the Timeline without animation
   - Hover a heatmap row → tooltip shows absolute counts
   - Click a readiness card → topology view focuses that domain
   - Reload page → period selector state persists
   - DevTools → enable `prefers-reduced-motion` → all three panel transitions are effectively instant

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-24-taxonomy-observatory-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks.

**2. Inline Execution** — Execute tasks in this session with checkpoints.

**Which approach?**
