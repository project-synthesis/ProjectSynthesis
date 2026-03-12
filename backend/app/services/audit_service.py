"""Structured audit logging for authentication events.

All auth events (login, logout, refresh, failure) are logged with
timestamp, user_id, IP, and user-agent for compliance and debugging.
Uses background tasks to never block the response path.

This module has no dependency on FastAPI — callers extract IP and
User-Agent from the request and pass them as plain strings.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.database import get_session_context
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

# Event type constants
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_NEW_USER = "auth.login.new_user"
AUTH_LOGOUT = "auth.logout"
AUTH_LOGOUT_ALL = "auth.logout_all"
AUTH_REFRESH = "auth.refresh"
AUTH_FAILURE = "auth.failure"
AUTH_TOKEN_EXCHANGE = "auth.token_exchange"


async def log_auth_event(
    event_type: str,
    *,
    user_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write a structured audit log entry.

    This function manages its own database session so it can be called
    from background tasks or anywhere in the request lifecycle without
    depending on the request's session.

    Args:
        event_type: One of the AUTH_* constants.
        user_id: The authenticated user's ID, if known.
        ip_address: Client IP address string.
        user_agent: Client User-Agent string (truncated to 512 chars by caller).
        metadata: Additional context (e.g., device_id, failure reason).
    """
    try:
        async with get_session_context() as session:
            entry = AuditLog(
                event_type=event_type,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata_=json.dumps(metadata) if metadata else None,
            )
            session.add(entry)
            await session.commit()
    except Exception:
        # Audit logging must never break the auth flow
        logger.exception("Failed to write audit log entry: %s", event_type)
