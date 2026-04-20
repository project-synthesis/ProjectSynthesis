"""Project node management for ADR-005 multi-project isolation.

Handles project creation, re-linking, resolution from repo name, and
bulk migration of optimizations between projects (B2).
Called from routers/github_repos.py (link endpoint) and engine.py
(process_optimization project resolution).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import LinkedRepo, Optimization, PromptCluster

logger = logging.getLogger(__name__)

# Module-level Legacy project_id cache (B1 — zero-query fallback).
# Backend-process lifespan pre-populates via app.state.legacy_project_id
# and ``prime_legacy_project_id_cache()``. MCP-process callers lazily warm
# it on first ``resolve_repo_project()``/``resolve_project_id()`` call
# that doesn't receive an explicit value. Safe across event loops because
# write is a single-assignment idempotent — worst case is a redundant
# query if the cache is primed concurrently, not a torn read.
_cached_legacy_project_id: str | None = None
_cached_legacy_warm: bool = False


def prime_legacy_project_id_cache(legacy_project_id: str | None) -> None:
    """Seed the module-level Legacy project_id cache at startup (B1).

    Called from ``main.py`` lifespan after the ADR-005 migration, and from
    ``mcp_server.py`` lifespan so the MCP process shares the same cache.
    Safe to call multiple times — last write wins. Passing ``None``
    explicitly marks the cache as warm with a null value (legitimate when
    the Legacy node hasn't been provisioned yet).
    """
    global _cached_legacy_project_id, _cached_legacy_warm
    _cached_legacy_project_id = legacy_project_id
    _cached_legacy_warm = True


async def ensure_project_for_repo(
    db: AsyncSession,
    repo_full_name: str,
    target_project_id: str | None = None,
) -> str:
    """Find or create a project node for the given repo.

    Args:
        db: Active database session.
        repo_full_name: GitHub repo in "owner/repo" format.
        target_project_id: If provided, link the repo to this existing project
            instead of auto-creating. Validates the project exists.

    Logic (when target_project_id is None):
    1. If LinkedRepo already has project_node_id set, return it.
    2. If a project node matching this repo label exists, reattach (re-link).
    3. Otherwise, create a new project node.

    Legacy is never renamed — it stays as the permanent home for
    pre-repo and non-repo work.

    Returns the project node ID (PromptCluster.id with state="project").
    """
    # Explicit project choice — validate and use directly
    if target_project_id:
        project = await db.get(PromptCluster, target_project_id)
        if project and project.state == "project":
            lr = (await db.execute(
                select(LinkedRepo).where(
                    LinkedRepo.full_name == repo_full_name,
                ).limit(1)
            )).scalar_one_or_none()
            if lr:
                lr.project_node_id = target_project_id
            logger.info(
                "Phase 2A: linked repo '%s' to existing project '%s' (%s)",
                repo_full_name, project.label, target_project_id[:8],
            )
            return target_project_id
        logger.warning(
            "Invalid target_project_id '%s' — falling through to auto-creation",
            target_project_id,
        )

    # Check if LinkedRepo already points to a project
    lr = (await db.execute(
        select(LinkedRepo).where(LinkedRepo.full_name == repo_full_name).limit(1)
    )).scalar_one_or_none()

    if lr and lr.project_node_id:
        return lr.project_node_id

    # Check for existing project node matching this repo label (re-link case)
    existing = (await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == repo_full_name,
        ).limit(1)
    )).scalar_one_or_none()

    if existing:
        if lr:
            lr.project_node_id = existing.id
            await db.flush()
        return existing.id

    # Always create a new project — never rename Legacy.
    # Legacy is the permanent home for pre-repo and non-repo work.
    # A user may have hundreds of unrelated prompts before linking
    # their first repo; renaming Legacy would miscategorize all of them.
    new_project = PromptCluster(
        label=repo_full_name,
        state="project",
        domain="general",
        task_type="general",
        member_count=0,
    )
    db.add(new_project)
    await db.flush()

    if lr:
        lr.project_node_id = new_project.id

    logger.info(
        "Phase 2A: created project node '%s' (%s)",
        repo_full_name, new_project.id[:8],
    )
    return new_project.id


async def _warm_legacy_cache(db: AsyncSession) -> str | None:
    """Populate ``_cached_legacy_project_id`` on first miss.

    Idempotent. Safe under concurrency (last write wins; value is stable
    across the process lifetime — Legacy's id never changes once seeded).
    """
    global _cached_legacy_project_id, _cached_legacy_warm
    if _cached_legacy_warm:
        return _cached_legacy_project_id
    try:
        _cached_legacy_project_id = await get_legacy_project_id(db)
    except Exception as exc:
        logger.debug("Lazy Legacy cache warm failed: %s", exc)
        _cached_legacy_project_id = None
    _cached_legacy_warm = True
    return _cached_legacy_project_id


async def resolve_project_id(
    db: AsyncSession,
    repo_full_name: str | None,
    legacy_project_id: str | None = None,
) -> str | None:
    """Resolve project_id from repo_full_name.

    Args:
        db: Active database session.
        repo_full_name: From Optimization.repo_full_name.
        legacy_project_id: Cached Legacy project ID (avoids query). When
            ``None``, falls back to the module-level
            ``_cached_legacy_project_id`` (lazily warmed).

    Returns:
        Project node ID, or resolved Legacy fallback.
    """
    _legacy = legacy_project_id
    if _legacy is None:
        _legacy = await _warm_legacy_cache(db)

    if not repo_full_name:
        return _legacy

    project_node_id = (await db.execute(
        select(LinkedRepo.project_node_id)
        .where(LinkedRepo.full_name == repo_full_name)
        .limit(1)
    )).scalar_one_or_none()

    if project_node_id:
        return project_node_id

    return _legacy


async def get_legacy_project_id(db: AsyncSession) -> str | None:
    """Return the canonical Legacy project node ID, or None if not provisioned.

    Called once at startup (main.py lifespan, mcp_server.py lifespan) and
    cached on ``app.state.legacy_project_id`` / module-level singleton. The
    pipeline uses this as the terminal fallback in the project-id resolution
    chain (explicit request → repo chain → cached Legacy). Returns ``None``
    only on a fresh DB before ADR-005 migration has run — callers should
    treat that as a valid no-op.
    """
    row = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        ).limit(1)
    )
    return row.scalar_one_or_none()


async def migrate_optimizations(
    db: AsyncSession,
    *,
    from_project_id: str,
    to_project_id: str,
    since: datetime | None = None,
    repo_full_name_is_null: bool = False,
    dry_run: bool = False,
) -> int:
    """Bulk-move ``Optimization`` rows between projects (ADR-005 B2).

    Used by the post-link migration toast (move recent Legacy prompts to a
    newly-linked project), the unlink ``mode="rehome"`` contract (B5),
    and the ``POST /api/projects/migrate`` user action (B3).  No automatic
    migration ever happens — the user is always the decider; this function
    only executes an explicit request.

    Args:
        db: Active session.  Caller owns commit (so API routes can wrap
            this inside their own transaction and emit events atomically).
        from_project_id: Source project node id.
        to_project_id: Destination project node id.  Must exist and have
            ``state="project"``.  A source-equals-destination call is a
            no-op and returns 0 without touching the DB.
        since: If set, only rows created on/after this timestamp are moved.
            Intended for last-7-day toast migrations.  ``None`` migrates
            the full history — use with intention.
        repo_full_name_is_null: When True, additionally require that the
            row's ``repo_full_name`` is NULL — i.e. an optimization that
            was made without a linked repo.  Prevents moving opts that
            were explicitly tagged with another repo's name (which would
            contradict the user's recorded intent).
        dry_run: When True, return the candidate count without mutating.

    Returns:
        Number of rows migrated (or candidate count if ``dry_run``).

    Raises:
        ValueError: if ``to_project_id`` does not resolve to a valid
            project node.
    """
    if from_project_id == to_project_id:
        logger.debug("migrate_optimizations: no-op (from == to)")
        return 0

    # Validate destination (source need not exist — a dead from_project_id
    # simply returns 0 candidates without raising, since this path is
    # often driven by UI that may race with project deletion).
    dest = await db.get(PromptCluster, to_project_id)
    if dest is None or dest.state != "project":
        raise ValueError(
            f"migrate_optimizations: to_project_id={to_project_id!r} "
            "is not a valid project node"
        )

    conditions = [Optimization.project_id == from_project_id]
    if since is not None:
        # Normalise to naive-UTC for SQLite compatibility.
        ts = since.astimezone(timezone.utc).replace(tzinfo=None) if since.tzinfo else since
        conditions.append(Optimization.created_at >= ts)
    if repo_full_name_is_null:
        conditions.append(Optimization.repo_full_name.is_(None))

    where_clause = and_(*conditions)

    # Count first — cheap, and makes dry-run / empty-migration logging
    # consistent.  SQLAlchemy's Row.rowcount from bulk update is driver
    # dependent and unreliable on SQLite.
    from sqlalchemy import func
    count_stmt = (
        select(func.count())
        .select_from(Optimization)
        .where(where_clause)
    )
    candidate_count = (await db.execute(count_stmt)).scalar_one() or 0

    if dry_run or candidate_count == 0:
        return int(candidate_count)

    update_stmt = (
        update(Optimization)
        .where(where_clause)
        .values(project_id=to_project_id)
    )
    await db.execute(update_stmt)
    await db.flush()

    logger.info(
        "migrate_optimizations: moved %d rows %s → %s (since=%s, null_repo=%s)",
        candidate_count,
        from_project_id[:8], to_project_id[:8],
        since.isoformat() if since else "forever",
        repo_full_name_is_null,
    )

    # Emit observability event (best-effort, non-fatal). publish() is sync.
    try:
        from app.services.event_bus import event_bus
        event_bus.publish("optimizations_migrated", {
            "from_project_id": from_project_id,
            "to_project_id": to_project_id,
            "count": int(candidate_count),
            "since": since.isoformat() if since else None,
            "repo_full_name_is_null": repo_full_name_is_null,
        })
    except Exception as exc:
        logger.debug("event_bus publish (optimizations_migrated) failed: %s", exc)

    # Emit taxonomy decision event for the audit log.
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="api",
            op="project_migration",
            decision="migrated",
            context={
                "from_project_id": from_project_id,
                "to_project_id": to_project_id,
                "count": int(candidate_count),
                "since": since.isoformat() if since else None,
                "repo_full_name_is_null": repo_full_name_is_null,
            },
        )
    except RuntimeError:
        pass

    return int(candidate_count)


async def resolve_repo_project(
    repo_full_name: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve repo_full_name (auto if None) and project_id in one call.

    When *repo_full_name* is ``None``, auto-resolves from the most recently
    linked repo.  Opens a single short-lived session for both lookups.

    Always applies the cached-or-warmed Legacy fallback via
    ``resolve_project_id()`` — repo-less optimizations land on Legacy's id
    rather than ``NULL`` (B1).

    Returns:
        ``(repo_full_name, project_id)`` tuple.  Both may be ``None``.
        Non-fatal — returns partial results on any failure.
    """
    try:
        async with async_session_factory() as db:
            if not repo_full_name:
                linked = (await db.execute(
                    select(LinkedRepo)
                    .order_by(LinkedRepo.linked_at.desc())
                    .limit(1)
                )).scalar_one_or_none()
                if linked:
                    repo_full_name = linked.full_name

            # B1: resolve even when repo is None so Legacy fallback applies.
            project_id = await resolve_project_id(db, repo_full_name)
            return repo_full_name, project_id
    except Exception:
        return repo_full_name, None
