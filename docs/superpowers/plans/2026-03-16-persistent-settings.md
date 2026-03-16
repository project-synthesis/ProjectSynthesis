# Persistent Settings System Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent user preferences (model selection, pipeline phase toggles, default strategy) with full-stack integration from JSON file through REST API to expanded Settings UI.

**Architecture:** File-based preferences (`data/preferences.json`) with atomic writes. Backend PreferencesService with snapshot pattern (load once per pipeline run). Frontend reactive store initialized at app root. Pipeline reads frozen snapshot for model selection and phase skipping.

**Tech Stack:** Python/FastAPI (backend service + router), Svelte 5 runes (store + UI), JSON file persistence, Pydantic validation.

**Spec:** `docs/superpowers/specs/2026-03-16-persistent-settings-system.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/app/services/preferences.py` | Create | PreferencesService — load/save/patch/resolve_model with snapshot pattern |
| `backend/tests/test_preferences.py` | Create | Unit tests for PreferencesService |
| `backend/app/routers/preferences.py` | Create | GET/PATCH /api/preferences endpoints |
| `backend/app/schemas/pipeline_contracts.py` | Modify | Make PipelineResult score fields Optional |
| `backend/app/main.py` | Modify | Register preferences router |
| `backend/app/services/pipeline.py` | Modify | Snapshot load, model selection, phase skipping, model_used tracking |
| `backend/app/services/refinement_service.py` | Modify | Snapshot load, model selection at 4 callsites |
| `backend/app/mcp_server.py` | Modify | Model selection in synthesis_analyze (3 refs) |
| `backend/app/routers/optimize.py` | Modify | Default strategy from preferences |
| `frontend/src/lib/api/client.ts` | Modify | Add getPreferences/patchPreferences |
| `frontend/src/lib/stores/preferences.svelte.ts` | Create | Reactive preferences store |
| `frontend/src/routes/+layout.svelte` | Modify | Init preferences store on app load |
| `frontend/src/lib/components/layout/Navigator.svelte` | Modify | Expanded settings panel + GitHub consolidation |
| `CLAUDE.md` | Modify | Document preferences system |

---

## Chunk 1: Backend Core

### Task 1: PreferencesService

**Files:**
- Create: `backend/app/services/preferences.py`
- Test: `backend/tests/test_preferences.py`

- [ ] **Step 1: Write the test file**

