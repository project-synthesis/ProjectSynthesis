"""Structured audit event logging for sensitive operations.

Writes to the AuditLog table. Auto-prunes entries older than
AUDIT_RETENTION_DAYS (default 90) via prune_audit_log().
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession,
    action: str,
    actor_ip: str | None = None,
    actor_session: str | None = None,
    detail: dict | None = None,
    outcome: str = "success",
) -> None:
    """Write an audit log entry."""
    entry = AuditLog(
        action=action,
        actor_ip=actor_ip,
        actor_session=actor_session,
        detail=detail,
        outcome=outcome,
    )
    db.add(entry)
    await db.commit()
    logger.debug("Audit log: action=%s outcome=%s ip=%s", action, outcome, actor_ip)


async def prune_audit_log(db: AsyncSession, retention_days: int = 90) -> int:
    """Delete audit log entries older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(
        delete(AuditLog).where(AuditLog.timestamp < cutoff)
    )
    await db.commit()
    deleted = result.rowcount
    if deleted:
        logger.info("Pruned %d audit log entries older than %d days", deleted, retention_days)
    return deleted
