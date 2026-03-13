"""Tests for complete_with_session on LLMProvider."""

import pytest
from unittest.mock import AsyncMock
from app.services.session_context import SessionContext


class TestCompleteWithSessionBase:
    @pytest.mark.asyncio
    async def test_default_implementation_delegates_to_complete(self):
        from app.providers.mock import MockProvider
        provider = MockProvider()
        result_text, session = await provider.complete_with_session(
            system="test", user="test", model="claude-haiku-4-5",
        )
        assert isinstance(result_text, str)
        assert isinstance(session, SessionContext)
        assert session.turn_count == 1
