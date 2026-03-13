"""Unified refinement service — branch CRUD and the single refine() operation.

One code path for auto-refinement (oracle-driven), user refinement, and forks.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncGenerator, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import PairwisePreference, RefinementBranch
from app.models.optimization import Optimization
from app.providers.base import LLMProvider
from app.services.prompt_diff import compute_prompt_hash
from app.services.session_context import SessionContext, compact_session, needs_compaction
from app.utils.json_fields import parse_json_column

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
        ctx_data = parse_json_column(branch.session_context)
        if ctx_data:
            try:
                session = SessionContext.from_dict(ctx_data)
            except (KeyError, TypeError):
                logger.warning("Failed to load session context for branch %s", branch_id)

    scores_before = parse_json_column(branch.scores, default={})

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
        logger.info(
            "Session compaction triggered for branch %s (turn %d)",
            branch_id, updated_session.turn_count,
        )
        updated_session = await compact_session(updated_session, provider)
        logger.info(
            "Session compaction complete for branch %s (turn_count now %d)",
            branch_id, updated_session.turn_count,
        )

    # Update branch
    prompt_hash = compute_prompt_hash(refined_prompt)
    turn_entry = {
        "turn": branch.turn_count + 1,
        "source": source,
        "message_summary": message[:200],
        "scores_before": scores_before,
        "prompt_hash": prompt_hash,
    }

    history = parse_json_column(branch.turn_history, default=[])
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
    scores = parse_json_column(branch.scores) if branch.scores else None

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
