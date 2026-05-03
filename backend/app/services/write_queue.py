"""Single-writer queue worker for SQLite write contention elimination.
See docs/specs/sqlite-writer-queue-2026-05-02.md."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T")
_SHUTDOWN_SENTINEL = object()


class WriteQueueStoppedError(RuntimeError): ...
class WriteQueueOverloadedError(RuntimeError): ...
class WriteQueueDeadError(RuntimeError): ...
class WriteQueueReentrancyError(RuntimeError): ...


def _emit_worker_event(decision: str, context: dict[str, Any]) -> None:
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="write_queue", op="worker", decision=decision, context=context,
        )
    except RuntimeError:
        pass
    except Exception:
        logger.debug("worker event emit failed", exc_info=True)


def _emit_complete_event(
    decision: str, label: str | None, latency_ms: int, exc: BaseException | None,
) -> None:
    context: dict[str, Any] = {"op_label": label, "latency_ms": latency_ms}
    if exc is not None:
        context["error_class"] = type(exc).__name__
        context["error_msg"] = str(exc)[:200]
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="write_queue", op="complete", decision=decision, context=context,
        )
    except RuntimeError:
        pass
    except Exception:
        logger.debug("complete event emit failed", exc_info=True)


@dataclass(frozen=True)
class WriteQueueMetrics:
    depth: int
    in_flight: bool
    total_submitted: int
    total_completed: int
    total_failed: int
    total_timeout: int
    total_overload: int
    p95_latency_ms: float
    p99_latency_ms: float
    max_observed_depth: int
    worker_alive: bool
    metrics_window_seconds: float
    metrics_sample_count: int


class _MetricsTracker:
    def __init__(self) -> None:
        self.total_submitted = 0
        self.total_completed = 0
        self.total_failed = 0
        self.total_timeout = 0
        self.total_overload = 0
        self.max_observed_depth = 0
        self._latency_samples: list[tuple[float, float]] = []

    def record_submit(self, current_depth: int) -> None:
        self.total_submitted += 1
        if current_depth > self.max_observed_depth:
            self.max_observed_depth = current_depth

    def record_overload(self) -> None:
        self.total_overload += 1

    def record_success(self, label: str | None, latency_s: float) -> None:
        self.total_completed += 1
        self._add_latency_sample(latency_s)

    def record_failure(self, label: str | None, latency_s: float, exc: BaseException) -> None:
        self.total_failed += 1
        if isinstance(exc, asyncio.TimeoutError):
            self.total_timeout += 1
        self._add_latency_sample(latency_s)

    def _add_latency_sample(self, latency_s: float) -> None:
        now = time.monotonic()
        cutoff = now - settings.WRITE_QUEUE_RESERVOIR_WINDOW_SECONDS
        self._latency_samples = [(t, s) for t, s in self._latency_samples if t >= cutoff]
        if len(self._latency_samples) >= settings.WRITE_QUEUE_RESERVOIR_SIZE:
            idx = random.randrange(len(self._latency_samples))
            self._latency_samples[idx] = (now, latency_s)
        else:
            self._latency_samples.append((now, latency_s))

    def snapshot(self, *, depth: int, in_flight: bool, worker_alive: bool) -> WriteQueueMetrics:
        samples = sorted(s for _, s in list(self._latency_samples))
        if samples:
            p95 = samples[int(0.95 * (len(samples) - 1))] * 1000.0
            p99 = samples[int(0.99 * (len(samples) - 1))] * 1000.0
        else:
            p95 = p99 = 0.0
        return WriteQueueMetrics(
            depth=depth, in_flight=in_flight, total_submitted=self.total_submitted,
            total_completed=self.total_completed, total_failed=self.total_failed,
            total_timeout=self.total_timeout, total_overload=self.total_overload,
            p95_latency_ms=p95, p99_latency_ms=p99,
            max_observed_depth=self.max_observed_depth, worker_alive=worker_alive,
            metrics_window_seconds=settings.WRITE_QUEUE_RESERVOIR_WINDOW_SECONDS,
            metrics_sample_count=len(samples),
        )


QueueItem = tuple[
    Callable[[AsyncSession], Awaitable[Any]],
    asyncio.Future,
    str | None,
    float,
]


class WriteQueue:
    def __init__(
        self,
        writer_engine: AsyncEngine,
        *,
        max_depth: int | None = None,
        default_timeout: float | None = None,
    ) -> None:
        self._writer_engine = writer_engine
        self._writer_session_factory = async_sessionmaker(
            writer_engine, class_=AsyncSession, expire_on_commit=False,
        )
        self._max_depth = max_depth or settings.WRITE_QUEUE_MAX_QUEUE_DEPTH
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_depth)
        self._default_timeout = default_timeout or settings.WRITE_QUEUE_DEFAULT_TIMEOUT_SECONDS
        self._worker_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._stopping: bool = False
        self._stop_done: asyncio.Event = asyncio.Event()
        self._dead: bool = False
        self._respawns_in_window: list[float] = []
        self._inflight_label: str | None = None
        self._metrics: _MetricsTracker = _MetricsTracker()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize() + (1 if self._inflight_label is not None else 0)

    @property
    def in_flight(self) -> bool:
        return self._inflight_label is not None

    @property
    def worker_alive(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done() and not self._dead

    def metrics_snapshot(self) -> WriteQueueMetrics:
        return self._metrics.snapshot(
            depth=self.queue_depth, in_flight=self.in_flight, worker_alive=self.worker_alive,
        )

    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._supervisor_task = asyncio.create_task(self._supervisor())
        _emit_worker_event("started", {})

    async def submit(
        self,
        work: Callable[[AsyncSession], Awaitable[T]],
        *,
        timeout: float | None = None,
        operation_label: str | None = None,
    ) -> T:
        """Submit ``work`` to the single-writer queue and await its result.

        ``work`` is a coroutine factory that takes a freshly-opened
        ``AsyncSession`` bound to the writer engine and performs all DB
        mutations the caller needs serialized against every other writer.
        The session is opened by the worker (NOT the caller), entered via
        ``__aenter__``, passed to ``work``, then exited under
        ``asyncio.shield`` so cleanup survives worker cancellation.

        Parameters
        ----------
        work : Callable[[AsyncSession], Awaitable[T]]
            The unit of work. MUST commit (or explicitly rollback) before
            returning — the queue does not commit on the caller's behalf.
            Receives an open ``AsyncSession``; returns ``T``.
        timeout : float | None, keyword-only, default ``None``
            Per-submit deadline in seconds. ``None`` falls back to
            ``settings.WRITE_QUEUE_DEFAULT_TIMEOUT_SECONDS`` (300s). The
            worker wraps ``work(db)`` in ``asyncio.wait_for`` so a runaway
            callback cannot wedge the queue forever.
        operation_label : str | None, keyword-only, default ``None``
            Free-form label for metrics + observability events. Surfaces
            in ``WriteQueueMetrics`` snapshots and the ``write_queue``
            decision events emitted on completion.

        Returns
        -------
        T
            The value returned by ``work(db)``. Whatever the callback
            yields is passed through unchanged.

        Raises
        ------
        WriteQueueDeadError
            The worker crashed twice within 60s and the queue declared
            itself dead. All future ``submit()`` calls raise this until
            the queue is replaced. Pending work is failed with the same
            exception.
        WriteQueueStoppedError
            ``stop()`` is in progress or completed, OR ``start()`` was
            never called. Recoverable only by spinning up a new queue.
        WriteQueueReentrancyError
            ``submit()`` was invoked from inside the worker task itself
            (i.e., from within a ``work`` callback that is currently
            executing on the worker). Such a call would deadlock — the
            worker is blocked on the outer ``work`` and would never get
            around to running the inner submission. See "Reentrancy
            patterns" below for the canonical alternative.
        WriteQueueOverloadedError
            The queue is at ``max_depth`` (default 256). The submit was
            rejected synchronously without enqueuing. Callers should
            shed load (degrade, retry with backoff) rather than spin.
        asyncio.TimeoutError
            ``work(db)`` did not complete within ``timeout``. The worker
            cancels ``work``, runs session cleanup under shield, and
            propagates the timeout to the caller.

        Reentrancy patterns
        -------------------
        Three concurrency patterns matter. Two deadlock; one is canonical.

        1. **DIRECT REENTRANCY (FORBIDDEN — raises ``WriteQueueReentrancyError``).**
           ``work`` calls ``submit`` synchronously. The worker is blocked
           on the outer ``work`` so the inner submit can never run::

               async def outer(db):
                   async def inner(db2):
                       return None
                   await queue.submit(inner)   # raises immediately
               await queue.submit(outer)

        2. **SPAWN-AND-AWAIT FROM INSIDE work (FORBIDDEN — DEADLOCKS).**
           ``work`` spawns a background task that calls ``submit`` and
           awaits its Future. The reentrancy guard does NOT catch this
           (the task is a different ``asyncio.current_task()``), but the
           worker is still blocked on the outer ``work`` so the inner
           submit's Future never resolves. The whole chain hangs until
           the per-submit timeout fires::

               async def outer(db):
                   async def inner(db2):
                       return None
                   t = asyncio.create_task(queue.submit(inner))
                   return await t                # DEADLOCK — never resolves

        3. **SPAWN-FROM-OUTSIDE work (CANONICAL).**
           Regular orchestration code (NOT a ``work`` callback running on
           the worker) spawns tasks that call ``submit``. This is the
           pattern probe_service uses — the on-progress callback runs on
           the caller's event loop, OUTSIDE any ``work``::

               async def _persist_one(idx: int):
                   async def _do(db: AsyncSession):
                       await db.execute(text("INSERT ..."))
                       await db.commit()
                   await queue.submit(_do)

               # Orchestration code (NOT inside any work callback):
               tasks = [asyncio.create_task(_persist_one(i)) for i in range(N)]
               await asyncio.gather(*tasks)

        When you have multi-step DB work, compose it into a single
        ``work`` callback rather than chaining nested ``submit()`` calls.
        """
        if self._dead:
            raise WriteQueueDeadError("queue worker died and could not respawn")
        if self._stopping:
            raise WriteQueueStoppedError("queue is shutting down")
        if self._worker_task is None:
            raise WriteQueueStoppedError(
                "queue not started yet — call WriteQueue.start() before submit()"
            )
        current = asyncio.current_task()
        if current is self._worker_task:
            raise WriteQueueReentrancyError(
                "submit() called from within the worker task — would deadlock. "
                "Compose multi-step work into a single callback instead."
            )
        timeout_s = timeout if timeout is not None else self._default_timeout
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        try:
            self._queue.put_nowait((work, future, operation_label, timeout_s))
            self._metrics.record_submit(current_depth=self.queue_depth)
        except asyncio.QueueFull:
            self._metrics.record_overload()
            raise WriteQueueOverloadedError(
                f"queue at max depth {self._max_depth}; degrade or retry later"
            ) from None
        return await future

    def _record_success(self, label: str | None, latency_s: float) -> None:
        self._metrics.record_success(label, latency_s)
        _emit_complete_event("success", label, int(latency_s * 1000), exc=None)

    def _record_failure(
        self, label: str | None, latency_s: float, exc: BaseException,
    ) -> None:
        self._metrics.record_failure(label, latency_s, exc)
        decision = "timeout" if isinstance(exc, asyncio.TimeoutError) else "failed"
        _emit_complete_event(decision, label, int(latency_s * 1000), exc)

    def _fail_all_pending(self, exc: BaseException) -> None:
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            if item is _SHUTDOWN_SENTINEL:
                continue
            _, future, _, _ = item
            if not future.done():
                with contextlib.suppress(asyncio.InvalidStateError):
                    future.set_exception(exc)

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN_SENTINEL:
                self._queue.task_done()
                return
            self._inflight_label = item[2]
            await self._run_one(item)

    async def _run_one(self, item: QueueItem) -> None:
        work, future, label, deadline = item
        try:
            db = self._writer_session_factory()
            try:
                await db.__aenter__()
                t0 = time.monotonic()
                try:
                    result = await asyncio.wait_for(work(db), timeout=deadline)
                except BaseException as exc:
                    elapsed = time.monotonic() - t0
                    await asyncio.shield(db.__aexit__(type(exc), exc, exc.__traceback__))
                    self._record_failure(label, elapsed, exc)
                    if not future.done():
                        with contextlib.suppress(asyncio.InvalidStateError):
                            future.set_exception(exc)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    return
                elapsed = time.monotonic() - t0
                await asyncio.shield(db.__aexit__(None, None, None))
                self._record_success(label, elapsed)
                if not future.done():
                    with contextlib.suppress(asyncio.InvalidStateError):
                        future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    with contextlib.suppress(asyncio.InvalidStateError):
                        future.set_exception(WriteQueueDeadError("worker cancelled"))
                raise
        finally:
            self._inflight_label = None
            self._queue.task_done()

    async def stop(self, *, drain_timeout: float | None = None) -> None:
        if self._stop_done.is_set():
            return
        if self._stopping:
            await self._stop_done.wait()
            return
        self._stopping = True
        timeout_s = (
            drain_timeout
            if drain_timeout is not None
            else settings.WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS
        )
        # Bind worker_task to a local so type narrowing survives the
        # except branches below — mypy can't track ``self._worker_task``
        # through ``except asyncio.TimeoutError`` re-entry, but a local
        # variable's narrowing is stable.
        worker_task = self._worker_task
        try:
            self._queue.put_nowait(_SHUTDOWN_SENTINEL)
            try:
                if worker_task is not None:
                    await asyncio.wait_for(worker_task, timeout=timeout_s)
            except asyncio.TimeoutError:
                if worker_task is not None:
                    worker_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker_task
                self._fail_all_pending(WriteQueueDeadError("drain timeout exceeded"))
            except BaseException as exc:
                self._fail_all_pending(
                    WriteQueueDeadError(f"worker died during drain: {exc}")
                )
            if self._supervisor_task and not self._supervisor_task.done():
                self._supervisor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._supervisor_task
            _emit_worker_event("stopped", {})
        finally:
            self._stop_done.set()

    async def _supervisor(self) -> None:
        # Supervisor is only started by ``start()`` AFTER ``_worker_task``
        # is created, so it is non-None for the lifetime of this loop.
        # Re-bound after each respawn at the bottom of the loop.
        while not self._stopping and not self._dead:
            assert self._worker_task is not None, "supervisor started without worker"
            try:
                await self._worker_task
                return
            except asyncio.CancelledError:
                return
            except BaseException as exc:
                now = time.monotonic()
                self._respawns_in_window = [
                    t for t in self._respawns_in_window if now - t < 60.0
                ]
                self._respawns_in_window.append(now)
                self._metrics.record_failure(self._inflight_label, 0.0, exc)
                if len(self._respawns_in_window) > 1:
                    self._dead = True
                    logger.error(
                        "WriteQueue worker crashed twice in 60s; declaring queue dead",
                        exc_info=exc,
                    )
                    self._fail_all_pending(WriteQueueDeadError("worker died twice in 60s"))
                    _emit_worker_event("dead", {
                        "exception": f"{type(exc).__name__}: {str(exc)[:200]}",
                    })
                    return
                logger.error(
                    "WriteQueue worker crashed; respawning once", exc_info=exc,
                )
                _emit_worker_event("respawned", {
                    "exception": f"{type(exc).__name__}: {str(exc)[:200]}",
                })
                self._worker_task = asyncio.create_task(self._worker_loop())


__all__ = [
    "WriteQueue",
    "WriteQueueStoppedError",
    "WriteQueueOverloadedError",
    "WriteQueueDeadError",
    "WriteQueueReentrancyError",
    "WriteQueueMetrics",
]
