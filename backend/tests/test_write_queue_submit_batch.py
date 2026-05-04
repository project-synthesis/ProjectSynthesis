"""v0.4.14 cycle 1 — submit_batch helper unit tests.

Pins the binding choices in spec § 12 rows 1-7. All tests should fail until
WriteQueue.submit_batch() and the SubmitBatchError/SubmitBatchCommitError
classes exist.
"""
from __future__ import annotations

import asyncio
import functools

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class TestSubmitBatchTransaction:
    """Spec § 12 row 1 — single transaction via async with db.begin()."""

    async def test_submit_batch_runs_in_single_transaction(self, write_queue_inmem):
        async def w1(db: AsyncSession) -> int:
            await db.execute(text("CREATE TABLE IF NOT EXISTS sb_t (n INTEGER)"))
            await db.execute(text("INSERT INTO sb_t VALUES (1)"))
            return 1

        async def w2(db: AsyncSession) -> int:
            await db.execute(text("INSERT INTO sb_t VALUES (2)"))
            return 2

        results = await write_queue_inmem.submit_batch([w1, w2])
        assert results == [1, 2]


class TestSubmitBatchRollback:
    """Spec § 12 row 2 — any work_fn failure rolls back ALL writes."""

    async def test_submit_batch_rolls_back_on_partial_failure(self, write_queue_inmem):
        from app.services.write_queue import SubmitBatchError

        async def w1(db: AsyncSession) -> None:
            await db.execute(text("CREATE TABLE IF NOT EXISTS sb_rb (n INTEGER)"))
            await db.execute(text("INSERT INTO sb_rb VALUES (1)"))

        async def w2_fails(db: AsyncSession) -> None:
            await db.execute(text("INSERT INTO sb_rb VALUES (2)"))
            raise RuntimeError("intentional failure")

        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([w1, w2_fails])
        assert excinfo.value.index == 1
        assert isinstance(excinfo.value.original, RuntimeError)
        assert "intentional failure" in str(excinfo.value.original)

        async def verify(db: AsyncSession) -> int:
            res = await db.execute(text("SELECT COUNT(*) FROM sb_rb"))
            return res.scalar_one()

        count = await write_queue_inmem.submit(verify)
        assert count == 0, "transaction must have rolled back ALL prior writes"


class TestSubmitBatchOrdering:
    """Spec § 12 row 3 — work_fns run serially in caller-supplied order."""

    async def test_submit_batch_preserves_caller_order(self, write_queue_inmem):
        observed: list[int] = []

        def make(i: int):
            async def w(db: AsyncSession) -> int:
                observed.append(i)
                await asyncio.sleep(0.001 * (5 - i))
                return i
            return w

        work_fns = [make(i) for i in range(5)]
        results = await write_queue_inmem.submit_batch(work_fns)
        assert results == [0, 1, 2, 3, 4]
        assert observed == [0, 1, 2, 3, 4]


class TestSubmitBatchCommitForbidden:
    """Spec § 12 row 4 — work_fn calling db.commit() raises SubmitBatchCommitError."""

    async def test_submit_batch_commit_inside_work_fn_raises(self, write_queue_inmem):
        from app.services.write_queue import SubmitBatchCommitError, SubmitBatchError

        async def naughty(db: AsyncSession) -> None:
            await db.execute(text("CREATE TABLE IF NOT EXISTS sb_cmt (n INTEGER)"))
            await db.commit()

        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([naughty])
        assert isinstance(excinfo.value.original, SubmitBatchCommitError)


class TestSubmitBatchErrorShape:
    """Spec § 12 rows 5-6 — diagnostic context + lambda/partial fn_name fallback."""

    async def test_submit_batch_error_carries_diagnostic_context(self, write_queue_inmem):
        from app.services.write_queue import SubmitBatchError

        async def named_fn(db: AsyncSession) -> None:
            raise ValueError("boom")

        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([named_fn])
        assert excinfo.value.index == 0
        assert excinfo.value.fn_name == "named_fn"
        assert isinstance(excinfo.value.original, ValueError)
        assert "named_fn" in str(excinfo.value)

    async def test_submit_batch_error_carries_fn_name_for_lambda_and_partial(
        self, write_queue_inmem,
    ):
        from app.services.write_queue import SubmitBatchError

        async def base(_db: AsyncSession, *, msg: str) -> None:
            raise RuntimeError(msg)

        partial_fn = functools.partial(base, msg="from_partial")
        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([partial_fn])
        # repr fallback identifies the wrapped callable
        assert "base" in excinfo.value.fn_name or "partial" in excinfo.value.fn_name.lower()

        async def lam_body(db: AsyncSession) -> None:
            raise KeyError("lam")
        # Lambdas surface as "<lambda>" via __name__
        the_lambda = lambda db: lam_body(db)  # noqa: E731
        with pytest.raises(SubmitBatchError) as excinfo2:
            await write_queue_inmem.submit_batch([the_lambda])
        assert excinfo2.value.fn_name == "<lambda>"


class TestSubmitBatchReentrancy:
    """Spec § 12 row 7 — nested submit/submit_batch from work_fn raises reentrancy.

    submit_batch routes through submit(_do_batch); inside _do_batch (running on
    the worker), nested submit/submit_batch hits write_queue.py:307-312 reentrancy
    guard.
    """

    async def test_submit_batch_inside_work_fn_raises_reentrancy(self, write_queue_inmem):
        from app.services.write_queue import (
            SubmitBatchError,
            WriteQueueReentrancyError,
        )

        async def inner(db: AsyncSession) -> None:
            return None

        async def outer(db: AsyncSession) -> None:
            await write_queue_inmem.submit(inner)

        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([outer])
        assert isinstance(excinfo.value.original, WriteQueueReentrancyError)

    async def test_nested_submit_batch_raises_reentrancy(self, write_queue_inmem):
        from app.services.write_queue import (
            SubmitBatchError,
            WriteQueueReentrancyError,
        )

        async def inner(db: AsyncSession) -> None:
            return None

        async def outer(db: AsyncSession) -> None:
            await write_queue_inmem.submit_batch([inner])

        with pytest.raises(SubmitBatchError) as excinfo:
            await write_queue_inmem.submit_batch([outer])
        assert isinstance(excinfo.value.original, WriteQueueReentrancyError)


class TestSubmitBatchEmptyAndSingle:
    """Edge cases — empty list + single fn."""

    async def test_submit_batch_empty_returns_empty_list(self, write_queue_inmem):
        results = await write_queue_inmem.submit_batch([])
        assert results == []

    async def test_submit_batch_single_fn_returns_single_result(self, write_queue_inmem):
        async def w(db: AsyncSession) -> str:
            return "ok"
        results = await write_queue_inmem.submit_batch([w])
        assert results == ["ok"]