```python
# backend/tests/test_preferences.py
"""Tests for PreferencesService."""

import json
import os
from pathlib import Path

import pytest

from app.services.preferences import PreferencesService


@pytest.fixture
def prefs_dir(tmp_path):
    return tmp_path


@pytest.fixture
def svc(prefs_dir):
    return PreferencesService(prefs_dir)


class TestLoad:
    def test_load_returns_defaults_when_no_file(self, svc):
        result = svc.load()
        assert result["schema_version"] == 1
        assert result["models"]["analyzer"] == "sonnet"
        assert result["models"]["optimizer"] == "opus"
        assert result["models"]["scorer"] == "sonnet"
        assert result["pipeline"]["enable_explore"] is True
        assert result["pipeline"]["enable_scoring"] is True
        assert result["pipeline"]["enable_adaptation"] is True
        assert result["defaults"]["strategy"] == "auto"

    def test_load_creates_file_on_first_access(self, svc, prefs_dir):
        svc.load()
        assert (prefs_dir / "preferences.json").exists()

    def test_load_merges_missing_keys_with_defaults(self, svc, prefs_dir):
        (prefs_dir / "preferences.json").write_text('{"models": {"analyzer": "haiku"}}')
        result = svc.load()
        assert result["models"]["analyzer"] == "haiku"
        assert result["models"]["optimizer"] == "opus"  # default
        assert result["pipeline"]["enable_scoring"] is True  # default

    def test_load_recovers_from_corrupt_json(self, svc, prefs_dir):
        (prefs_dir / "preferences.json").write_text("NOT JSON {{{")
        result = svc.load()
        assert result["models"]["optimizer"] == "opus"  # full defaults

    def test_load_replaces_invalid_model_with_default(self, svc, prefs_dir):
        (prefs_dir / "preferences.json").write_text('{"models": {"analyzer": "gpt-4"}}')
        result = svc.load()
        assert result["models"]["analyzer"] == "sonnet"  # replaced


class TestSave:
    def test_save_writes_valid_json(self, svc, prefs_dir):
        prefs = svc.load()
        prefs["models"]["analyzer"] = "haiku"
        svc.save(prefs)
        raw = json.loads((prefs_dir / "preferences.json").read_text())
        assert raw["models"]["analyzer"] == "haiku"

    def test_save_rejects_invalid_model(self, svc):
        prefs = svc.load()
        prefs["models"]["analyzer"] = "gpt-4"
        with pytest.raises(ValueError, match="Invalid model"):
            svc.save(prefs)


class TestPatch:
    def test_patch_deep_merges(self, svc):
        svc.patch({"models": {"analyzer": "haiku"}})
        result = svc.load()
        assert result["models"]["analyzer"] == "haiku"
        assert result["models"]["optimizer"] == "opus"  # untouched

    def test_patch_rejects_invalid_strategy(self, svc):
        with pytest.raises(ValueError, match="Invalid strategy"):
            svc.patch({"defaults": {"strategy": "nonexistent"}})


class TestResolveModel:
    def test_resolve_sonnet(self, svc):
        snapshot = svc.load()
        model_id = svc.resolve_model("analyzer", snapshot)
        assert "sonnet" in model_id

    def test_resolve_opus(self, svc):
        snapshot = svc.load()
        model_id = svc.resolve_model("optimizer", snapshot)
        assert "opus" in model_id

    def test_resolve_haiku(self, svc):
        svc.patch({"models": {"scorer": "haiku"}})
        snapshot = svc.load()
        model_id = svc.resolve_model("scorer", snapshot)
        assert "haiku" in model_id

    def test_resolve_without_snapshot_reads_file(self, svc):
        model_id = svc.resolve_model("analyzer")
        assert "sonnet" in model_id


class TestGet:
    def test_get_dot_path(self, svc):
        snapshot = svc.load()
        assert svc.get("models.analyzer", snapshot) == "sonnet"
        assert svc.get("pipeline.enable_scoring", snapshot) is True

    def test_get_returns_none_for_missing_path(self, svc):
        snapshot = svc.load()
        assert svc.get("nonexistent.path", snapshot) is None


class TestFileRecovery:
    def test_deleted_file_regenerates_defaults(self, svc, prefs_dir):
        svc.load()  # creates file
        os.remove(prefs_dir / "preferences.json")
        result = svc.load()  # should regenerate
        assert result["models"]["optimizer"] == "opus"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_preferences.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.preferences'`

- [ ] **Step 3: Implement PreferencesService**

