"""Refinement context dataclasses — frozen snapshots used by Foundation P4 Cycle 2.

Eliminates detached-ORM access after the read session closes. Every ORM field
consumed by the LLM phase OR the persist callback is captured here as a plain
scalar before the `async with async_session_factory()` block exits.

Cycle 2 RED test 5 (AST-based) pins the contract: no `ast.Attribute` access on
`Name(id in ('opt', 'latest_turn', 'branch', 'optimization', 'prev_turn'))` is
allowed after the read-session block exit line in `tools/refine.py`. The same
test enforces zero `self.db` references in `invoke_refinement_pipeline` body.

Field scoping rule: include ONLY fields needed by post-`_load_*` code paths.
Do not over-specify "every attribute that exists on the ORM model" — the
snapshot dataclass is a contract, not a mirror.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.services.context_enrichment import EnrichedContext


# ---------------------------------------------------------------------------
# ORM-row snapshot dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OptSnapshot:
    """Frozen snapshot of Optimization fields read post-`_load_optimization`.

    Field set (5) — every entry justified by a post-snapshot consumer:
    - id: persist callbacks build `optimization_id=ctx.opt_snapshot.id`
    - raw_prompt: LLM step consumes at refinement_service.py:204
    - optimized_prompt: enrich() + build_initial_turn_payload consume at refine.py:78,126
    - strategy_used: build_initial_turn_payload consumes at refine.py:80
    - project_id: enrich(project_id=...) consumes at refine.py:131

    Excluded fields (round-3 negative-assertion citations):
    - `status`: read pre-LLM at refine.py:62 inside the read-session error-message
      path; zero post-LLM accesses. Not in snapshot.
    - Score fields (score_clarity, score_specificity, score_structure,
      score_faithfulness, score_conciseness): computed via build_scores_dict()
      INSIDE the read session before block exit, stored on
      RefinementContext.initial_scores_dict.
    """
    id: str
    raw_prompt: str
    optimized_prompt: str
    strategy_used: str | None
    project_id: str | None

    @classmethod
    def from_orm(cls, opt: "Optimization") -> "_OptSnapshot":
        """Build a frozen snapshot from an attached Optimization row.

        Must be called INSIDE the read session — accesses raw ORM attributes
        synchronously to materialize plain scalars before the session closes.
        """
        return cls(
            id=opt.id,
            raw_prompt=opt.raw_prompt or "",
            optimized_prompt=opt.optimized_prompt or "",
            strategy_used=opt.strategy_used,
            project_id=opt.project_id,
        )


@dataclass(frozen=True)
class _TurnSnapshot:
    r"""Frozen snapshot of RefinementTurn fields read post-`_load_latest_turn`.

    Field set (4) — every entry justified by a post-snapshot consumer:
    - version: persist callback builds `parent_version=ctx.latest_turn_version`
    - prompt: LLM step consumes at refinement_service.py:203
    - scores: score-deltas computation at refinement_service.py:257,390,391
    - strategy_used: LLM step's strategy_name resolution at refinement_service.py:205

    Excluded: `id` — never accessed post-`_load_latest_turn`
    (grep -nE r'\b(prev_turn|latest_turn)\.id\b': zero matches).
    """
    version: int
    prompt: str
    scores: dict[str, float] | None
    strategy_used: str | None

    @classmethod
    def from_orm(cls, turn: "RefinementTurn | None") -> "_TurnSnapshot | None":
        """Build a frozen snapshot from an attached RefinementTurn (or None).

        Returns None when no prior turn exists (initial-turn-missing case).
        """
        if turn is None:
            return None
        return cls(
            version=turn.version,
            prompt=turn.prompt,
            scores=turn.scores,
            strategy_used=turn.strategy_used,
        )


@dataclass(frozen=True)
class _BranchSnapshot:
    """Frozen snapshot of RefinementBranch fields read post-`_resolve_branch`.

    Field set (1) — `id` consumed by persist callback and SSE event payload.

    Round-17 narrowing: `parent_branch_id` and `forked_at_version` removed —
    verified at `refinement_service.py:131-132` that build_initial_turn_payload
    hardcodes these to None for the seed branch (does NOT read from loaded
    branch). No post-restructure code reads these fields from the snapshot.
    """
    id: str

    @classmethod
    def from_orm(cls, branch: "RefinementBranch") -> "_BranchSnapshot":
        """Build a frozen snapshot from an attached RefinementBranch row."""
        return cls(id=branch.id)


# ---------------------------------------------------------------------------
# Top-level RefinementContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefinementContext:
    """Frozen context object carrying all state needed by the LLM phase + persist
    callback. Replaces `RefinementService.db` + ORM-attribute reads inside the
    pipeline.

    Snapshot timing: `build()` MUST be called INSIDE the read step's
    `async with async_session_factory()` block. The classmethod synchronously
    materializes every snapshot dataclass before the block exits. Any post-LLM
    code path reads `ctx.opt_snapshot.<attr>`, `ctx.latest_turn_snapshot.<attr>`,
    `ctx.branch_snapshot.<attr>` — never `ctx.opt.<attr>` (which would be a
    SQLAlchemy lazy-load against a closed session).

    Cycle 2 RED test 5 (AST-based) pins this contract.
    """
    opt_snapshot: _OptSnapshot
    latest_turn_snapshot: _TurnSnapshot | None
    branch_snapshot: _BranchSnapshot
    historical_stats: dict[str, Any] | None
    enrichment: "EnrichedContext"
    refinement_request: str
    trace_id: str
    initial_scores_dict: dict[str, float]

    # ------------------------------------------------------------------
    # Convenience accessors (avoid spelling out `.opt_snapshot.id` everywhere)
    # ------------------------------------------------------------------

    @property
    def optimization_id(self) -> str:
        """Convenience: same as `ctx.opt_snapshot.id`."""
        return self.opt_snapshot.id

    @property
    def branch_id(self) -> str:
        """Convenience: same as `ctx.branch_snapshot.id`."""
        return self.branch_snapshot.id

    @property
    def latest_turn_version(self) -> int:
        """Latest turn version, or 0 if no prior turns exist."""
        return self.latest_turn_snapshot.version if self.latest_turn_snapshot else 0

    @classmethod
    def build(
        cls,
        *,
        opt: "Optimization",
        latest_turn: "RefinementTurn | None",
        branch: "RefinementBranch",
        historical_stats: dict[str, Any] | None,
        enrichment: "EnrichedContext",
        refinement_request: str,
        trace_id: str,
        initial_scores_dict: dict[str, float],
    ) -> "RefinementContext":
        """Construct a frozen RefinementContext from attached ORM rows.

        Must be called INSIDE the read session — accesses raw ORM attributes
        synchronously to materialize plain scalars before the session closes.
        """
        return cls(
            opt_snapshot=_OptSnapshot.from_orm(opt),
            latest_turn_snapshot=_TurnSnapshot.from_orm(latest_turn),
            branch_snapshot=_BranchSnapshot.from_orm(branch),
            historical_stats=historical_stats,
            enrichment=enrichment,
            refinement_request=refinement_request,
            trace_id=trace_id,
            initial_scores_dict=initial_scores_dict,
        )


# ---------------------------------------------------------------------------
# Payload dataclasses for queue-callback inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _InitialTurnPayload:
    """Pure-compute output of `build_initial_turn_payload`.

    Both kwargs dicts are ready to splat into RefinementBranch / RefinementTurn
    constructors inside the queue's persist callback. No DB, no LLM in this
    object — just the values needed to insert the seed rows.
    """
    branch_kwargs: dict[str, Any]
    turn_kwargs: dict[str, Any]


@dataclass(frozen=True)
class RollbackPayload:
    """Pure-compute output of `RefinementService.rollback()`.

    Carries the un-attached branch + seed-turn fields needed by the persist
    callback to INSERT both a new RefinementBranch row AND a seed
    RefinementTurn row at rollback time. Router uses the callback's return
    dict (post-commit `db.refresh()`-populated) to construct the HTTP
    response without touching detached ORM objects.

    v0.4.22 soak-gate Day 1 finding (refine-after-rollback UX defect): prior
    to seed-turn inclusion, the new branch shipped with zero turns; any
    subsequent ``POST /api/refine`` without an explicit ``branch_id``
    defaulted to the latest branch (the empty rollback branch) and raised
    ``ValueError("invoke_refinement_pipeline requires latest_turn_snapshot;
    caller must seed via build_initial_turn_payload + queue submit")``. The
    seed turn is now created as version 1 of the new branch carrying the
    content of the version being rolled back to — refines on the rolled-back
    branch work seamlessly from that point on.
    """
    branch_kwargs: dict[str, Any]
    seed_turn_kwargs: dict[str, Any]
