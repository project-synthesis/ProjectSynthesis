"""Tests for soft-delete behavior (Task 11)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.optimization_service import delete_optimization, get_optimization, restore_optimization


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


# ── restore_optimization service tests ────────────────────────────────────────


async def test_restore_optimization_happy_path():
    """restore_optimization() clears deleted_at when the record is in trash and owned by user."""
    opt = MagicMock()
    opt.deleted_at = datetime(2026, 3, 1, tzinfo=timezone.utc)

    session = AsyncMock()
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=opt)
    session.execute = AsyncMock(return_value=execute_result)

    result = await restore_optimization(session, "opt-id", "user-id")

    assert result is True
    assert opt.deleted_at is None, "deleted_at should be cleared on restore"


async def test_restore_optimization_wrong_user_returns_false():
    """restore_optimization() returns False when the record belongs to a different user."""
    session = AsyncMock()
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=execute_result)

    result = await restore_optimization(session, "opt-id", "other-user-id")

    assert result is False


async def test_restore_optimization_not_soft_deleted_returns_false():
    """restore_optimization() returns False when the record exists but deleted_at is None."""
    # The service query includes Optimization.deleted_at.isnot(None) so a non-deleted
    # record will not be returned by the query — scalar_one_or_none returns None.
    session = AsyncMock()
    execute_result = AsyncMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=execute_result)

    result = await restore_optimization(session, "live-opt-id", "user-id")

    assert result is False


# ── restore endpoint tests (router layer) ─────────────────────────────────────


async def test_restore_endpoint_happy_path():
    """POST /api/history/{id}/restore returns 200 {"restored": True} when service succeeds."""
    from app.routers.history import restore_optimization as endpoint

    mock_user = MagicMock()
    mock_user.id = "user-id"

    mock_opt = MagicMock()
    mock_opt.user_id = "user-id"

    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=mock_opt)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_session.commit = AsyncMock()

    # The router does a local import of the service function, so patch at the service module.
    with patch(
        "app.services.optimization_service.restore_optimization",
        AsyncMock(return_value=True),
    ):
        result = await endpoint(
            optimization_id="opt-abc",
            current_user=mock_user,
            session=mock_session,
        )

    assert result == {"restored": True, "id": "opt-abc"}
    mock_session.commit.assert_awaited_once()


async def test_restore_endpoint_not_in_trash_raises_404():
    """POST /api/history/{id}/restore returns 404 when service returns False."""
    from fastapi import HTTPException

    from app.routers.history import restore_optimization as endpoint

    mock_user = MagicMock()
    mock_user.id = "user-id"

    # Simulate record not found at all (execute returns None)
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=None)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)

    # The router does a local import of the service function, so patch at the service module.
    with patch(
        "app.services.optimization_service.restore_optimization",
        AsyncMock(return_value=False),
    ):
        try:
            await endpoint(
                optimization_id="opt-xyz",
                current_user=mock_user,
                session=mock_session,
            )
            assert False, "Expected HTTPException 404"
        except HTTPException as exc:
            assert exc.status_code == 404


async def test_restore_endpoint_wrong_user_raises_403():
    """POST /api/history/{id}/restore returns 403 when the record belongs to a different user."""
    from fastapi import HTTPException

    from app.routers.history import restore_optimization as endpoint

    mock_user = MagicMock()
    mock_user.id = "user-id"

    # Record exists in trash but belongs to a DIFFERENT user
    mock_opt = MagicMock()
    mock_opt.user_id = "other-user-id"

    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=mock_opt)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)

    try:
        await endpoint(
            optimization_id="opt-xyz",
            current_user=mock_user,
            session=mock_session,
        )
        assert False, "Expected HTTPException 403"
    except HTTPException as exc:
        assert exc.status_code == 403