```python
# backend/app/services/preferences.py
"""Persistent user preferences — file-based JSON with atomic writes.

Preferences persist across server restarts via data/preferences.json.
Pipeline callers use the snapshot pattern: load() once, pass frozen
dict to get() and resolve_model() throughout the pipeline run.
"""

from __future__ import annotations

import copy
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

VALID_MODELS = {"sonnet", "opus", "haiku"}
VALID_STRATEGIES = {
    "auto", "chain-of-thought", "few-shot",
    "meta-prompting", "role-playing", "structured-output",
}

MODEL_MAP = {
    "sonnet": lambda: settings.MODEL_SONNET,
    "opus": lambda: settings.MODEL_OPUS,
    "haiku": lambda: settings.MODEL_HAIKU,
}


class PreferencesService:
    """Load, save, and query persistent user preferences."""

    DEFAULTS: dict[str, Any] = {
        "schema_version": 1,
        "models": {"analyzer": "sonnet", "optimizer": "opus", "scorer": "sonnet"},
        "pipeline": {
            "enable_explore": True,
            "enable_scoring": True,
            "enable_adaptation": True,
        },
        "defaults": {"strategy": "auto"},
    }

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "preferences.json"
        self._data_dir = data_dir

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Read preferences from disk, deep-merge with defaults.

        Returns a fresh dict (snapshot) safe to pass around without
        re-reading the file. Creates the file with defaults if missing.
        """
        data: dict[str, Any] = {}
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Corrupt preferences.json — using defaults: %s", exc,
                )
                data = {}

        merged = self._deep_merge(copy.deepcopy(self.DEFAULTS), data)
        merged = self._sanitize(merged)

        # Persist the clean merged version (creates file if missing)
        self._write(merged)
        return merged

    def save(self, prefs: dict[str, Any]) -> None:
        """Validate and atomically write preferences to disk."""
        self._validate(prefs)
        self._write(prefs)

    def patch(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge updates into existing preferences, validate, save.

        Returns the full updated preferences dict.
        """
        current = self.load()
        merged = self._deep_merge(current, updates)
        self._validate(merged)
        self._write(merged)
        return merged

    def get(self, path: str, snapshot: dict[str, Any] | None = None) -> Any:
        """Dot-path accessor (e.g., 'models.analyzer', 'pipeline.enable_scoring').

        If snapshot is provided, reads from it. Otherwise reads from disk.
        """
        data = snapshot if snapshot is not None else self.load()
        keys = path.split(".")
        current: Any = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def resolve_model(self, phase: str, snapshot: dict[str, Any] | None = None) -> str:
        """Map a phase name to its full model ID string.

        Args:
            phase: One of 'analyzer', 'optimizer', 'scorer'.
            snapshot: Frozen preferences dict. If None, reads from disk.

        Returns:
            Full model ID string (e.g., 'claude-sonnet-4-6').
        """
        short_name = self.get(f"models.{phase}", snapshot) or "sonnet"
        resolver = MODEL_MAP.get(short_name)
        if resolver is None:
            logger.warning("Unknown model '%s' for phase '%s' — falling back to sonnet", short_name, phase)
            return settings.MODEL_SONNET
        return resolver()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Recursively merge override into base (base is mutated)."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                PreferencesService._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def _sanitize(self, prefs: dict[str, Any]) -> dict[str, Any]:
        """Replace invalid values with defaults, log each replacement."""
        models = prefs.get("models", {})
        for phase in ("analyzer", "optimizer", "scorer"):
            if models.get(phase) not in VALID_MODELS:
                default = self.DEFAULTS["models"][phase]
                logger.warning(
                    "Invalid model '%s' for %s — replacing with '%s'",
                    models.get(phase), phase, default,
                )
                models[phase] = default

        strategy = prefs.get("defaults", {}).get("strategy", "auto")
        if strategy not in VALID_STRATEGIES:
            logger.warning(
                "Invalid strategy '%s' — replacing with 'auto'", strategy,
            )
            prefs.setdefault("defaults", {})["strategy"] = "auto"

        pipeline = prefs.get("pipeline", {})
        for toggle in ("enable_explore", "enable_scoring", "enable_adaptation"):
            if not isinstance(pipeline.get(toggle), bool):
                pipeline[toggle] = self.DEFAULTS["pipeline"][toggle]

        return prefs

    def _validate(self, prefs: dict[str, Any]) -> None:
        """Raise ValueError if prefs contain invalid values."""
        models = prefs.get("models", {})
        for phase in ("analyzer", "optimizer", "scorer"):
            val = models.get(phase)
            if val and val not in VALID_MODELS:
                raise ValueError(
                    f"Invalid model '{val}' for {phase}. Must be one of: {VALID_MODELS}"
                )

        strategy = prefs.get("defaults", {}).get("strategy")
        if strategy and strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{strategy}'. Must be one of: {VALID_STRATEGIES}"
            )

    def _write(self, prefs: dict[str, Any]) -> None:
        """Atomic write via temp file + rename."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._data_dir), suffix=".tmp", prefix="prefs_",
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(prefs, f, indent=2)
                f.write("\n")
            Path(tmp).replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_preferences.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/preferences.py backend/tests/test_preferences.py
git commit -m "feat: add PreferencesService with file-based JSON persistence"
```

---

### Task 2: Preferences REST API

**Files:**
- Create: `backend/app/routers/preferences.py`
- Modify: `backend/app/main.py:94-152`

- [ ] **Step 1: Create the router**

