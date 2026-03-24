# Remove Sampling Model Hints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip advisory ModelPreferences/ModelHint from MCP sampling, persist per-phase model IDs, and show actual models used in Navigator and Inspector.

**Architecture:** Remove ~95 lines of hint machinery from `sampling_pipeline.py`, add `models_by_phase` JSON column via Alembic migration, capture per-phase model IDs in both internal and sampling pipelines, surface them through SSE events and REST responses, and replace the Navigator's hint dropdowns with a real-time model display for the sampling tier.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, SvelteKit 5 (Svelte runes), Tailwind CSS 4

**Spec:** `docs/superpowers/specs/2026-03-24-remove-sampling-hints-design.md`

---

### Task 1: Alembic Migration — `models_by_phase` Column

**Files:**
- Create: `backend/alembic/versions/<auto>_add_models_by_phase.py`
- Modify: `backend/app/models.py:53` (after `model_used`)

- [ ] **Step 1: Add column to SQLAlchemy model**

In `backend/app/models.py`, add after line 53 (`model_used = Column(String, nullable=True)`):

```python
    models_by_phase = Column(JSON, nullable=True)
```

- [ ] **Step 2: Generate Alembic migration**

Run: `cd backend && source .venv/bin/activate && alembic revision --autogenerate -m "add models_by_phase to optimization"`
Expected: New migration file created under `backend/alembic/versions/`

- [ ] **Step 3: Apply migration**

Run: `cd backend && source .venv/bin/activate && alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade ... -> ..., add models_by_phase to optimization`

- [ ] **Step 4: Verify column exists**

