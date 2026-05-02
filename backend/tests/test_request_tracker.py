import asyncio

import pytest

from app.services.request_tracker import RequestTracker


@pytest.mark.asyncio
async def test_request_tracker_counts_in_flight_requests():
    tracker = RequestTracker()

    assert tracker.in_flight == 0

    tracker.add_request()
    assert tracker.in_flight == 1

    tracker.add_request()
    assert tracker.in_flight == 2

    tracker.remove_request()
    assert tracker.in_flight == 1

    tracker.remove_request()
    assert tracker.in_flight == 0

@pytest.mark.asyncio
async def test_request_tracker_wait_for_drain():
    tracker = RequestTracker()

    # Should return immediately if empty
    await asyncio.wait_for(tracker.wait_for_drain(timeout=1.0), timeout=0.1)

    tracker.add_request()

    # Wait should block while there's a request
    async def wait_task():
        await tracker.wait_for_drain(timeout=5.0)

    task = asyncio.create_task(wait_task())

    # Give it a tiny bit of time to block
    await asyncio.sleep(0.01)
    assert not task.done()

    tracker.remove_request()

    # Now it should finish
    await asyncio.wait_for(task, timeout=0.1)

@pytest.mark.asyncio
async def test_request_tracker_wait_for_drain_timeout():
    tracker = RequestTracker()
    tracker.add_request()

    # Should not raise exception, just return False on timeout
    drained = await tracker.wait_for_drain(timeout=0.01)
    assert not drained
    assert tracker.in_flight == 1
