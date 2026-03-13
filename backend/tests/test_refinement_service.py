"""Tests for refinement service — branch CRUD and unified refine."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_service import (
    create_trunk_branch,
    get_branches,
    get_branch,
    MAX_BRANCHES_PER_OPTIMIZATION,
    MAX_ACTIVE_BRANCHES,
)


class TestCreateTrunkBranch:
    @pytest.mark.asyncio
    async def test_creates_trunk(self):
        db = AsyncMock()
        db.add = MagicMock()
        branch = await create_trunk_branch(
            optimization_id="opt-1",
            prompt="Test prompt",
            scores={"overall_score": 6.0, "clarity_score": 7},
            db=db,
        )
        assert branch["label"] == "trunk"
        assert branch["status"] == "active"
        assert branch["turn_count"] == 0
        db.add.assert_called_once()


class TestBranchLimits:
    @pytest.mark.asyncio
    async def test_max_branches_enforced(self):
        db = AsyncMock()
        # Mock existing branches at limit
        result_mock = MagicMock()
        result_mock.scalar.return_value = MAX_BRANCHES_PER_OPTIMIZATION
        db.execute.return_value = result_mock

        from app.services.refinement_service import fork_branch
        with pytest.raises(ValueError, match="Maximum.*branches"):
            async for _ in fork_branch(
                optimization_id="opt-1",
                parent_branch_id="branch-1",
                message="test",
                provider=AsyncMock(),
                db=db,
            ):
                pass