```python
# backend/app/routers/preferences.py
"""Preferences REST API — GET/PATCH for persistent user settings."""

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR
from app.services.preferences import PreferencesService

router = APIRouter(prefix="/api", tags=["preferences"])

_svc = PreferencesService(DATA_DIR)


@router.get("/preferences")
async def get_preferences():
    """Return full preferences (merged with defaults)."""
    return _svc.load()


@router.patch("/preferences")
async def patch_preferences(body: dict):
    """Deep-merge updates into preferences. Validates before saving."""
    try:
        return _svc.patch(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **Step 2: Register in main.py**

Add after the existing router registrations (after line ~152 in `backend/app/main.py`):

```python
try:
    from app.routers.preferences import router as preferences_router
    app.include_router(preferences_router)
except ImportError:
    pass
```

- [ ] **Step 3: Run tests + smoke test**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q`
Expected: All tests pass

Run: `curl -s http://localhost:8000/api/preferences | python3 -m json.tool`
Expected: Returns full preferences JSON with defaults

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/preferences.py backend/app/main.py
git commit -m "feat: add GET/PATCH /api/preferences endpoints"
```

---

### Task 3: PipelineResult Schema Change

**Files:**
- Modify: `backend/app/schemas/pipeline_contracts.py:172-174`

- [ ] **Step 1: Make score fields Optional**

Change lines 172-176 in `pipeline_contracts.py`:

```python
# Before:
optimized_scores: DimensionScores
original_scores: DimensionScores
score_deltas: dict[str, float]
overall_score: float

# After:
optimized_scores: DimensionScores | None = None
original_scores: DimensionScores | None = None
score_deltas: dict[str, float] | None = None
overall_score: float | None = None
```

- [ ] **Step 2: Run all tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q`
Expected: All pass (existing code always provides scores; Optional is backward-compatible)

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/pipeline_contracts.py
git commit -m "feat: make PipelineResult score fields Optional for scoring-disabled mode"
```

---

## Chunk 2: Pipeline Integration

### Task 4: Pipeline Model Selection + Phase Skipping

**Files:**
- Modify: `backend/app/services/pipeline.py` (lines 203, 267, 321, 155-183, 453-454, 484-503)

- [ ] **Step 1: Add imports**

At top of `pipeline.py`, add:
```python
from app.services.preferences import PreferencesService
```

- [ ] **Step 2: Load preferences snapshot at start of run()**

At the top of the `run()` method (after `start_time = time.monotonic()`), add:
```python
prefs = PreferencesService(DATA_DIR)
prefs_snapshot = prefs.load()
```

- [ ] **Step 3: Replace model constants with preference lookups**

Replace:
- Line ~203: `model=settings.MODEL_SONNET` → `model=prefs.resolve_model("analyzer", prefs_snapshot)`
- Line ~217: `model=settings.MODEL_SONNET` (trace) → `model=prefs.resolve_model("analyzer", prefs_snapshot)`
- Line ~267: `model=settings.MODEL_OPUS` → `model=prefs.resolve_model("optimizer", prefs_snapshot)`
- Line ~282: `model=settings.MODEL_OPUS` (trace) → `model=prefs.resolve_model("optimizer", prefs_snapshot)`
- Line ~321: `model=settings.MODEL_SONNET` → `model=prefs.resolve_model("scorer", prefs_snapshot)`
- Line ~335: `model=settings.MODEL_SONNET` (trace) → `model=prefs.resolve_model("scorer", prefs_snapshot)`

Store the optimizer model for DB tracking:
```python
optimizer_model = prefs.resolve_model("optimizer", prefs_snapshot)
```

- [ ] **Step 4: Add explore phase preference check**

At line ~155, wrap the existing explore conditional:
```python
explore_enabled = prefs.get("pipeline.enable_explore", prefs_snapshot)
if explore_enabled and repo_full_name and github_token and codebase_context is None:
    # ... existing explore logic ...
