# Quality Feedback Loops + Session Resumption (H3)

**Date:** 2026-03-13
**Status:** Draft
**Scope:** Backend services, provider layer, database schema, frontend components, MCP tools

## Overview

Two convergent tracks that transform the pipeline from a stateless single-shot system into an adaptive, session-aware optimization engine with user feedback loops.

**Track A (Data Layer):** Feedback schema, internal diagnostics (dimension deltas, prompt diffs, per-instruction compliance, cycle detection), user feedback API, and an adaptation engine that tunes validator weights, strategy selection, retry thresholds, and optimizer context per-user.

**Track B (Session Layer):** H3 session resumption plumbing (persist session_id, expose in API), unified refinement service (auto-refinement replaces stateless retries, user-initiated refinement continues the same session), and parallel branching with pairwise preference capture.

**Convergence:** Refinement interactions feed the feedback table. Branch selection generates pairwise preferences (2x weight vs absolute ratings). The adaptation engine consumes both signals and influences the next pipeline run.

---

## 1. Data Model & Schema

### New Tables

#### `feedback`

User feedback on completed optimizations. One feedback per optimization per user (upsert semantics).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `optimization_id` | FK -> Optimization | What they're rating |
| `user_id` | Text | Who rated |
| `rating` | SmallInt | -1 (down), 0 (neutral), +1 (up) |
| `dimension_overrides` | JSON | `{clarity_score: 9, faithfulness_score: 4, ...}` -- only dimensions the user corrects |
| `corrected_issues` | JSON | `["issue the validator missed", ...]` -- user's view of what's actually wrong |
| `comment` | Text | Free-form note |
| `created_at` | DateTime | Timestamp |

**Indexes:** unique on `(optimization_id, user_id)`, composite on `(user_id, created_at)`.

#### `user_adaptation`

Learned preferences per user, materialized from accumulated feedback. Recomputed after each feedback submission.

| Column | Type | Purpose |
|--------|------|---------|
| `user_id` | Text | PK |
| `dimension_weights` | JSON | `{clarity: 0.22, faithfulness: 0.28, ...}` -- adjusted from default 20/20/15/25/20 |
| `strategy_affinities` | JSON | `{coding: {preferred: ["structured-output"], avoid: ["CO-STAR"]}, ...}` -- per task_type |
| `retry_threshold` | Float | Adjusted score threshold (default 5.0) |
| `feedback_count` | Int | How many feedbacks inform this state |
| `last_computed_at` | DateTime | When adaptation was last recomputed |

#### `refinement_branch`

First-class refinement branches. Every optimization gets a "trunk" branch on completion. User-initiated forks create child branches.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `optimization_id` | FK -> Optimization | Parent optimization |
| `parent_branch_id` | FK -> self (nullable) | Branch this was forked from (null = trunk) |
| `forked_at_turn` | Int (nullable) | Which turn of the parent this diverged from |
| `label` | Text | User-provided or auto-generated ("trunk", "concise-v1") |
| `optimized_prompt` | Text | Current prompt on this branch |
| `scores` | JSON | Current validation scores (all 5 dimensions + overall) |
| `session_context` | JSON | Serialized SessionContext for provider resumption |
| `turn_count` | Int | How many refinement turns on this branch |
| `turn_history` | JSON | `[{turn, source, message_summary, scores_before, scores_after, dimension_deltas, prompt_hash}]` |
| `status` | Text | `"active"` / `"selected"` / `"abandoned"` |
| `row_version` | Int | Optimistic locking (matches Optimization pattern) |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**Indexes:** `(optimization_id)`, `(optimization_id, status)`.

#### `pairwise_preference`

Recorded when a user selects a branch over others. Stronger adaptation signal than absolute ratings (2x weight).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `optimization_id` | FK -> Optimization | |
| `preferred_branch_id` | FK -> refinement_branch | Winner |
| `rejected_branch_id` | FK -> refinement_branch | Loser |
| `preferred_scores` | JSON | Scores at time of selection |
| `rejected_scores` | JSON | Scores at time of selection |
| `user_id` | Text | Who chose |
| `reason` | Text (nullable) | Optional rationale |
| `created_at` | DateTime | |

**Indexes:** `(user_id)`, `(optimization_id)`, `(user_id, created_at)` (for decay-ordered queries).

### Extensions to `Optimization` Model

| New Column | Type | Purpose |
|------------|------|---------|
| `retry_history` | JSON | `[{attempt, scores, focus_areas, dimension_deltas, prompt_hash}]` |
| `per_instruction_compliance` | JSON | `[{instruction, satisfied, note}]` |
| `session_id` | Text | H3 -- captured from provider for resumption |
| `refinement_turns` | Int | Total refinement turns across all branches |
| `active_branch_id` | Text (nullable) | Currently selected branch (app-layer referential integrity, no DB FK -- avoids circular dependency with refinement_branch) |
| `adaptation_snapshot` | JSON (nullable) | Exact weights/threshold/affinities used for this run (audit trail) |
| `branch_count` | Int | Quick count (avoids JOIN for display) |

**Invariant:** `optimization.optimized_prompt` always mirrors `active_branch.optimized_prompt`. The active branch is the source of truth; the optimization column is a denormalized convenience.

### Pydantic Schemas

```python
VALID_DIMENSIONS = {"clarity_score", "specificity_score", "structure_score",
                     "faithfulness_score", "conciseness_score"}

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
                    raise ValueError(f"Unknown dimension: {key}")
                if not 1 <= value <= 10:
                    raise ValueError(f"Score for {key} must be 1-10, got {value}")
        return self

class FeedbackResponse(BaseModel):
    id: str
    optimization_id: str
    rating: int
    dimension_overrides: dict[str, int] | None
    corrected_issues: list[str] | None
    comment: str | None
    created_at: datetime

class DimensionDelta(BaseModel):
    dimension: str
    previous: int
    current: int
    change: Literal["improved", "degraded", "unchanged"]

class RetryHistoryEntry(BaseModel):
    attempt: int
    scores: dict[str, float]
    focus_areas: list[str]
    dimension_deltas: list[DimensionDelta]
    prompt_hash: str

class InstructionCompliance(BaseModel):
    instruction: str
    satisfied: bool
    note: str | None = None

class RefinementRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    branch_id: str | None = None  # null = active branch
    protect_dimensions: list[str] | None = None

class ForkRequest(BaseModel):
    from_branch_id: str
    label: str | None = None  # auto-generated if omitted
    message: str = Field(..., min_length=1, max_length=2000)

class SelectRequest(BaseModel):
    branch_id: str
    reason: str | None = None
```

