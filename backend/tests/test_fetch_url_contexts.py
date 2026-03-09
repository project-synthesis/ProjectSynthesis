"""Tests for fetch_url_contexts in url_fetcher.

Covers:
- Happy path: HTML fetched and stripped, correct dict shape returned
- Error path: network exception → error entry returned, batch not aborted
- Mixed: one URL succeeds, one fails — both represented in result
- Empty / None input → empty list
- Content truncated at 3000 chars
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.url_fetcher import fetch_url_contexts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(text: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_stripped_content():
    """HTML tags are stripped and result has the expected dict shape."""
    html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(html))

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(["https://example.com"])

    assert len(results) == 1
    entry = results[0]
    assert entry["url"] == "https://example.com"
    assert "# Hello" in entry["content"]
    assert "World" in entry["content"]
    assert entry["error"] is None


@pytest.mark.asyncio
async def test_happy_path_preserves_order():
    """Results are returned in the same order as the input URLs."""
    responses = [
        _mock_response("<p>First</p>"),
        _mock_response("<p>Second</p>"),
    ]
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=responses)

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(
            ["https://first.example.com", "https://second.example.com"]
        )

    assert results[0]["url"] == "https://first.example.com"
    assert "First" in results[0]["content"]
    assert results[1]["url"] == "https://second.example.com"
    assert "Second" in results[1]["content"]


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_error_returns_error_entry():
    """A network-level exception produces an error dict rather than aborting the batch."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(["https://unreachable.example.com"])

    assert len(results) == 1
    entry = results[0]
    assert entry["url"] == "https://unreachable.example.com"
    assert entry["content"] == ""
    assert entry["error"] is not None
    assert "connection refused" in entry["error"]


@pytest.mark.asyncio
async def test_non_200_response_returns_error_entry():
    """A 404 response (raise_for_status raises) is captured as an error entry."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response("Not Found", status_code=404))

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(["https://example.com/missing"])

    assert len(results) == 1
    entry = results[0]
    assert entry["content"] == ""
    assert entry["error"] is not None


# ---------------------------------------------------------------------------
# Mixed: one succeeds, one fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_batch_returns_both_entries():
    """One successful fetch and one failure both appear in the output."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_response("<h2>API Docs</h2><p>Details here.</p>"),
            httpx.TimeoutException("timed out"),
        ]
    )

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(
            ["https://good.example.com", "https://slow.example.com"]
        )

    assert len(results) == 2

    good = results[0]
    assert good["url"] == "https://good.example.com"
    assert "## API Docs" in good["content"]
    assert good["error"] is None

    bad = results[1]
    assert bad["url"] == "https://slow.example.com"
    assert bad["content"] == ""
    assert bad["error"] is not None
    assert "timed out" in bad["error"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_returns_empty():
    results = await fetch_url_contexts([])
    assert results == []


@pytest.mark.asyncio
async def test_none_input_returns_empty():
    results = await fetch_url_contexts(None)
    assert results == []


@pytest.mark.asyncio
async def test_content_truncated_at_3000_chars():
    """Content is capped at 3000 characters."""
    long_text = "x" * 10_000
    html = f"<p>{long_text}</p>"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(html))

    with patch("app.services.url_fetcher.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await fetch_url_contexts(["https://example.com/long"])

    assert len(results[0]["content"]) <= 3000
