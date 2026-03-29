"""Tests for Haiku label generation."""

from unittest.mock import AsyncMock

import pytest

from app.services.taxonomy.labeling import generate_label


@pytest.mark.asyncio
async def test_generate_label_returns_string(mock_provider):
    """Should return a short label from the LLM."""
    mock_provider.complete_parsed = AsyncMock(
        return_value=type("R", (), {"label": "API Architecture"})()
    )
    label = await generate_label(
        provider=mock_provider,
        member_texts=["REST API endpoint", "GraphQL resolver"],
        model="claude-haiku-4-5",
    )
    assert label == "API Architecture"
    mock_provider.complete_parsed.assert_called_once()


@pytest.mark.asyncio
async def test_generate_label_fallback_on_error(mock_provider):
    """Should return fallback label if LLM fails."""
    mock_provider.complete_parsed = AsyncMock(side_effect=RuntimeError("LLM down"))
    label = await generate_label(
        provider=mock_provider,
        member_texts=["test text"],
        model="claude-haiku-4-5",
    )
    assert label == "Unnamed Cluster"


@pytest.mark.asyncio
async def test_generate_label_no_provider():
    """Should return fallback label if no provider."""
    label = await generate_label(
        provider=None,
        member_texts=["test text"],
        model="claude-haiku-4-5",
    )
    assert label == "Unnamed Cluster"