```

- [ ] **Step 5: Add scoring phase skip**

Wrap the entire Phase 3 block (lines ~290-396) in a conditional:
```python
if prefs.get("pipeline.enable_scoring", prefs_snapshot):
    # --- existing Phase 3 scoring code ---
    yield PipelineEvent(event="status", data={"stage": "score", "state": "running"})
    # ... all scoring + hybrid blending ...
    yield PipelineEvent(event="score_card", data={...})
else:
    # Scoring disabled — skip Phase 3 entirely
    original_scores = None
    optimized_scores = None
    deltas = None
    scoring_mode = "skipped"
    logger.info("Scoring phase skipped per user preferences. trace_id=%s", trace_id)
```

- [ ] **Step 6: Add adaptation preference check**

Before the optimize phase, check:
```python
adaptation_enabled = prefs.get("pipeline.enable_adaptation", prefs_snapshot)
if not adaptation_enabled:
    adaptation_state = None
```

- [ ] **Step 7: Update DB persist block**

Replace hardcoded values in the Optimization constructor:
```python
model_used=optimizer_model,  # was settings.MODEL_OPUS
scoring_mode="hybrid" if prefs.get("pipeline.enable_scoring", prefs_snapshot) else "skipped",
```

And handle None scores:
```python
score_clarity=optimized_scores.clarity if optimized_scores else None,
score_specificity=optimized_scores.specificity if optimized_scores else None,
score_structure=optimized_scores.structure if optimized_scores else None,
score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
score_conciseness=optimized_scores.conciseness if optimized_scores else None,
overall_score=optimized_scores.overall if optimized_scores else None,
original_scores=original_scores.model_dump() if original_scores else None,
score_deltas=deltas,
```

- [ ] **Step 8: Update PipelineResult assembly**

```python
result = PipelineResult(
    # ... existing fields ...
    optimized_scores=optimized_scores,  # may be None
    original_scores=original_scores,    # may be None
    score_deltas=deltas,                # may be None
    overall_score=optimized_scores.overall if optimized_scores else None,
    model_used=optimizer_model,
    scoring_mode="hybrid" if optimized_scores else "skipped",
    # ...
)
```

- [ ] **Step 9: Run all tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/pipeline.py
git commit -m "feat: pipeline reads preferences for model selection and phase skipping"
```

---

### Task 5: Refinement Service + MCP Server Integration

**Files:**
- Modify: `backend/app/services/refinement_service.py` (lines 189, 216, 248, 458)
- Modify: `backend/app/mcp_server.py` (lines 250, 274, 314)
- Modify: `backend/app/routers/optimize.py` (line 59)

- [ ] **Step 1: Update refinement_service.py**

Add import at top:
```python
from app.services.preferences import PreferencesService
from app.config import DATA_DIR
```

In `create_refinement_turn()` method, load snapshot once at start:
```python
_prefs = PreferencesService(DATA_DIR)
_prefs_snapshot = _prefs.load()
```

Replace 3 model constants:
- Line ~189: `model=settings.MODEL_SONNET` → `model=_prefs.resolve_model("analyzer", _prefs_snapshot)`
- Line ~216: `model=settings.MODEL_OPUS` → `model=_prefs.resolve_model("optimizer", _prefs_snapshot)`
- Line ~248: `model=settings.MODEL_SONNET` → `model=_prefs.resolve_model("scorer", _prefs_snapshot)`
- Line ~458: `model=settings.MODEL_HAIKU` — **leave as-is** (intentionally not configurable)

- [ ] **Step 2: Update mcp_server.py synthesis_analyze**

Load snapshot once in synthesis_analyze:
```python
prefs = PreferencesService(DATA_DIR)
prefs_snapshot = prefs.load()
```

Replace 3 references:
- Line ~250: `model=settings.MODEL_SONNET` → `model=prefs.resolve_model("analyzer", prefs_snapshot)`
- Line ~274: `model=settings.MODEL_SONNET` → `model=prefs.resolve_model("scorer", prefs_snapshot)`
- Line ~314: `model_used=settings.MODEL_SONNET` → `model_used=prefs.resolve_model("analyzer", prefs_snapshot)`

- [ ] **Step 3: Update optimize.py for default strategy**

Add imports at top of `optimize.py`:
```python
from app.services.preferences import PreferencesService
```

