import pytest
from unittest.mock import patch, MagicMock

class TestStrategiesRouter:
    @pytest.mark.asyncio
    async def test_list_strategies(self, app_client):
        with patch("app.routers.strategies._loader.list_with_metadata") as mock_list:
            mock_list.return_value = [
                {"name": "strat1", "tagline": "t", "description": "d", "warnings": []}
            ]
            resp = await app_client.get("/api/strategies")
            assert resp.status_code == 200
            assert resp.json() == [{"name": "strat1", "tagline": "t", "description": "d", "warnings": []}]

    @pytest.mark.asyncio
    async def test_get_strategy_success(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_file.read_text.return_value = "---\ntagline: t\n---\nbody"
            mock_path.return_value = mock_file
            
            resp = await app_client.get("/api/strategies/strat1")
            assert resp.status_code == 200
            assert resp.json() == {"name": "strat1", "content": "---\ntagline: t\n---\nbody"}

    @pytest.mark.asyncio
    async def test_get_strategy_not_found(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = False
            mock_path.return_value = mock_file
            
            resp = await app_client.get("/api/strategies/strat1")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_strategy_read_error(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_file.read_text.side_effect = OSError("read error")
            mock_path.return_value = mock_file
            
            resp = await app_client.get("/api/strategies/strat1")
            assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_update_strategy_success(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_path.return_value = mock_file
            
            payload = {"content": "---\ntagline: t\ndescription: d\n---\nbody text"}
            resp = await app_client.put("/api/strategies/strat1", json=payload)
            assert resp.status_code == 200
            
            mock_file.write_text.assert_called_once_with(payload["content"], encoding="utf-8")

    @pytest.mark.asyncio
    async def test_update_strategy_no_frontmatter(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_path.return_value = mock_file
            
            payload = {"content": "just body text no frontmatter"}
            resp = await app_client.put("/api/strategies/strat1", json=payload)
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_strategy_empty_body(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_path.return_value = mock_file
            
            payload = {"content": "---\ntagline: t\n---\n   "}
            resp = await app_client.put("/api/strategies/strat1", json=payload)
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_strategy_write_error(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_file.write_text.side_effect = OSError("write error")
            mock_path.return_value = mock_file
            
            payload = {"content": "---\ntagline: t\ndescription: d\n---\nbody text"}
            resp = await app_client.put("/api/strategies/strat1", json=payload)
            assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_update_strategy_not_found(self, app_client):
        with patch("app.routers.strategies._safe_strategy_path") as mock_path:
            mock_file = MagicMock()
            mock_file.is_file.return_value = False
            mock_path.return_value = mock_file
            
            payload = {"content": "---\n---\nbody"}
            resp = await app_client.put("/api/strategies/strat1", json=payload)
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_safe_strategy_path_traversal(self):
        from app.routers.strategies import _safe_strategy_path
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _safe_strategy_path("../../../etc/passwd")
        assert exc.value.status_code == 400
