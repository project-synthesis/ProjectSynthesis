"""Shared pytest fixtures for backend tests.

Autouse fixtures applied to ALL tests in this directory.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _bypass_slowapi_request_type_check():
    """Allow MagicMock requests through slowapi's isinstance guard in unit tests.

    slowapi validates ``isinstance(request, starlette.requests.Request)`` before
    checking rate limits. Unit tests pass MagicMock objects, not real Starlette
    Requests. Patching the guard to use ``object`` (which every MagicMock satisfies)
    lets unit tests call rate-limited endpoints directly.

    Rate limit counting is not affected — each MagicMock gets a unique string key
    via its repr, so tests never hit rate limits.
    """
    with patch("slowapi.extension.Request", object):
        yield