In the `optimize()` handler, before passing to orchestrator:
```python
_prefs = PreferencesService(DATA_DIR)
effective_strategy = body.strategy or _prefs.get("defaults.strategy") or None
# ... pass to orchestrator:
strategy_override=effective_strategy,
```

- [ ] **Step 4: Ruff check + full tests**

Run: `cd backend && source .venv/bin/activate && ruff check app/ tests/ && pytest tests/ -x -q`
Expected: All checks pass, all tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/refinement_service.py backend/app/mcp_server.py backend/app/routers/optimize.py
git commit -m "feat: refinement, MCP, and optimize router read model preferences"
```

---

## Chunk 3: Frontend

### Task 6: API Client + Preferences Store

**Files:**
- Modify: `frontend/src/lib/api/client.ts`
- Create: `frontend/src/lib/stores/preferences.svelte.ts`
- Modify: `frontend/src/routes/+layout.svelte`

- [ ] **Step 1: Add API client functions**

Add to `frontend/src/lib/api/client.ts`:

```typescript
// ---- Preferences ----
export async function getPreferences(): Promise<Record<string, any>> {
  const res = await fetch('/api/preferences');
  if (!res.ok) throw new ApiError(res.status, 'Failed to load preferences');
  return res.json();
}

export async function patchPreferences(updates: Record<string, any>): Promise<Record<string, any>> {
  const res = await fetch('/api/preferences', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail || 'Failed to save preferences');
  }
  return res.json();
}
```

- [ ] **Step 2: Create preferences store**

```typescript
// frontend/src/lib/stores/preferences.svelte.ts
import { getPreferences, patchPreferences } from '$lib/api/client';

export interface ModelPrefs {
  analyzer: string;
  optimizer: string;
  scorer: string;
}

export interface PipelinePrefs {
  enable_explore: boolean;
  enable_scoring: boolean;
  enable_adaptation: boolean;
}

export interface Preferences {
  schema_version: number;
  models: ModelPrefs;
  pipeline: PipelinePrefs;
  defaults: { strategy: string };
}

const DEFAULTS: Preferences = {
  schema_version: 1,
  models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
  pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true },
  defaults: { strategy: 'auto' },
};

class PreferencesStore {
  prefs = $state<Preferences>(structuredClone(DEFAULTS));
  loading = $state(false);
  error = $state<string | null>(null);

  get models(): ModelPrefs { return this.prefs.models; }
  get pipeline(): PipelinePrefs { return this.prefs.pipeline; }
  get defaultStrategy(): string { return this.prefs.defaults.strategy; }

  get isLeanMode(): boolean {
    return !this.prefs.pipeline.enable_explore && !this.prefs.pipeline.enable_scoring;
  }

  async init(): Promise<void> {
    this.loading = true;
    this.error = null;
    try {
      const data = await getPreferences();
      this.prefs = data as Preferences;
    } catch {
      // Backend offline — use defaults
    } finally {
      this.loading = false;
    }
  }

  async update(patch: Record<string, any>): Promise<void> {
    this.error = null;
    try {
      const updated = await patchPreferences(patch);
      this.prefs = updated as Preferences;
    } catch (err: any) {
      this.error = err.message || 'Failed to save';
    }
  }

  async setModel(phase: string, model: string): Promise<void> {
    await this.update({ models: { [phase]: model } });
  }

  async setPipelineToggle(key: string, value: boolean): Promise<void> {
    await this.update({ pipeline: { [key]: value } });
  }

  async setDefaultStrategy(strategy: string): Promise<void> {
    await this.update({ defaults: { strategy } });
  }
}

export const preferencesStore = new PreferencesStore();
```

- [ ] **Step 3: Init store in +layout.svelte**

In `frontend/src/routes/+layout.svelte`, add import and init call:

```typescript
import { preferencesStore } from '$lib/stores/preferences.svelte';

// Add to existing $effect or onMount:
$effect(() => {
  preferencesStore.init();
});
```

- [ ] **Step 4: Run svelte-check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/client.ts frontend/src/lib/stores/preferences.svelte.ts frontend/src/routes/+layout.svelte
git commit -m "feat: add preferences store with API client and root layout init"
```

