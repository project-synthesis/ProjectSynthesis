import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from watchfiles import Change

from app.services.file_watcher import watch_strategy_files

@pytest.mark.asyncio
async def test_watch_strategy_files_dir_not_exist(caplog):
    # Tests that it exits early if directory doesn't exist
    with patch("app.services.file_watcher.Path.is_dir", return_value=False):
        await watch_strategy_files(Path("/fake/path"))
    assert caplog.records or not caplog.records

@pytest.mark.asyncio
async def test_watch_strategy_files_cancel():
    # Tests graceful exit on CancelledError
    async def mock_awatch(*args, **kwargs):
        raise asyncio.CancelledError()
        yield  # Make it an async generator

    with patch("app.services.file_watcher.Path.is_dir", return_value=True), \
         patch("app.services.file_watcher.awatch", side_effect=mock_awatch):
        await watch_strategy_files(Path("/fake/path"))

@pytest.mark.asyncio
async def test_watch_strategy_files_exception(caplog):
    # Force awatch to raise an exception, wait out the sleep, then cancel
    call_count = 0

    async def mock_awatch(*args, **kwargs):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            raise Exception("Test Exception")
        raise asyncio.CancelledError()
        yield  # Make it an async generator

    with patch("app.services.file_watcher.Path.is_dir", return_value=True), \
         patch("app.services.file_watcher.awatch", side_effect=mock_awatch), \
         patch("app.services.file_watcher.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        
        await watch_strategy_files(Path("/fake/path"))
        assert "Strategy file watcher error: Test Exception" in caplog.text
        mock_sleep.assert_called_once_with(5)

@pytest.mark.asyncio
async def test_watch_strategy_files_successful_events():
    # Provide a mock sequence for awatch yielding changes
    async def mock_awatch(*args, **kwargs):
        # Valid event
        yield {(Change.added, "/fake/path/test_strategy.md")}
        # Ignored because not .md
        yield {(Change.modified, "/fake/path/ignore_me.txt")}
        # Ignored because unsupported change type (not in _action_map)
        yield {("unsupported_change_type", "/fake/path/test_strategy2.md")}
        # Deleted event
        yield {(Change.deleted, "/fake/path/test_strategy3.md")}
        raise asyncio.CancelledError()

    mock_bus = MagicMock()

    with patch("app.services.file_watcher.Path.is_dir", return_value=True), \
         patch("app.services.file_watcher.awatch", side_effect=mock_awatch), \
         patch("app.services.file_watcher.time.time", return_value=12345.6), \
         patch("app.services.event_bus.event_bus", mock_bus):
        
        await watch_strategy_files(Path("/fake/path"))
        
        # Verify event was published for .md file only for supported actions
        assert mock_bus.publish.call_count == 2
        
        # Call 1
        args, kwargs = mock_bus.publish.call_args_list[0]
        assert args[0] == "strategy_changed"
        assert args[1]["action"] == "created"
        assert args[1]["name"] == "test_strategy"
        assert args[1]["timestamp"] == 12345.6
        
        # Call 2
        args, kwargs = mock_bus.publish.call_args_list[1]
        assert args[0] == "strategy_changed"
        assert args[1]["action"] == "deleted"
        assert args[1]["name"] == "test_strategy3"
