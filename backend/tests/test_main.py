import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.main import app, lifespan

pytestmark = pytest.mark.asyncio

async def test_lifespan_startup_and_shutdown():
    # Mocking different components to ensure lifespan runs properly without hanging
    with patch("app.main.aiosqlite.connect") as mock_connect, \
         patch("app.providers.detector.detect_provider") as mock_detect_provider, \
         patch("app.services.prompt_loader.PromptLoader") as mock_prompt_loader, \
         patch("app.services.strategy_loader.StrategyLoader") as mock_strategy_loader, \
         patch("app.main.watch_strategy_files", new_callable=MagicMock) as mock_watch, \
         patch("app.main.logger") as mock_logger:
            
        with patch("app.database.async_session_factory") as mock_session_factory, \
             patch("app.services.trace_logger.TraceLogger") as mock_trace_logger, \
             patch("app.main.DATA_DIR") as mock_data_dir, \
             patch("app.main.event_bus") as mock_event_bus, \
             patch("app.services.pattern_extractor.PatternExtractorService") as mock_pattern_extractor:
        
            # Setup mocks
            mock_db_path = MagicMock()
            mock_db_path.exists.return_value = True
            mock_data_dir.__truediv__.return_value = mock_db_path
            
            mock_db = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_db
            
            mock_provider = MagicMock()
            mock_provider.name = "mock_provider"
            mock_detect_provider.return_value = mock_provider
            
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__.return_value = mock_session
            
            mock_trace_logger_instance = MagicMock()
            mock_trace_logger_instance.rotate.return_value = 5
            mock_trace_logger.return_value = mock_trace_logger_instance
            
            # Subscribed events loop test
            # We want the listener to process an event
            async def mock_subscribe():
                yield {"event": "optimization_created", "data": {"id": "test_opt_1"}}
                yield {"event": "optimization_created", "data": {}} # missing id
                yield {"event": "other_event", "data": {"id": "123"}}
                # Then we simulate a cancel or exception to break the loop
                raise asyncio.CancelledError()
                
            mock_event_bus.subscribe = mock_subscribe
            
            # Make sure extractor processes correctly
            mock_extractor_instance = AsyncMock()
            mock_pattern_extractor.return_value = mock_extractor_instance
            
            # Since we want to test the inner listener, let's capture the tasks
            class DummyTask:
                def __init__(self, coro=None, name=None, *args, **kwargs):
                    self.coro = coro
                    self.name = name
                    self.cancelled = False
                def cancel(self):
                    self.cancelled = True
                def __await__(self):
                    async def _inner():
                        if self.coro:
                            try:
                                await self.coro
                            except Exception:
                                pass
                    return _inner().__await__()
            
            with patch("app.main.asyncio.create_task", side_effect=DummyTask) as mock_create_task:
                async with lifespan(app):
                    assert app.state.provider == mock_provider
                    
                    # We have a task for listener. Let's await it to simulate the background work
                    if hasattr(app.state, "extraction_task") and app.state.extraction_task:
                        await app.state.extraction_task
                
                mock_session.execute.assert_called_once()


async def test_lifespan_startup_handler_errors_do_not_crash():
    with patch("app.main.aiosqlite.connect") as mock_connect, \
         patch("app.providers.detector.detect_provider", side_effect=ImportError("No provider")), \
         patch("app.services.prompt_loader.PromptLoader") as mock_prompt_loader, \
         patch("app.services.strategy_loader.StrategyLoader") as mock_strategy_loader, \
         patch("app.main.watch_strategy_files", new_callable=MagicMock):
        
        with patch("app.database.async_session_factory") as mock_session_factory, \
             patch("app.services.trace_logger.TraceLogger") as mock_trace_logger, \
             patch("app.main.DATA_DIR") as mock_data_dir:
            
            mock_prompt_loader.return_value.validate_all.side_effect = RuntimeError("Bad template")
            mock_trace_logger.side_effect = Exception("Rotate Failed")
            mock_session_factory.return_value.__aenter__.side_effect = Exception("DB error")
            
            class DummyTask:
                def __init__(self, *args, **kwargs):
                    pass
                def cancel(self):
                    pass
                def __await__(self):
                    async def _inner(): raise asyncio.CancelledError()
                    return _inner().__await__()
                    
            with patch("app.main.asyncio.create_task", side_effect=DummyTask):
                async with lifespan(app):
                    pass

async def test_main_router_imports():
    from app.main import app as main_app
    assert len(main_app.routes) > 0 # make sure some routers loaded


async def test_lazy_imports_exception_handling():
    import sys
    from importlib import reload
    import app.main
    
    # We will hide a module so that ImportError is triggered
    with patch.dict(sys.modules, {"app.routers.health": None, "app.routers.optimize": None, "app.routers.history": None, "app.routers.feedback": None, "app.routers.providers": None, "app.routers.settings": None, "app.routers.github_auth": None, "app.routers.github_repos": None, "app.routers.refinement": None, "app.routers.events": None, "app.routers.preferences": None, "app.routers.strategies": None, "app.routers.patterns": None}):
        reload(app.main)
        
    # Put it back to normal
    reload(app.main)