---

### Task 7: Expanded Settings Panel

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte` (lines 262-361)

This is the largest UI task. Replace the existing sparse settings panel with the full expanded version containing: Models, Pipeline, Defaults, Provider, API Key, GitHub, and System sections.

- [ ] **Step 1: Add imports to Navigator.svelte**

Add at top of `<script>`:
```typescript
import { preferencesStore } from '$lib/stores/preferences.svelte';
```

- [ ] **Step 2: Replace the settings panel section**

Replace the entire `{:else if active === 'settings'}` block (lines 262-361) with the expanded settings panel containing:

1. **MODELS** section — three `<select>` dropdowns for analyzer/optimizer/scorer. Options: sonnet, opus, haiku. `onchange` calls `preferencesStore.setModel(phase, value)`.

2. **PIPELINE** section — three CSS toggle switches for enable_explore, enable_scoring, enable_adaptation. `onchange` calls `preferencesStore.setPipelineToggle(key, value)`. Show "LEAN MODE" badge when both explore and scoring are OFF.

3. **DEFAULTS** section — strategy `<select>` dropdown. Options match the strategies list already in the component. `onchange` calls `preferencesStore.setDefaultStrategy(value)`.

4. **PROVIDER** section — keep existing (lines 269-285).

5. **API KEY** section — keep existing (lines 287-332).

6. **GITHUB** section — move the GitHub panel content here. Auth status, linked repo, connect/disconnect buttons.

7. **SYSTEM** section — keep existing Config display (lines 334-356), add scoring mode display.

**CSS for toggle switches:**
```css
.toggle-track {
  width: 28px;
  height: 14px;
  background: var(--color-bg-input);
  border: 1px solid var(--color-border-subtle);
  cursor: pointer;
  position: relative;
  transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  flex-shrink: 0;
}

.toggle-track--on {
  background: rgba(0, 229, 255, 0.15);
  border-color: var(--color-neon-cyan);
}

.toggle-thumb {
  width: 10px;
  height: 10px;
  background: var(--color-text-dim);
  position: absolute;
  top: 1px;
  left: 1px;
  transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
}

.toggle-track--on .toggle-thumb {
  left: 15px;
  background: var(--color-neon-cyan);
}
```

- [ ] **Step 3: Update GitHub activity bar to redirect to settings**

In `+layout.svelte`, when the GitHub activity bar icon is clicked, set `activeActivity = 'settings'` and auto-scroll to the GitHub section. The simplest approach: clicking the GitHub icon in ActivityBar dispatches `switch-activity` with `'settings'`.

- [ ] **Step 4: Run svelte-check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte frontend/src/lib/components/layout/ActivityBar.svelte
git commit -m "feat: expanded settings panel with models, pipeline toggles, and GitHub consolidation"
```

---

## Chunk 4: Documentation + Verification

### Task 8: CLAUDE.md + Final Verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to Key services section:
```
- `preferences.py` — persistent user preferences (model selection, pipeline toggles, default strategy). File-based JSON at `data/preferences.json`.
```

Add to Routers section:
```
- `preferences.py` — `GET /api/preferences`, `PATCH /api/preferences`
```

Add to Key architectural decisions:
```
- **User preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase (analyzer/optimizer/scorer), pipeline toggle (explore/scoring/adaptation), default strategy. Non-configurable: explore synthesis and suggestions always use Haiku.
```

- [ ] **Step 2: Ruff + full test suite**

Run: `cd backend && source .venv/bin/activate && ruff check app/ tests/ && pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 3: Svelte check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 4: Restart + manual E2E test**

```bash
./init.sh restart
# Verify preferences API:
curl -s http://localhost:8000/api/preferences | python3 -m json.tool
# Change a model:
curl -s -X PATCH http://localhost:8000/api/preferences -H "Content-Type: application/json" -d '{"models": {"analyzer": "haiku"}}' | python3 -m json.tool
# Restart and verify persistence:
./init.sh restart
curl -s http://localhost:8000/api/preferences | python3 -m json.tool
# Should still show analyzer: haiku
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with preferences system documentation"
```
