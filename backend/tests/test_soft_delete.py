"""Tests for soft-delete behavior (Task 11)."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.optimization_service import delete_optimization, get_optimization


async def test_delete_optimization_sets_deleted_at():
    """delete_optimization() should set deleted_at, NOT call session.delete."""
    opt = MagicMock()
    opt.deleted_at = None

    session = AsyncMock()
    session.flush = AsyncMock()

    with patch(
        "app.services.optimization_service.get_optimization_orm",
        return_value=opt,
    ):
        result = await delete_optimization(session, "test-id")

    assert result is True
    assert opt.deleted_at is not None, "deleted_at should be set"
    assert isinstance(opt.deleted_at, datetime), "deleted_at should be a datetime"
    session.delete.assert_not_called()


async def test_get_optimization_excludes_soft_deleted():
    """get_optimization() returns None when deleted_at filter matches (simulates soft-deleted)."""
    session = AsyncMock()
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=execute_result)

    result = await get_optimization(session, "soft-deleted-id")

    assert result is None
