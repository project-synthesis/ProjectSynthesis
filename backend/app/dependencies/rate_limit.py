"""In-memory rate limiting FastAPI dependency.

Uses the `limits` library with in-memory storage.
Rate strings configurable via env vars (e.g., "10/minute").
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


def reset_rate_limit_storage() -> None:
    """Reset the in-memory moving-window storage. Test-only helper.

    The storage is a process-level singleton, so quotas consumed by one
    test leak into the next unless the storage is reset between runs.
    Call from pytest fixtures (autouse or per-test) — never from
    production code paths.
    """
    _storage.reset()


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
                detail="Rate limit exceeded. Try again later.",
                headers={"Retry-After": "60"},
            )

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        import ipaddress

        from app.config import settings

        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded and client_ip in settings.TRUSTED_PROXIES:
            candidate = forwarded.split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                logger.warning("Invalid IP in X-Forwarded-For: %s", candidate)
                return client_ip
        return client_ip