Run: `cd backend && source .venv/bin/activate && python -c "from app.database import engine; import asyncio; asyncio.run(engine.dispose())"; sqlite3 data/synthesis.db ".schema optimization" | grep models_by_phase`
Expected: `models_by_phase TEXT` (SQLite stores JSON as TEXT)

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/*models_by_phase*
git commit -m "feat(db): add models_by_phase JSON column to Optimization"
```

---

### Task 2: Remove Hint Machinery from `sampling_pipeline.py`

**Files:**
- Modify: `backend/app/services/sampling_pipeline.py:32-176` (imports + hint dicts + function)
- Modify: `backend/app/services/sampling_pipeline.py:184-320` (request function signatures)
- Test: `backend/tests/test_sampling_pipeline.py`

- [ ] **Step 1: Write tests to verify hint-free sampling requests work**

In `backend/tests/test_sampling_pipeline.py`, add after the existing test functions (replace the 8 `_resolve_model_preferences` tests at lines 111-192):

```python
# The 8 _resolve_model_preferences tests (lines 111-192) should be DELETED.
# Replace with tests that verify no model_preferences are sent:

@pytest.mark.asyncio
async def test_sampling_request_plain_sends_no_model_preferences():
    """Verify _sampling_request_plain does not include model_preferences in kwargs."""
    ctx = _make_ctx(create_message_return=_make_text_result("hello"))
    text, model_id = await _sampling_request_plain(ctx, "system", "user")
    call_kwargs = ctx.session.create_message.call_args
    assert "model_preferences" not in call_kwargs.kwargs
    assert text == "hello"


@pytest.mark.asyncio
async def test_sampling_request_structured_sends_no_model_preferences():
    """Verify _sampling_request_structured does not include model_preferences."""
    ctx = _make_ctx(create_message_return=_make_tool_use_result({"name": "x", "value": 1}))
    result, model_id = await _sampling_request_structured(
        ctx, "system", "user", _SimpleModel,
    )
    call_kwargs = ctx.session.create_message.call_args
    assert "model_preferences" not in call_kwargs.kwargs
    assert result.name == "x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_sampling_pipeline.py::test_sampling_request_plain_sends_no_model_preferences tests/test_sampling_pipeline.py::test_sampling_request_structured_sends_no_model_preferences -v`
Expected: FAIL (new tests not yet added, or old code still sends model_preferences)

- [ ] **Step 3: Remove imports**

In `backend/app/services/sampling_pipeline.py`, change the import block at lines 32-40 from:

```python
from mcp.types import (
    CreateMessageResult,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
    ToolChoice,
    ToolResultContent,
)
```

to:

```python
from mcp.types import (
    CreateMessageResult,
    SamplingMessage,
    TextContent,
    ToolChoice,
    ToolResultContent,
)
```

- [ ] **Step 4: Delete hint machinery (lines 82-176)**

Delete the following blocks entirely:
- Lines 82-110: `_PHASE_PRESETS`, `_PREF_TO_MODEL`, `_EFFORT_PRIORITIES` dicts and their comments
- Lines 129-176: `_resolve_model_preferences()` function and its docstring

- [ ] **Step 5: Remove `model_preferences` from `_sampling_request_plain()`**

At the function starting around line 184, remove the `model_preferences` parameter and its usage:

Before:
```python
async def _sampling_request_plain(
    ctx: Context,
    system: str,
    user: str,
    *,
    max_tokens: int = 16384,
    model_preferences: ModelPreferences | None = None,
) -> tuple[str, str]:
    """Send a text-only sampling request.  Returns ``(text, model_id)``."""
    kwargs: dict[str, Any] = {
        "messages": [SamplingMessage(role="user", content=TextContent(type="text", text=user))],
        "system_prompt": system,
        "max_tokens": max_tokens,
    }
    if model_preferences is not None:
        kwargs["model_preferences"] = model_preferences
```

After:
```python
async def _sampling_request_plain(
    ctx: Context,
    system: str,
    user: str,
    *,
    max_tokens: int = 16384,
) -> tuple[str, str]:
    """Send a text-only sampling request.  Returns ``(text, model_id)``."""
    kwargs: dict[str, Any] = {
        "messages": [SamplingMessage(role="user", content=TextContent(type="text", text=user))],
        "system_prompt": system,
        "max_tokens": max_tokens,
    }
```

- [ ] **Step 6: Remove `model_preferences` from `_sampling_request_structured()`**

At the function starting around line 263, remove the `model_preferences` parameter and its usage:

Before:
```python
async def _sampling_request_structured(
    ctx: Context,
    system: str,
    user: str,
    output_model: type[T],
    *,
    max_tokens: int = 16384,
    model_preferences: ModelPreferences | None = None,
    tool_name: str = "respond",
) -> tuple[T, str]:
```

After:
```python
async def _sampling_request_structured(
    ctx: Context,
    system: str,
    user: str,
    output_model: type[T],
    *,
    max_tokens: int = 16384,
    tool_name: str = "respond",
) -> tuple[T, str]:
```

Also remove the two lines inside the function body:
```python
        if model_preferences is not None:
            kwargs["model_preferences"] = model_preferences
```

- [ ] **Step 7: Remove hint usage from `SamplingLLMAdapter` (line 392)**

In `backend/app/services/sampling_pipeline.py`, update `SamplingLLMAdapter.complete_parsed()` at lines 391-398:

Before:
```python
    async def complete_parsed(self, model, system_prompt, user_message, output_format, max_tokens=16384, effort=None):
        """Delegate to structured sampling with Haiku preferences."""
        prefs = _resolve_model_preferences("suggest")  # Haiku
        parsed, _model_id = await _sampling_request_structured(
            self._ctx, system_prompt, user_message, output_format,
            max_tokens=max_tokens,
            model_preferences=prefs,
        )
        return parsed
```

After:
```python
    async def complete_parsed(self, model, system_prompt, user_message, output_format, max_tokens=16384, effort=None):
        """Delegate to structured sampling — IDE selects the model."""
        parsed, _model_id = await _sampling_request_structured(
            self._ctx, system_prompt, user_message, output_format,
            max_tokens=max_tokens,
        )
        return parsed
```

Also update the class docstring (lines 366-373) to remove Haiku/suggest phase references:

Before:
```python
    """Minimal ``LLMProvider`` wrapper that delegates to MCP sampling.

    Only ``complete_parsed()`` is implemented — sufficient for
    ``CodebaseExplorer`` which needs a single Haiku synthesis call.

    Note: The ``model`` parameter in ``complete_parsed()`` is intentionally
    ignored.  The adapter always uses Haiku model preferences (the "suggest"
    phase preset), matching ``CodebaseExplorer``'s design assumption.
    """
```

After:
```python
    """Minimal ``LLMProvider`` wrapper that delegates to MCP sampling.

    Only ``complete_parsed()`` is implemented — sufficient for
    ``CodebaseExplorer`` which needs a single synthesis call.  The IDE
    selects which model to use.

    Note: The ``model`` parameter in ``complete_parsed()`` is intentionally
    ignored — the IDE has full control over model selection.
    """
```

- [ ] **Step 8: Remove all `_resolve_model_preferences()` call sites in `run_sampling_pipeline()`**

Remove these lines and their `model_preferences=` kwargs from the calls that follow:

| Line | Remove |
|------|--------|
| ~537 | `analyze_prefs = _resolve_model_preferences("analyze", prefs_snapshot)` |
| ~541 | `model_preferences=analyze_prefs,` |
| ~547 | `model_preferences=analyze_prefs,` |
| ~700 | `optimize_prefs = _resolve_model_preferences("optimize", prefs_snapshot)` |
| ~706 | `model_preferences=optimize_prefs,` |
| ~713 | `model_preferences=optimize_prefs,` |
| ~773 | `score_prefs = _resolve_model_preferences("score", prefs_snapshot)` |
| ~778 | `model_preferences=score_prefs,` |
| ~854 | `suggest_prefs = _resolve_model_preferences("suggest", prefs_snapshot)` |
| ~858 | `model_preferences=suggest_prefs,` |

- [ ] **Step 9: Remove `_resolve_model_preferences()` call sites in `run_sampling_analyze()`**

Remove these lines:

| Line | Remove |
|------|--------|
| ~1011 | `analyze_prefs = _resolve_model_preferences("analyze", prefs_snapshot)` |
| ~1014 | `model_preferences=analyze_prefs,` |
| ~1074 | `score_prefs = _resolve_model_preferences("score", prefs_snapshot)` |
| ~1079 | `model_preferences=score_prefs,` |

- [ ] **Step 10: Update module docstring**

Change lines 12-13 from:
```python
- **Model preferences per phase** — ``ModelPreferences`` hints steer the IDE
  towards the right model class (e.g. Opus for optimize, Haiku for suggest).
```
to:
```python
- **Per-phase model capture** — the actual model used by the IDE is recorded
  from each ``CreateMessageResult.model`` field and persisted to DB.
```

- [ ] **Step 11: Update test file imports and delete old tests**

In `backend/tests/test_sampling_pipeline.py`, change line 21 to remove `_resolve_model_preferences`:

Before:
```python
from app.services.sampling_pipeline import (
    SamplingLLMAdapter,
    _parse_text_response,
    _pydantic_to_mcp_tool,
    _resolve_model_preferences,
    _sampling_request_plain,
    _sampling_request_structured,
)
```

After:
```python
from app.services.sampling_pipeline import (
    SamplingLLMAdapter,
    _parse_text_response,
    _pydantic_to_mcp_tool,
    _sampling_request_plain,
    _sampling_request_structured,
)
```

Delete the 8 test functions at lines 111-192 (`test_model_preferences_*`). Add the 2 new tests from Step 1 in their place.

- [ ] **Step 12: Run all sampling pipeline tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_sampling_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 13: Commit**

```bash
git add backend/app/services/sampling_pipeline.py backend/tests/test_sampling_pipeline.py
git commit -m "feat(sampling): remove ModelPreferences/ModelHint — IDE selects model freely"
```

---

### Task 3: Persist `models_by_phase` in Sampling Pipeline

**Files:**
- Modify: `backend/app/services/sampling_pipeline.py:907,962` (DB persist + return dict in `run_sampling_pipeline`)
- Modify: `backend/app/services/sampling_pipeline.py:1130,1135` (DB persist in `run_sampling_analyze`)

- [ ] **Step 1: Add `models_by_phase` to DB persist in `run_sampling_pipeline()`**

At line ~910 (inside the `Optimization()` constructor), after `tokens_by_phase=phase_durations,`, add:

```python
            models_by_phase=model_ids,
```

- [ ] **Step 2: Add `models_by_phase` to return dict in `run_sampling_pipeline()`**

At line ~962 (in the return dict), after `"model_used": model_ids.get("optimize", "unknown"),`, add:

```python
        "models_by_phase": model_ids,
```

- [ ] **Step 3: Add `models_by_phase` to DB persist in `run_sampling_analyze()`**

At line ~1135 (inside the `Optimization()` constructor), after `tokens_by_phase=phase_durations,`, add:

```python
            models_by_phase={"analyze": _analyze_model, "score": _score_model},
```

- [ ] **Step 4: Run sampling pipeline tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_sampling_pipeline.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sampling_pipeline.py
git commit -m "feat(sampling): persist models_by_phase to DB and return dict"
```

---

### Task 4: Persist `models_by_phase` in Internal Pipeline + SSE Model Reveal

**Files:**
- Modify: `backend/app/services/pipeline.py:166,276,469,534,709,797,838`
- Modify: `backend/app/schemas/pipeline_contracts.py:314`

- [ ] **Step 1: Add `models_by_phase` to `PipelineResult` contract**

In `backend/app/schemas/pipeline_contracts.py`, after line 314 (`model_used: str`), add:

```python
    models_by_phase: dict[str, str] = Field(
        default_factory=dict,
        description="Per-phase model IDs: {analyze: '...', optimize: '...', score: '...'}.",
    )
```

- [ ] **Step 2: Capture per-phase model IDs in internal pipeline**

In `backend/app/services/pipeline.py`, after line 166 (`optimizer_model = prefs.resolve_model("optimizer", prefs_snapshot)`), add:

```python
        analyzer_model = prefs.resolve_model("analyzer", prefs_snapshot)
        scorer_model = prefs.resolve_model("scorer", prefs_snapshot)
        model_ids: dict[str, str] = {
            "analyze": analyzer_model,
            "optimize": optimizer_model,
            "score": scorer_model,
        }
```

- [ ] **Step 3: Add `model` field to phase-complete SSE events**

Change line 276 from:
```python
            yield PipelineEvent(event="status", data={"stage": "analyze", "state": "complete"})
```
to:
```python
            yield PipelineEvent(event="status", data={"stage": "analyze", "state": "complete", "model": analyzer_model})
```

Change line 469 from:
```python
            yield PipelineEvent(event="status", data={"stage": "optimize", "state": "complete"})
```
to:
```python
            yield PipelineEvent(event="status", data={"stage": "optimize", "state": "complete", "model": optimizer_model})
```

Change line 534 from:
```python
                yield PipelineEvent(event="status", data={"stage": "score", "state": "complete"})
```
to:
```python
                yield PipelineEvent(event="status", data={"stage": "score", "state": "complete", "model": scorer_model})
```

- [ ] **Step 4: Persist `models_by_phase` to DB**

At line 717 (inside `Optimization()` constructor), after `tokens_by_phase=phase_durations,`, add:

```python
                models_by_phase=model_ids,
```

- [ ] **Step 5: Include `models_by_phase` in `PipelineResult`**

At line 797 (inside `PipelineResult()` constructor), after `model_used=optimizer_model,`, add:

```python
                models_by_phase=model_ids,
```

- [ ] **Step 6: Include `models_by_phase` in failed optimization persist**

At line 838 (inside failed `Optimization()` constructor), after `model_used=optimizer_model,`, add:

```python
                    models_by_phase=model_ids,
```

- [ ] **Step 7: Run backend tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/pipeline.py backend/app/schemas/pipeline_contracts.py
git commit -m "feat(pipeline): capture per-phase model IDs and emit in SSE events"
```

---

### Task 5: API Response Plumbing — `models_by_phase`

**Files:**
- Modify: `backend/app/routers/optimize.py:28-58,228-258`
- Modify: `backend/app/schemas/mcp_models.py:92-95`
- Modify: `backend/app/tools/optimize.py:204-222,252-272`

- [ ] **Step 1: Add to `OptimizationDetail` schema**

In `backend/app/routers/optimize.py`, after line 46 (`model_used`), add:

```python
    models_by_phase: dict[str, str] | None = Field(default=None, description="Per-phase model IDs used during optimization.")
```

- [ ] **Step 2: Add to `_serialize_optimization()` helper**

In `backend/app/routers/optimize.py`, at line ~249 (inside the `OptimizationDetail()` constructor), after `model_used=opt.model_used,`, add:

```python
        models_by_phase=opt.models_by_phase,
```

- [ ] **Step 3: Add to `OptimizeOutput` MCP schema**

In `backend/app/schemas/mcp_models.py`, after line 95 (`model_used` field), add:

```python
    models_by_phase: dict[str, str] | None = Field(
        default=None,
        description="Per-phase model IDs: {analyze: '...', optimize: '...', score: '...'}.",
    )
```

- [ ] **Step 4: Add to `_sampling_result_to_output()` in tools**

In `backend/app/tools/optimize.py`, at line ~268 (inside `_sampling_result_to_output()`), after `model_used=result.get("model_used"),`, add:

```python
        models_by_phase=result.get("models_by_phase"),
```

- [ ] **Step 5: Add to internal pipeline `OptimizeOutput` in tools**

In `backend/app/tools/optimize.py`, at line ~218 (inside the internal pipeline `OptimizeOutput()` constructor), after `model_used=result.get("model_used"),`, add:

```python
        models_by_phase=result.get("models_by_phase"),
```

- [ ] **Step 6: Add `models_by_phase` to `HistoryItem` schema**

In `backend/app/routers/history.py`, after line 28 (`provider`), add:

```python
    models_by_phase: dict[str, str] | None = Field(default=None, description="Per-phase model IDs used.")
```

Then find the serialization site that builds `HistoryItem` instances and add `models_by_phase=opt.models_by_phase`.

- [ ] **Step 7: Run backend tests**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/optimize.py backend/app/routers/history.py backend/app/schemas/mcp_models.py backend/app/tools/optimize.py
git commit -m "feat(api): surface models_by_phase in REST and MCP responses"
```

---

### Task 6: Frontend — `OptimizationResult` + Forge Store

**Files:**
- Modify: `frontend/src/lib/api/client.ts:36-58`
- Modify: `frontend/src/lib/stores/forge.svelte.ts:185-222`

- [ ] **Step 1: Add `models_by_phase` to `OptimizationResult` interface**

In `frontend/src/lib/api/client.ts`, after line 53 (`model_used: string;`), add:

```typescript
  models_by_phase: Record<string, string> | null;
```

- [ ] **Step 2: Add `phaseModels` state to forge store**

In `frontend/src/lib/stores/forge.svelte.ts`, find the class state declarations (near `currentPhase`, `scores`, etc.) and add:

```typescript
  phaseModels: Record<string, string> = $state({});
```

- [ ] **Step 3: Reset `phaseModels` on new optimization**

Find the reset/forge method that sets `this.status = 'synthesizing'` (or similar) and add:

```typescript
    this.phaseModels = {};
```

- [ ] **Step 4: Capture model from SSE status events**

In `forge.svelte.ts` at line ~188 (inside the `eventType === 'status'` handler), after updating `this.currentPhase`, add:

```typescript
      const model = event.model as string | undefined;
      if (model && phase) {
        this.phaseModels = { ...this.phaseModels, [phase]: model };
      }
```

- [ ] **Step 5: Capture `models_by_phase` from `optimization_complete` event**

In `forge.svelte.ts` at line ~204 (inside the `eventType === 'optimization_complete'` handler), before `this.loadFromRecord(data as OptimizationResult)`, add:

```typescript
      if (data.models_by_phase) {
        this.phaseModels = data.models_by_phase as Record<string, string>;
      }
```

- [ ] **Step 6: Populate `phaseModels` from `loadFromRecord`**

Find the `loadFromRecord` method and add at the end:

```typescript
    if (record.models_by_phase) {
      this.phaseModels = record.models_by_phase;
    }
```

- [ ] **Step 7: Run frontend type check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api/client.ts frontend/src/lib/stores/forge.svelte.ts
git commit -m "feat(frontend): track per-phase model IDs from SSE events"
```

---

### Task 7: Navigator — Replace Hint Dropdowns with IDE Model Display

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte:486-509,610-635`
- Modify: `frontend/src/lib/components/layout/Navigator.test.ts:913-995`

- [ ] **Step 1: Replace "Model Hints" section with "IDE Model" section**

In `Navigator.svelte`, replace lines 486-509 (the `{:else}` block for non-passthrough models):

Before:
```svelte
        {:else}
        <div class="sub-section">
          <span class="sub-heading" class:sub-heading--sampling={routing.isSampling}>{routing.isSampling ? 'Model Hints' : 'Models'}</span>
          {#if routing.isSampling}<span class="sub-heading-note sub-heading-note--sampling">// via IDE</span>{/if}
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', phase: 'analyzer' },
              { label: 'Optimizer', phase: 'optimizer' },
              { label: 'Scorer', phase: 'scorer' },
            ] as { label, phase }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.models[phase as keyof typeof preferencesStore.models]}
                  onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
                >
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

After:
```svelte
        {:else if routing.isSampling}
        <div class="sub-section">
          <span class="sub-heading sub-heading--sampling">IDE Model</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyze' },
              { label: 'Optimizer', key: 'optimize' },
              { label: 'Scorer', key: 'score' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <span class="data-value neon-green" class:data-value--dim={!forgeStore.phaseModels[key]}>
                  {forgeStore.phaseModels[key] || 'pending'}
                </span>
              </div>
            {/each}
          </div>
        </div>
        {:else}
        <div class="sub-section">
          <span class="sub-heading">Models</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', phase: 'analyzer' },
              { label: 'Optimizer', phase: 'optimizer' },
              { label: 'Scorer', phase: 'scorer' },
            ] as { label, phase }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.models[phase as keyof typeof preferencesStore.models]}
                  onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
                >
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

- [ ] **Step 2: Replace "Effort Hints" section**

In `Navigator.svelte`, replace lines 610-635 (the `{:else}` block for effort/scoring):

Before:
```svelte
        {:else}
        <div class="sub-section">
          <span class="sub-heading" class:sub-heading--sampling={routing.isSampling}>{routing.isSampling ? 'Effort Hints' : 'Effort'}</span>
          {#if routing.isSampling}<span class="sub-heading-note sub-heading-note--sampling">// via IDE</span>{/if}
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyzer_effort' },
              { label: 'Optimizer', key: 'optimizer_effort' },
              { label: 'Scorer', key: 'scorer_effort' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline] as string}
                  onchange={(e) => preferencesStore.setEffort(key, (e.target as HTMLSelectElement).value)}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="max">max</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

After:
```svelte
        {:else if !routing.isSampling}
        <div class="sub-section">
          <span class="sub-heading">Effort</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyzer_effort' },
              { label: 'Optimizer', key: 'optimizer_effort' },
              { label: 'Scorer', key: 'scorer_effort' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline] as string}
                  onchange={(e) => preferencesStore.setEffort(key, (e.target as HTMLSelectElement).value)}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="max">max</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

Note: In sampling mode, neither Models nor Effort dropdowns are shown — the IDE Model section (Task 7 Step 1) replaces both.

- [ ] **Step 3: Update Navigator tests**

In `Navigator.test.ts`, replace the sampling mode tests at lines ~913-995. Delete tests that assert on "Model Hints", "Effort Hints", and `// via IDE`. Replace with:

```typescript
it('shows "IDE Model" heading in sampling mode', async () => {
  // Setup: mock routing.isSampling = true
  // Assert: expect "IDE Model" text to be present
  // Assert: expect "pending" for each phase (no optimization yet)
});

it('shows actual model IDs after optimization in sampling mode', async () => {
  // Setup: mock routing.isSampling = true, forgeStore.phaseModels = { analyze: 'gpt-5-mini', optimize: 'claude-sonnet-4-6', score: 'gpt-5-mini' }
  // Assert: expect model IDs to appear next to phase labels
});

it('hides model dropdowns in sampling mode', async () => {
  // Setup: mock routing.isSampling = true
  // Assert: no <select> elements in the Models section
});

it('hides effort dropdowns in sampling mode', async () => {
  // Setup: mock routing.isSampling = true
  // Assert: no effort <select> elements visible
});

it('shows standard "Models" heading in internal mode', async () => {
  // Keep existing regression test at lines 974-980
});

it('shows standard "Effort" heading in internal mode', async () => {
  // Keep existing regression test at lines 982-988
});
```

- [ ] **Step 4: Run frontend tests and type check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte frontend/src/lib/components/layout/Navigator.test.ts
git commit -m "feat(ui): replace sampling hint dropdowns with IDE Model display"
```

---

### Task 8: Inspector — Per-Phase Model Display

**Files:**
- Modify: `frontend/src/lib/components/layout/Inspector.svelte:393-412`

- [ ] **Step 1: Add Models row to Inspector meta-section**

In `Inspector.svelte`, after the provider meta-row (lines 406-411), add:

```svelte
          {#if activeResult?.models_by_phase && activeResult.provider === 'mcp_sampling'}
            <div class="meta-row">
              <span class="meta-label">Models</span>
              <span
                class="meta-value meta-value--green"
                title="analyze: {activeResult.models_by_phase.analyze || '?'} / optimize: {activeResult.models_by_phase.optimize || '?'} / score: {activeResult.models_by_phase.score || '?'}"
              >
                {activeResult.models_by_phase.analyze || '?'} / {activeResult.models_by_phase.optimize || '?'} / {activeResult.models_by_phase.score || '?'}
              </span>
            </div>
          {/if}
```

- [ ] **Step 2: Add `meta-value--green` style if not present**

Check existing styles. If `meta-value--green` is not defined, add to the `<style>` block:

```css
.meta-value--green { color: var(--color-neon-green); }
```

- [ ] **Step 3: Run frontend type check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/Inspector.svelte
git commit -m "feat(ui): show per-phase model IDs in Inspector for sampling results"
```

---

### Task 9: SamplingGuide Modal Updates

**Files:**
- Modify: `frontend/src/lib/components/shared/SamplingGuide.svelte:15-56,67-71`

- [ ] **Step 1: Update step 2 description and detail**

In `SamplingGuide.svelte`, change step 2 (lines 26-30):

Before:
```typescript
    {
      number: 2,
      title: 'You enter a prompt',
      description:
        'Type or paste your prompt in the editor. The system routes to the IDE\'s LLM instead of the backend provider. Model Hints and Effort Hints steer the IDE\'s model selection.',
      detail: 'Hints are advisory — the IDE has final say on which model to use',
      accent: 'cyan',
    },
```

After:
```typescript
    {
      number: 2,
      title: 'You enter a prompt',
      description:
        'Type or paste your prompt in the editor. The system routes to the IDE\'s LLM instead of the backend provider. The IDE selects which model to use for each phase.',
      detail: 'Model used per phase is displayed as each phase completes',
      accent: 'cyan',
    },
```

- [ ] **Step 2: Update `whyText`**

In `SamplingGuide.svelte`, change the `whyText` prop (line 71):

Before:
```typescript
  whyText="Your IDE's LLM powers the entire optimization pipeline via MCP sampling. Full 3-phase pipeline (analyze, optimize, score) runs through the IDE — no backend provider or API key needed. Model and effort preferences are transmitted as hints; the IDE has final say on model selection."
```

After:
```typescript
  whyText="Your IDE's LLM powers the entire optimization pipeline via MCP sampling. Full 3-phase pipeline (analyze, optimize, score) runs through the IDE — no backend provider or API key needed. The IDE selects the model — the actual model used is captured per phase and displayed in real time."
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/shared/SamplingGuide.svelte
git commit -m "fix(ui): update SamplingGuide to remove hint references"
```

---

### Task 10: Documentation + Changelog

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md sampling pipeline description**

Find the `### Sampling pipeline` section and update references to `ModelPreferences` per phase. Change language from "model preferences per phase" to "per-phase model capture from IDE response". Remove mentions of `ModelHint`.

Find the `### Key services` entry for `sampling_pipeline.py` and update the description to remove "Model preferences per phase" and replace with "Per-phase model ID capture".

- [ ] **Step 2: Update CHANGELOG.md**

Add under `## Unreleased` → `Changed`:

```markdown
- Removed advisory MCP `ModelPreferences`/`ModelHint` from sampling pipeline — IDE selects model freely; actual model captured per phase
- Navigator shows actual model IDs used by IDE instead of hint dropdowns in sampling mode
- Inspector displays per-phase model breakdown for sampling results
- New `models_by_phase` JSON column persists per-phase model IDs for both internal and sampling pipelines
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md
git commit -m "docs: update CLAUDE.md and CHANGELOG for sampling hint removal"
```

---

### Task 11: Final Verification

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && source .venv/bin/activate && pytest tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && npx svelte-check --tsconfig ./tsconfig.json`
Expected: 0 errors

- [ ] **Step 3: Start services and verify**

Run: `./init.sh restart`
Expected: All 3 services start. Check `./init.sh status`.

- [ ] **Step 4: Verify Navigator UI in browser**

Open `http://localhost:5199`. With no MCP connection (internal tier):
- Settings panel should show "Models" with opus/sonnet/haiku dropdowns
- Settings panel should show "Effort" with low/medium/high/max dropdowns

Toggle "Force IDE sampling" on:
- Settings panel should show "IDE Model" with "pending" for each phase
- No model or effort dropdowns visible

- [ ] **Step 5: Push**

```bash
git push origin main
```