### Design Rationale

- **One feedback per optimization (upsert):** Prevents feedback spam, keeps adaptation stable. Users can revise their feedback.
- **`user_adaptation` is materialized, not computed on-the-fly:** Pipeline runs need adaptation data fast (it's in the hot path). One query by PK, sub-ms.
- **`retry_history` on Optimization, not a separate table:** Retry data is always read together with the optimization. JSON is write-once (set at pipeline completion).
- **`session_id` on Optimization:** Natural place -- one pipeline run = one session context.
- **Separate `refinement_branch` table:** Branches need independent querying, concurrent access, and tree traversal. JSON array on Optimization would be unmanageable.
- **`active_branch_id` as plain Text (no FK):** Avoids circular FK dependency between Optimization and refinement_branch. Adding an FK from Optimization → refinement_branch while refinement_branch already has an FK → Optimization creates a cycle that complicates table creation order, cascade behavior, and SQLite's limited ALTER TABLE support. Referential integrity enforced at application layer instead (branch CRUD validates the FK programmatically). Note: `retry_of` on Optimization *does* use a real FK since it's a self-referential (non-circular) relationship — different situation.
- **`row_version` on refinement_branch:** Consistent with Optimization's optimistic locking pattern. Prevents concurrent refine calls from overwriting each other.
- **User orphan handling:** Feedback records are retained if a user is removed (no CASCADE). The project uses soft-delete for optimizations; feedback is similarly preserved for historical adaptation accuracy.

---

## 2. Internal Diagnostics (System Feedback)

### 2a. Dimension Deltas

After each retry's validation, compute per-dimension changes server-side:

```
Retry 1: {clarity: 6, specificity: 5, structure: 7, faithfulness: 4, conciseness: 8}
Retry 2: {clarity: 7, specificity: 5, structure: 6, faithfulness: 6, conciseness: 7}
Deltas:  {clarity: +1 improved, specificity: 0 unchanged, structure: -1 degraded,
          faithfulness: +2 improved, conciseness: -1 degraded}
```

Computed in `pipeline.py` after each validation pass. Stored in `retry_history`. Emitted as `retry_diagnostics` SSE event.

### 2b. Prompt Diff Tracking

Each retry produces a new `optimized_prompt`. Track structural drift via normalized hash:

```python
def compute_prompt_hash(prompt: str) -> str:
    normalized = re.sub(r'\s+', ' ', prompt.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
```

**Cycle detection:** If retry N's `prompt_hash` matches any previous attempt (not just N-1), the optimizer is cycling (includes A->B->A oscillation). Abort retries immediately with `retry_cycle_detected` event.

### 2c. Per-Instruction Compliance

When user provides output instructions, the validator evaluates each individually:

```json
[
  {"instruction": "use formal tone", "satisfied": true, "note": null},
  {"instruction": "include code examples", "satisfied": false, "note": "No code blocks found"},
  {"instruction": "keep under 500 words", "satisfied": true, "note": "~320 words"}
]
```

Added to `ValidateOutput` as optional field. Unsatisfied instructions become highest-priority `focus_areas` for retries.

### 2d. Adaptive RetryOracle

Replaces the fixed-threshold retry logic with a stateful oracle that tracks five real-time signals across attempts within a single pipeline run.

**Module:** `backend/app/services/retry_oracle.py` (own module, not in `pipeline.py` -- the oracle is a complex stateful class that belongs in the service layer, matching the `strategy_selector.py` pattern).

#### Core Signals

**1. Score Momentum** -- is the trajectory improving or flattening?

Exponentially weighted moving delta with 0.7 decay factor. Recent attempts matter more. Positive = still gaining, near-zero = plateau, negative = regression.

**2. Dimension Elasticity** -- which dimensions respond to retries?

Per-dimension ratio of successful improvements when targeted vs total times targeted. High (>0.6) = optimizer can move it. Low (<0.3) = structurally constrained. Oracle stops targeting inelastic dimensions.

**3. Prompt Entropy** -- is the optimizer exploring or just rephrasing?

Jaccard similarity on sentence-level tokenization between consecutive attempts. High (>0.4) = genuinely different output. Low (<0.15) = cosmetic changes only (creative exhaustion).

**Configurable constants** (defined at module top, candidates for future user-configurability):

```python
ENTROPY_EXHAUSTION_THRESHOLD = 0.15    # below = creative exhaustion
ENTROPY_EXPLORATION_THRESHOLD = 0.40   # above = genuinely different
REGRESSION_RATIO_THRESHOLD = 0.40      # above = zero-sum trap
ELASTICITY_HIGH = 0.60                 # above = responsive to retries
ELASTICITY_LOW = 0.30                  # below = structurally constrained
FOCUS_EFFECTIVENESS_LOW = 0.30         # below = retargeting failing
MOMENTUM_NEGATIVE_THRESHOLD = -0.30    # below = getting worse
MOMENTUM_DECAY_FACTOR = 0.70           # exponential decay for weighting
DIMINISHING_RETURNS_BASE = 0.50        # base expected gain
DIMINISHING_RETURNS_GROWTH = 1.30      # growth factor per attempt
```

**4. Regression Ratio** -- are retries robbing Peter to pay Paul?

`dimensions_degraded / total_dimensions`. If >0.4 consistently across two consecutive retries, the prompt has a zero-sum tradeoff structure. Oracle switches to "accept best trade-off" mode.

**5. Focus Effectiveness** -- does targeting a dimension actually move it?

`dimensions_improved_in_focus / len(focus_areas)`. Below 0.3 across two consecutive retries triggers escalation: unconstrained retry (empty focus_areas) to let the optimizer find its own path.

#### Adaptive Thresholds

Three calibration sources (priority order):

1. **User adaptation** (`user_adaptation.retry_threshold`): personal threshold learned from feedback patterns.
2. **Task-type baseline** (historical median for this task_type): don't retry if within 1.5 of typical scores.
3. **Diminishing returns decay**: `min_expected_gain = 0.5 * 1.3^attempt`. Rising bar each retry.

#### Decision Algorithm (7 Gates)

```python
class RetryOracle:
    def should_retry(self) -> RetryDecision:
        # Gate 1: Score >= adapted threshold? → ACCEPT
        # Gate 2: Budget exhausted? → ACCEPT_BEST
        # Gate 3: Cycle detected (hash collision)? → ACCEPT_BEST
        # Gate 4: Creative exhaustion (entropy < 0.15)? → ACCEPT_BEST
        # Gate 5: Negative momentum (< -0.3)? → ACCEPT_BEST
        # Gate 6: Zero-sum trap (two consecutive regression_ratio > 0.4)? → ACCEPT_BEST
        # Gate 7: Diminishing returns (expected_gain < rising threshold)? → ACCEPT_BEST
        # All gates passed → RETRY with adaptive focus selection
```

#### Best-of-N Selection

The pipeline no longer returns the latest retry result. `ACCEPT_BEST` returns the attempt with the highest user-weighted overall score across ALL attempts. Prevents the pathological case where a later retry scores worse.

#### Focus Selection

```python
def _select_focus(self) -> list[str]:
    # If last two retries had low focus effectiveness → go unconstrained []
    # Otherwise: unsatisfied instructions (highest priority)
    #          + lowest-scoring dimensions with high elasticity
    #          + exclude inelastic and recently-degraded dimensions
```

#### SSE Events

| Event | When | Payload |
|-------|------|---------|
| `retry_diagnostics` | Before each retry | `{attempt, momentum, entropy, regression_ratio, elasticity, focus_areas, expected_gain, decision_reason}` |
| `retry_cycle_detected` | Prompt hash collision | `{attempt, matching_attempt, prompt_hash}` |
| `retry_exhaustion` | Entropy below threshold | `{attempt, entropy, similarity_to_previous}` |
| `retry_best_selected` | Oracle chose non-latest attempt | `{selected_attempt, selected_score, latest_attempt, latest_score, reason}` |
| `instruction_compliance` | After validation (when instructions present) | `[{instruction, satisfied, note}]` |

---

## 3. User Feedback API

### Endpoints

**`POST /api/optimize/{id}/feedback`** -- submit or update feedback.

Upsert semantics: second submission for same `optimization_id + user_id` replaces the first. Returns 201 on create, 200 on update. Rate limit: `Depends(RateLimit(lambda: "10/minute"))` -- tighter than reads because it triggers background adaptation recomputation.

**`GET /api/optimize/{id}/feedback`** -- feedback for an optimization.

Returns `{feedback: {...} | null, aggregate: {total_ratings, positive, negative, neutral, avg_dimension_overrides}}`. Aggregate computed via single GROUP BY query. Rate limit: `Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))`.

**`GET /api/feedback/history`** -- paginated feedback history for current user.

Query params: `offset`, `limit`, `rating_filter`, `sort`. Standard pagination envelope. Rate limit: `Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))`.

**`GET /api/feedback/stats`** -- user's feedback patterns + adaptation transparency.

Returns `{total_feedbacks, rating_distribution, avg_override_delta, most_corrected_dimension, adaptation_state}`. Rate limit: `Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))`.

### Adaptation Trigger

Feedback submission triggers async `recompute_adaptation()` as a background task. **Concurrency guard:** A per-user asyncio.Lock (keyed by user_id in a module-level WeakValueDictionary) prevents concurrent recomputation. If a lock is already held for a user_id, the new request skips recomputation (the in-flight one will pick up the latest feedback):

```python
async def recompute_adaptation(user_id: str, db: AsyncSession):
    feedbacks = await get_all_feedbacks_for_user(user_id, db)
    preferences = await get_all_preferences_for_user(user_id, db)

    if len(feedbacks) < 3:
        return  # not enough signal

    # 1. Dimension weight adjustment
    override_deltas = compute_override_deltas(feedbacks)
    adjusted_weights = adjust_weights_from_deltas(
        base_weights=DEFAULT_WEIGHTS,
        deltas=override_deltas,
        damping=0.15,       # max 15% shift per dimension
        min_samples=3,      # need 3+ overrides for a dimension to count
    )

    # 2. Retry threshold adjustment
    threshold = compute_threshold_from_feedback(
        feedbacks, default=5.0, bounds=(3.0, 8.0),
    )

    # 3. Strategy affinities
    affinities = compute_strategy_affinities(
        feedbacks, min_samples=2, decay_days=90,
    )

    # 4. Pairwise preference integration (2x weight)
    if preferences:
        pairwise_weight_signal = compute_pairwise_dimension_signal(preferences)
        pairwise_strategy_signal = compute_pairwise_strategy_signal(preferences)
        adjusted_weights = merge_signals(
            feedback_signal=override_deltas,        # weight: 1.0
            pairwise_signal=pairwise_weight_signal,  # weight: 2.0
            damping=0.15,
        )
        affinities = merge_strategy_signals(
            feedback_affinities=affinities,           # weight: 1.0
            pairwise_affinities=pairwise_strategy_signal,  # weight: 2.0
            decay_days=90,
        )

    await upsert_user_adaptation(user_id, db, UserAdaptation(
        dimension_weights=adjusted_weights,
        retry_threshold=threshold,
        strategy_affinities=affinities,
        feedback_count=len(feedbacks),
        last_computed_at=utcnow(),
    ))
```

### Safety Rails

- **Damped weight adjustment (max 15% shift):** Prevents a few strong opinions from wildly skewing dimensions.
- **Minimum sample thresholds:** 3 feedbacks before adaptation kicks in, 2 per task_type+framework pair for strategy affinities.
- **90-day decay on strategy affinities:** User preferences evolve.
- **Threshold bounds (3.0-8.0):** Below 3.0 = "never retry" (dangerous). Above 8.0 = "always retry" (wasteful).
- **Weight bounds [0.05, 0.40]:** No dimension can be zeroed out or dominate.
- **Weight normalization invariant:** Always re-normalized to sum to 1.0 at computation time.

---

## 4. Adaptation Engine

Four integration points where feedback becomes pipeline behavior.

### Integration Point 1: Validator Weight Tuning

**Where:** `validator.py` -> `compute_overall_score()`

Default weights (clarity 0.20, specificity 0.20, structure 0.15, faithfulness 0.25, conciseness 0.20) are replaced by user-adapted weights when available. A user who consistently rates faithfulness lower than the validator sees faithfulness weighted higher -- the pipeline self-corrects toward what the user actually values.

### Integration Point 2: Strategy Selection Bias

**Where:** `strategy.py` -> strategy prompt construction

When `user_adaptation.strategy_affinities` has data for the current `task_type`, soft hints are injected into the strategy prompt:

- Preferred frameworks: "This user has historically responded well to {frameworks} for {task_type} tasks."
- Avoided frameworks: "This user has given negative feedback when {frameworks} was applied."

This is a soft signal, not a hard override. The LLM can still pick a "disliked" framework if it's clearly the best fit.

### Integration Point 3: RetryOracle Calibration

**Where:** `RetryOracle.__init__()` in `retry_oracle.py` (instantiated by `pipeline.py`)

Three parameters from adaptation: `threshold` (personal retry trigger), `user_weights` (for best-attempt scoring), `task_baseline` (task-type calibration).

### Integration Point 4: Optimizer Retry Context

**Where:** `optimizer.py` -> retry prompt construction

Retry context enriched with:
- `user_priority_dimensions`: top 2 dimensions by user weight
- `dimension_elasticity`: which dimensions respond to retries
- `approach_hint`: if momentum is low, instruct optimizer to try a structurally different approach

### Loading

Adaptation loaded once per pipeline run (single DB query by PK). Passed to stages that need it. If `user_adaptation` is `None` (new user, <3 feedbacks), every integration point falls through to defaults.

### Audit Trail

Every pipeline run stores the exact weights/threshold/affinities used in the `adaptation_snapshot` column on the Optimization model (separate JSON column, not mixed into `stage_durations`). This preserves reproducibility even if adaptation changes later, and avoids schema pollution of the timing-focused `stage_durations` blob.

---

## 5. Unified Refinement Architecture

### Core Insight

Auto-refinement (oracle-driven retries) and user refinement are the same operation -- a session-aware turn producing a revised prompt based on feedback. One code path, one service, three entry points:

| Trigger | Source | Message content |
|---------|--------|----------------|
| Auto-retry | RetryOracle | Diagnostic feedback (dimension deltas, elasticity, focus areas) |
| User refinement | Human | Natural language instruction ("make it shorter") |
| Fork | Human | Instruction applied to a copy of an existing branch's session |

### Provider-Level Session Abstraction

```python
class SessionContext:
    session_id: str | None = None          # CLI: SDK session ID for resume
    message_history: list[dict] | None = None  # API: conversation turns for replay
    provider_type: str = ""                # "claude_cli" | "anthropic_api"
    created_at: datetime
    turn_count: int = 0
```

**CLI provider:** Uses `ClaudeAgentOptions(resume=session_id)`. SDK preserves context server-side. We only store the session_id string (~50 bytes).

**API provider:** No native session resumption. Stores full message_history and replays as prefix on next call. Capped at 10 turns with Haiku-based compaction (summarize old turns, keep last 4 pairs).

### Base Class Addition

```python
# Added to LLMProvider as concrete method with default implementation
# NOT @abstractmethod — avoids breaking MockProvider and all existing providers
async def complete_with_session(
    self, system: str, user: str, model: str,
    session: SessionContext | None = None,
    schema: dict | None = None,
) -> tuple[str, SessionContext]:
    """Completion with session continuity. Returns (response, updated_session).

    Default implementation: delegates to complete(), returns a fresh SessionContext.
    AnthropicAPIProvider and ClaudeCLIProvider override with session-aware behavior.
    MockProvider inherits the default (sufficient for testing).
    """
    if schema:
        response = await self.complete_json(system, user, model, schema)
        text = json.dumps(response)
    else:
        text = await self.complete(system, user, model)
    new_session = SessionContext(
        provider_type=self.name,
        created_at=session.created_at if session else utcnow(),
        turn_count=(session.turn_count + 1) if session else 1,
    )
    return text, new_session
```

`AnthropicAPIProvider` and `ClaudeCLIProvider` override this with their session-specific implementations (message history replay and SDK resume respectively). `MockProvider` inherits the default — no changes needed for tests to pass.

Existing methods (`complete`, `stream`, `complete_json`, `complete_parsed`, `complete_agentic`) remain unchanged.

### Session Compaction

Triggered by **either** turn count or byte size, whichever threshold is hit first:

```python
MAX_REFINEMENT_TURNS = 10
MAX_SESSION_CONTEXT_BYTES = 256_000  # 256KB hard cap per branch

async def compact_session(session: SessionContext, provider) -> SessionContext:
    needs_compaction = (
        session.turn_count > MAX_REFINEMENT_TURNS
        or len(json.dumps(session.message_history or [])) > MAX_SESSION_CONTEXT_BYTES
    )
    if not needs_compaction:
        return session

    # Keep system message + summary of old turns + last 4 turn pairs
    # Summary generated by Haiku 4.5 (cheap, fast, max 500 output tokens)
    old_turns = session.message_history[1:-8]  # skip system, keep last 4 pairs
    try:
        summary = await provider.complete(
            system="Summarize this conversation concisely, preserving all decisions and constraints.",
            user=json.dumps(old_turns),
            model="claude-haiku-4-5",
        )
    except Exception:
        # Compaction failure: continue with uncompacted session, log warning
        logger.warning("Session compaction failed, continuing with full history")
        return session

    compacted_history = [
        session.message_history[0],  # system
        {"role": "user", "content": f"[Previous refinement context]\n{summary}"},
        {"role": "assistant", "content": "Understood. I have the full context."},
        *session.message_history[-8:],  # last 4 turn pairs
    ]
    return SessionContext(
        session_id=session.session_id,
        message_history=compacted_history,
        provider_type=session.provider_type,
        created_at=session.created_at,
        turn_count=session.turn_count,
    )
```

**Storage expectations:** CLI sessions store only a session_id string (~50 bytes per branch). API sessions grow ~30-80KB per turn (system prompt + optimizer output). With compaction at 256KB, worst case per optimization: 5 branches x 256KB = 1.28MB. Abandoned branch cleanup clears `session_context` at 7 days.

### Unified Refine Operation

```python
async def refine(
    branch_id: str,
    message: str,
    source: Literal["auto", "user"],
    protect_dimensions: list[str] | None,
    provider: LLMProvider,
    user_adaptation: UserAdaptation | None,
    db: AsyncSession,
) -> AsyncGenerator[dict, None]:
    """One refinement turn on a branch. Used by both auto-retry and user refinement."""
    # 1. Load branch + session
    # 2. Build refinement prompt (same structure regardless of source)
    # 3. complete_with_session() -- session-aware call
    # 4. Extract refined prompt
    # 5. Validate refined output
    # 6. Compact session if needed
    # 7. Update branch (prompt, scores, session, turn_history)
    # 8. Sync to optimization if active branch
    # Yields SSE events throughout
```

### Auto-Refinement (RetryOracle Integration)

The retry loop in `pipeline.py` now uses the unified refine operation:

```python
trunk = await create_branch(optimization_id, "trunk", initial_prompt, initial_scores)
oracle = RetryOracle(user_adaptation, task_type, max_retries)
oracle.record_attempt(initial_scores, initial_prompt, [])

while True:
    decision = oracle.should_retry()
    if decision.action in ("accept", "accept_best"):
        if decision.action == "accept_best" and oracle.best_attempt differs:
            await revert_branch_to_attempt(trunk, oracle.best_attempt)
        break
    # decision.action == "retry"
    diagnostic_message = oracle.build_diagnostic_message(decision.focus_areas)
    async for event in refine(trunk.id, diagnostic_message, source="auto", ...):
        yield event
    trunk = await get_branch(trunk.id, db)
    oracle.record_attempt(trunk.scores, trunk.optimized_prompt, decision.focus_areas)
```

Key changes from old retry loop:
- `provider.stream()` -> `refine()` (session-aware)
- Fresh context each retry -> accumulated conversational history
- Oracle diagnostics as prompt injection -> oracle diagnostics as conversational turn
- Latest-wins -> best-of-N via oracle selection

### Branching Operations

**Fork:** Deep-copies session context from parent branch. API provider: true deep-copy of message_history. CLI provider: SDK sessions can't be forked server-side, so the fork starts with `session_context=None`. The first refinement turn on the forked branch gets a context preamble generated by Haiku 4.5 (max 500 output tokens) summarizing the parent branch's turn history. This summary is injected as the first user message in the new session. On summary generation failure: proceed without context summary (log warning); the optimizer will lack parent context but the fork remains functional. The summary is consumed on the first turn and not stored permanently — subsequent turns build their own session context from that point.

**Select:** Updates optimization's active_branch_id and syncs prompt+scores. Marks non-selected branches as abandoned. Records N-1 pairwise preferences. Triggers adaptation recomputation.

**Limits:** Max 5 branches per optimization. Max 3 active simultaneously. Max 10 turns per branch. Abandoned branches cleaned up after 7 days (session_context cleared, metadata retained for preference history).

### Lifecycle

```
Pipeline run -> trunk created -> auto-refinement (oracle retries on trunk)
    -> pipeline complete
    -> user can: feedback (thumbs/overrides)
                 refine (continue trunk session)
                 fork (new branch from any branch)
                 compare (side-by-side diff + scores)
                 select (pick winner, generate pairwise preferences)
```

### API Endpoints

```
POST /api/optimize/{id}/refine          -- SSE stream, one refinement turn
POST /api/optimize/{id}/branches        -- SSE stream, fork + first turn
GET  /api/optimize/{id}/branches        -- list all branches
GET  /api/optimize/{id}/branches/compare?branch_a={id}&branch_b={id}
POST /api/optimize/{id}/branches/select -- pick winner
GET  /api/optimize/{id}/branches/{branch_id}  -- branch detail + turn history
```

**Rate limits:**
- `POST .../refine`: `Depends(RateLimit(lambda: "5/minute"))` -- triggers Opus-level LLM calls
- `POST .../branches` (fork): `Depends(RateLimit(lambda: "3/minute"))` -- creates branch + LLM call
- `POST .../branches/select`: `Depends(RateLimit(lambda: "10/minute"))` -- DB-only, lighter
- `GET` endpoints: `Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))` -- standard read rate

**SSE streaming:** Both `POST .../refine` and `POST .../branches` return `StreamingResponse(media_type="text/event-stream")`. Events use the same `_sse_event()` formatter extracted to `backend/app/routers/_sse.py` (shared with `optimize.py`). Terminal event: `refinement_complete` with final scores and prompt. Error events follow the existing `{stage, error, recoverable}` pattern.

**Router:** New file `backend/app/routers/refinement.py`. Mounted under `/api/optimize` prefix. All endpoints require authenticated user.

---

## 6. Frontend Design

### Chromatic Encoding (New Semantic Assignments)

| Concept | Token | Hex | Rationale |
|---------|-------|-----|-----------|
| Feedback positive | neon-green | #22ff88 | Success semantic |
| Feedback negative | neon-red | #ff3366 | Danger semantic |
| Score delta positive | neon-green | #22ff88 | Improvement = health |
| Score delta negative | neon-orange | #ff8c00 | Degradation = attention (not danger) |
| Score delta neutral | text-dim | #7a7a9e | No signal = no color |
| Auto-refinement turn | neon-indigo | #7b61ff | Reasoning -- oracle thinking |
| User refinement turn | neon-cyan | #00e5ff | Primary action -- user is agent |
| Branch active | neon-cyan | #00e5ff | Primary identity |
| Branch selected | neon-green | #22ff88 | Success -- winner |
| Branch abandoned | text-dim | #7a7a9e | Inactive = no emission |
| Protected dimension | neon-teal | #00d4aa | Extraction/preservation |
| Adaptation state | neon-purple | #a855f7 | Processed/elevated |
| Oracle diagnostics | neon-yellow | #fbbf24 | Alchemical fire |
| Pairwise preference | neon-pink | #ff6eb4 | Creativity -- choosing between possibilities |

### Forge Motion Mapping

| Interaction | Forge Stage | Keyframe |
|-------------|-------------|----------|
| Feedback submit | Validate (testing temper) | `copy-flash` (200ms) |
| Refinement turn | Optimize (shaping under pressure) | `scale-in` (400ms) |
| Branch fork | Strategy (selecting tool) | `slide-in-right` (300ms) |
| Branch select | Validate (testing temper) | `copy-flash` + `fade-in` |
| Score delta reveal | Validate | `stagger-fade-in` (300ms, 50ms stagger) |
| Oracle diagnostics | Analyze (heating metal) | `slide-up-in` (300ms) |

### Component Architecture

```
src/lib/components/
  editor/
    FeedbackInline.svelte        -- 32px strip: thumbs + dimension badges + refine trigger
    RefinementInput.svelte       -- expandable input well + turn history
  pipeline/
    BranchIndicator.svelte       -- compact branch label + switch dropdown
    BranchCompare.svelte         -- full overlay: DiffView + score table + timeline
    RetryDiagnostics.svelte      -- oracle signal visualization (inside StageCard)
  layout/
    InspectorFeedback.svelte     -- full feedback panel (section in Inspector)
    InspectorRefinement.svelte   -- turn history + session state
    InspectorBranches.svelte     -- branch tree + compare + select
    InspectorAdaptation.svelte   -- learned weights transparency
```

### Stores

Two class-based stores matching existing patterns (forge.svelte.ts, editor.svelte.ts):

**`feedback.svelte.ts`** — feedback + adaptation state. State: `currentFeedback`, `submitting`, `aggregate`, `adaptationState`. Handles absolute rating lifecycle.

**`refinement.svelte.ts`** — branches + refinement sessions. State: `refinementOpen`, `refinementStreaming`, `protectedDimensions`, `branches`, `activeBranchId`, `comparingBranches`. Derived: `activeBranch`, `activeBranchTurns`, `branchTree`. Handles new SSE events via `handleRefinementEvent()` method.

This split mirrors the existing separation between `forge.svelte.ts` (pipeline) and `editor.svelte.ts` (prompt editing).

### FeedbackInline.svelte -- Compact Strip (32px)

Single row below optimized prompt. Elements:

- **Thumbs:** Two `btn-icon` variants (16px icon, 28px hit target). Active up: neon-green 1px border + 8% fill. Active down: neon-red. Submit triggers `copy-flash`.
- **Dimension chips:** `chip-rect` pattern (4px radius), font-mono 10px, 3-letter abbreviation + score. Background/border from `getScoreColor()`. Click opens 1-10 stepper. Override state: border shifts to neon-purple at 30%.
- **Overall score:** `ScoreCircle` (20px), existing component.
- **Refine button:** `btn-ghost`. Click expands `RefinementInput` with `slide-up-in` (300ms).
- **Branch indicator:** `badge-sm` pill, font-mono 9px. Label + turn count. Click opens Inspector branches.

### RefinementInput.svelte -- Expandable Well

Expands below FeedbackInline. Entry: `slide-up-in` (300ms). Exit: accelerating 200ms.

- **Protected dimensions:** Syne 10px uppercase label. Chips in neon-teal (20% bg, 35% border).
- **Input well:** `bg-bg-input`, standard focus ring. Disabled during streaming: sharp border-color oscillation (NOT a glow) between cyan 20% and 50% at 1.5s period.
- **Turn history:** Reverse chronological, 20px per turn, font-mono 10px. User turns in neon-cyan, auto turns in neon-indigo. Score deltas: neon-green positive, neon-orange negative. Max 5 visible, scrollable.

### BranchIndicator.svelte -- Compact Control

Single branch: simple `badge-sm`. Multiple branches: dropdown trigger with tree view, fork button, compare button. Dropdown: `bg-bg-card`, z-index 100 (popover layer), `scale-in` entry. Branch rows 24px: active = cyan dot, selected = green dot, abandoned = dim dot. Tree connectors via monospace characters.

### InspectorFeedback.svelte -- Full Panel

Inspector sidebar section. Elements: verdict thumbs (32px btn-icon pair), dimension overrides (ScoreBar + stepper, modified state in neon-purple), issue correction (accept/reject toggles + user additions with neon-pink left accent), comment textarea, save button (btn-outline-primary, copy-flash on submit).

### InspectorBranches.svelte -- Branch Management

Branch tree with monospace labels, connectors, score + turn count. Fork button (btn-outline-secondary). Compare dropdowns + button. Select winner with radio buttons + reason input. Selection triggers copy-flash on winner, abandoned branches fade to text-dim over 300ms.

### BranchCompare.svelte -- Full Overlay

Modal overlay (z-index 50, glass bg, 8px blur). Entry: `dialog-in` (300ms). Contains:

- **Score table:** font-mono, tabular figures. Per-dimension with delta column (neon-green positive, neon-orange negative). Overall row with ScoreCircle pair.
- **Prompt diff:** Direct reuse of existing `DiffView.svelte` -- zero new diff code.
- **Turn timeline:** Horizontal timeline (12px nodes, 2px connectors). Auto=indigo, user=cyan, fork=pink nodes.
- **Select buttons:** Higher-scoring branch gets btn-outline-primary (visual recommendation).

### InspectorAdaptation.svelte -- Transparency

Visible when adaptation state exists (3+ feedbacks). Weight bars (neon-purple fill, max 40%), delta from default in neon-green/neon-orange. Strategy affinities grouped by task_type: preferred in neon-green + strategy chromatic color, avoided in neon-red + strikethrough. Retry threshold displayed with explanatory text. Reset button (btn-outline-danger) with inline confirmation.

### RetryDiagnostics.svelte -- Oracle Visualization

Renders inside Validate StageCard during auto-retry. Oracle signal bars (neon-yellow fill, except regression which uses inverted scale). Elasticity mini-bars (40px wide, 4px height). Focus areas as chip-rects. Decision line: neon-yellow for RETRY, neon-green for ACCEPT.

### Accessibility

All components follow the 5-state interactive lifecycle. Focus rings: 1px cyan at 30%, 2px offset. `prefers-reduced-motion`: all animations to 0.01ms. Screen reader labels on all interactive elements. WCAG AAA contrast on primary text, AA minimum on accents. Full keyboard navigation.

---

## 7. Testing Strategy

### Test Taxonomy

| Layer | Count | Speed | What it catches |
|-------|-------|-------|----------------|
| Unit | 98 | <1s each | Logic errors in isolated functions |
| Property-based | 12 | <5s each | Invariant violations across input space |
| Integration | 34 | <10s each | Component interaction, DB state |
| Contract | 18 | <2s each | API schema drift, SSE event structure |
| Resilience | 14 | <15s each | Graceful degradation under failure |
| E2E | 8 | <30s each | Full user flow regressions |

**Total: 184 tests**

### Unit Tests (98)

**test_retry_oracle.py (32):** All 7 gates with boundary conditions, momentum/elasticity/entropy edge cases, focus selection logic, best-of-N tracking.

**test_adaptation_engine.py (26):** Weight sum-to-one invariant, damping caps, bound enforcement, threshold calibration, strategy affinities with decay, pairwise preference signal merging with 2x weight.

**test_feedback_service.py (14):** CRUD operations, upsert semantics, aggregate computation, pagination, stats.

**test_refinement_service.py (16):** Unified refine for both sources, branch state updates, turn history, session creation/resumption/compaction, prompt extraction edge cases.

**test_branch_operations.py (10):** Trunk creation, fork with deep-copy (API) and fresh-start (CLI), selection with pairwise preferences.

### Property-Based Tests (12)

Using Hypothesis to fuzz critical invariants:

- Weights always sum to 1.0 (any random overrides)
- All weights within [0.05, 0.40] (any random overrides)
- Max shift from default <= 0.15 (damping invariant)
- Threshold within [3.0, 8.0] (any random feedbacks)
- Best attempt score >= minimum of all attempts
- Momentum bounded (never infinite)
- Entropy between 0.0 and 1.0 (any random prompts)
- Entropy exactly 0.0 for identical prompts
- Regression ratio between 0.0 and 1.0
- Session serialization round-trips correctly
- Overall score within dimension min/max range
- Adapted score still in [1.0, 10.0] range

### Integration Tests (34)

**test_feedback_integration.py (10):** API round-trips with real DB, aggregate across users, adaptation trigger verification, rate limiting.

**test_pipeline_integration.py (14):** Branch lifecycle through pipeline, auto-retry with session continuity, full adaptation cycle (pipeline -> feedback -> adaptation -> next pipeline), accumulator backward compatibility.

**test_branch_integration.py (10):** Fork-refine-select flow, parent branch isolation, pairwise preferences feeding adaptation, limit enforcement, concurrent access via row_version.

### Contract Tests (18)

**test_api_contracts.py (10):** Response shape assertions for all new endpoints, pagination envelope compliance.

**test_sse_contracts.py (8):** Event structure validation for all new SSE events (retry_diagnostics, retry_cycle_detected, retry_exhaustion, retry_best_selected, instruction_compliance, refinement_started, refinement_validated, refinement_complete).

### Resilience Tests (14)

Provider failures (timeout, rate limit, 500 error), session failures (expired CLI session, corrupted API history, compaction failure), database failures (write error, version conflict, adaptation compute error), concurrent access (two refine calls, select during refinement), edge cases (feedback on deleted optimization, refine on abandoned branch).

### E2E Tests (8)

Full user flows with mock provider:

1. Optimize -> feedback -> adaptation -> adapted pipeline
2. Optimize -> refine loop (2 turns, session persisted)
3. Branch fork -> compare -> select winner
4. Auto-retry with oracle diagnostics events
5. Full adaptation feedback loop (5 optimizations, feedback, pairwise, adapted run)
6. Instruction compliance tracking end-to-end
7. Branch limit enforcement
8. Graceful degradation with zero feedbacks (pre-feature behavior preserved)

### Frontend Tests (28)

**Component tests (18):** FeedbackInline (thumbs, dimension overrides, refine expand), RefinementInput (submit, streaming state, protected dimensions, turn history), BranchIndicator (single vs multi, dropdown behavior).

**Store tests (10):** SSE event dispatch, branch tree derivation, active branch tracking, protected dimensions persistence, unknown event handling.

### Coverage Targets

| Module | Target | Hard floor |
|--------|--------|-----------|
| retry_oracle.py | 98% | 95% |
| adaptation_engine.py | 98% | 95% |
| refinement_service.py | 92% | 88% |
| branch_operations.py | 92% | 88% |
| feedback router | 90% | 85% |
| session context | 90% | 85% |
| feedback store (TS) | 85% | 80% |
| **Overall new code** | **93%** | **90%** |

### Test Fixtures

Shared factories: `oracle_factory` (build oracle with configurable score history), `feedback_factory` (create feedback records), `branch_factory` (create branches), `adaptation_factory` (build adaptation from feedback specs). Hypothesis strategies: `feedback_strategy()`, `valid_weight_strategy()`.

**Hypothesis configuration:** All property-based tests use `@settings(max_examples=200)` for CI predictability. A `ci` profile caps at 200 examples; a `dev` profile allows 1000 for local fuzzing:

```python
settings.register_profile("ci", max_examples=200, deadline=5000)
settings.register_profile("dev", max_examples=1000, deadline=10000)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
```

---

## 8. Migration & Rollout

### Database Migrations

The project uses no Alembic. Migrations are handled via:
- **New tables:** Defined as SQLAlchemy models and created by `Base.metadata.create_all()` on startup.
- **New columns on existing tables:** Added to the `_new_columns` dict in `database.py`'s `_migrate_add_missing_columns()`.
- **New indexes:** Added to `_migrate_add_missing_indexes()`.

**New model files:**
- `backend/app/models/feedback.py` — `Feedback`, `UserAdaptation` ORM models
- `backend/app/models/branch.py` — `RefinementBranch`, `PairwisePreference` ORM models

**New columns on `optimization` table** (added to `_new_columns` dict):

```python
_new_columns = {
    # ... existing entries ...
    "retry_history": "TEXT",
    "per_instruction_compliance": "TEXT",
    "session_id": "TEXT",
    "refinement_turns": "INTEGER DEFAULT 0",
    "active_branch_id": "TEXT",
    "branch_count": "INTEGER DEFAULT 0",
    "adaptation_snapshot": "TEXT",
}
```

**New indexes** (appended to `_new_indexes` list in `_migrate_add_missing_indexes`):

```python
# Existing format: list[tuple[str, str, str]]  →  (index_name, table_name, column_spec)
_new_indexes: list[tuple[str, str, str]] = [
    # ... existing entries ...
    ("ix_feedback_user_created", "feedback", "user_id, created_at"),
    ("ix_branch_optimization", "refinement_branch", "optimization_id"),
    ("ix_branch_opt_status", "refinement_branch", "optimization_id, status"),
    ("ix_pairwise_user", "pairwise_preference", "user_id"),
    ("ix_pairwise_optimization", "pairwise_preference", "optimization_id"),
    ("ix_pairwise_user_created", "pairwise_preference", "user_id, created_at"),
]
```

**Unique constraint for `(optimization_id, user_id)` on `feedback`:** The existing `_migrate_add_missing_indexes` only supports `CREATE INDEX`. The unique constraint is defined on the ORM model via `UniqueConstraint("optimization_id", "user_id")` in `__table_args__` and created by `Base.metadata.create_all()` for new databases. For existing databases that predate the feedback table, `create_all()` creates the table with the constraint already in place — no ALTER needed since the table is new.

All new columns are nullable. No data backfill needed. SQLite compatible (plain ALTER TABLE ADD COLUMN, no FK constraints).

### Backward Compatibility

- Existing optimizations: `active_branch_id = NULL`, `branch_count = 0`. All endpoints return identical responses -- new fields are optional.
- Branch-aware code guards: first refine on a branchless optimization auto-creates trunk.
- SSE backward compatibility: new events are additive; clients that don't handle them ignore naturally per SSE spec.
- API backward compatibility: all new endpoints on new paths. Existing endpoint responses gain optional fields only.

### Sort Whitelist Extension

`refinement_turns` and `branch_count` added to `VALID_SORT_COLUMNS` in `optimization_service.py` (single source of truth -- `history.py` and `mcp_server.py` import `validate_sort_params` from there, not maintain separate whitelists).

Note: The CLAUDE.md documentation incorrectly states the whitelist exists in three files. A follow-up task should correct CLAUDE.md to reflect the single-source pattern.

### MCP Server Extensions

Three new tools: `submit_feedback`, `get_branches`, `get_adaptation_state`. Each with full annotations per CLAUDE.md convention.

### Rollout Phases

**Phase 1 -- Data layer (no user-facing changes):**
Run migrations. Pipeline populates retry_history, instruction_compliance, session_id. RetryOracle replaces old retry logic. Trunk branches auto-created.

**Phase 2 -- Feedback API:**
Deploy feedback + adaptation endpoints. MCP tools available. No frontend changes.

**Phase 3 -- Frontend inline feedback:**
Deploy FeedbackInline, RefinementInput, RetryDiagnostics. Users can rate, override, refine. Adaptation accumulates.

**Phase 4 -- Adaptation activation:**
Enable adaptation injection into pipeline stages. InspectorAdaptation deployed for transparency.

**Phase 5 -- Branching:**
Deploy BranchIndicator, InspectorBranches, BranchCompare. Fork/compare/select flows. Pairwise preferences feed adaptation.

### Rollback Strategy

Each phase independently reversible:

| Phase | Rollback | Data impact |
|-------|----------|-------------|
| 1 | Revert to old retry logic. New columns ignored. | Orphaned data, harmless |
| 2 | Remove endpoints. Tables remain unused. | Feedbacks preserved |
| 3 | Revert frontend. API still available. | Zero data loss |
| 4 | Set adaptation=None in pipeline. Defaults resume. | Data preserved, unused |
| 5 | Hide branch UI. Trunk still created, branching disabled. | Branches preserved |

No migration downgrades needed -- all changes are additive and nullable.

### Performance

| Concern | Mitigation |
|---------|-----------|
| Adaptation lookup | Single PK query, sub-ms |
| Feedback aggregate | Single GROUP BY, typically <10 rows |
| Session storage (API) | Capped at 256KB per branch via size+turn-based compaction. CLI stores ~50 bytes. Worst case: 5 branches x 256KB = 1.28MB per optimization. Abandoned cleanup at 7 days. |
| Branch proliferation | Max 5 per optimization, cleanup at 7 days |
| Pairwise growth | Max 10 per optimization (5 branches, C(5,2)) |
| RetryOracle | In-memory arrays (max 5 entries), sub-ms |
| Adaptation recompute | Background, <500ms for <100 feedbacks |
