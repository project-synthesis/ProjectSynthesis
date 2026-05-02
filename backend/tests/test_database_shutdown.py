from unittest.mock import AsyncMock, patch

import pytest

from app.database import dispose


@pytest.mark.asyncio
async def test_dispose_issues_wal_checkpoint():
    """Verify that dispose() acquires a connection and issues a WAL checkpoint."""
    # We mock engine.begin to intercept the PRAGMA execution
    with patch("app.database.engine") as mock_engine:
        # Set up a mock connection context manager
        mock_conn = AsyncMock()
        mock_conn_ctx = AsyncMock()
        mock_conn_ctx.__aenter__.return_value = mock_conn
        mock_engine.begin.return_value = mock_conn_ctx
        mock_engine.dispose = AsyncMock()

        await dispose()

        # Verify that engine.begin was called to get a connection
        mock_engine.begin.assert_called_once()

        # Verify the PRAGMA was executed
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0][0]
        assert "PRAGMA wal_checkpoint(TRUNCATE)" in str(call_args)

        # Verify that engine.dispose was awaited
        mock_engine.dispose.assert_awaited_once()

@pytest.mark.asyncio
async def test_dispose_swallows_checkpoint_errors():
    """Verify that if the DB is unrecoverably locked during checkpoint, dispose() still completes."""
    with patch("app.database.engine") as mock_engine:
        import sqlalchemy.exc

        mock_conn = AsyncMock()
        # Simulate a SQLite OperationalError during checkpoint
        mock_conn.execute.side_effect = sqlalchemy.exc.OperationalError("database is locked", None, None)

        mock_conn_ctx = AsyncMock()
        mock_conn_ctx.__aenter__.return_value = mock_conn
        mock_engine.begin.return_value = mock_conn_ctx
        mock_engine.dispose = AsyncMock()

        # Should NOT raise an exception
        await dispose()

        # Verify that engine.dispose was STILL awaited despite the error
        mock_engine.dispose.assert_awaited_once()
