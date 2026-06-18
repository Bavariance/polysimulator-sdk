"""Behavioral tests for the concrete ``AsyncSubscriptionHandle`` and the public
``SubscriptionHandle`` protocol.

No transport, no network — these drive the handle's queue directly via the
internal ``_push`` / ``_end`` / ``_bind_close`` seams the transport uses.
"""

from __future__ import annotations

import asyncio

import pytest

from polysim_polymarket.streams import SubscriptionHandle
from polysim_polymarket.streams._handle import AsyncSubscriptionHandle


def test_subscription_handle_is_runtime_checkable() -> None:
    assert isinstance(AsyncSubscriptionHandle(queue_size=4), SubscriptionHandle)


def test_queue_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        AsyncSubscriptionHandle(queue_size=0)


async def test_push_then_iterate_in_order() -> None:
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=8)
    for i in range(3):
        h._push(i)
    h._end()
    got = [ev async for ev in h]
    assert got == [0, 1, 2]


async def test_drop_oldest_backpressure_increments_dropped() -> None:
    # queue_size=2. push 1,2 -> [1,2]; push 3 -> drop 1 -> [2,3];
    # push 4 -> drop 2 -> [3,4]. _end() also needs a slot for the END
    # sentinel -> drop oldest (3) -> [4, END]. So 3 events are dropped and
    # only the newest survivor (4) is yielded. This mirrors py-sdk's
    # AsyncSubscriptionHandle exactly (the end sentinel shares the queue).
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=2)
    h._push(1)
    h._push(2)
    h._push(3)  # full -> drops oldest (1)
    h._push(4)  # full -> drops oldest (2)
    h._end()  # full -> drops oldest (3) to seat the END sentinel
    got = [ev async for ev in h]
    assert got == [4]
    assert h.dropped == 3


async def test_close_is_idempotent_and_terminates_iteration() -> None:
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._push(7)
    await h.close()
    await h.close()  # second close is a no-op, must not raise
    got = [ev async for ev in h]
    assert got == [7]


async def test_close_runs_bound_on_close_once() -> None:
    calls: list[str] = []

    async def on_close(_h: AsyncSubscriptionHandle[int]) -> None:
        calls.append("closed")

    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._bind_close(on_close)
    await h.close()
    await h.close()
    assert calls == ["closed"]


async def test_on_close_exception_is_suppressed_handle_still_terminal() -> None:
    async def boom(_h: AsyncSubscriptionHandle[int]) -> None:
        raise RuntimeError("close failed")

    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._bind_close(boom)
    await h.close()  # must not raise
    got = [ev async for ev in h]
    assert got == []


async def test_end_with_error_raises_at_end_of_stream() -> None:
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._push(1)
    h._end(ValueError("stream broke"))
    collected: list[int] = []
    with pytest.raises(ValueError, match="stream broke"):
        async for ev in h:
            collected.append(ev)
    assert collected == [1]


async def test_async_context_manager_closes_on_exit() -> None:
    calls: list[str] = []

    async def on_close(_h: AsyncSubscriptionHandle[int]) -> None:
        calls.append("closed")

    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._bind_close(on_close)
    async with h as ctx:
        assert ctx is h
    assert calls == ["closed"]


async def test_push_after_end_is_ignored() -> None:
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
    h._end()
    h._push(99)  # ignored
    got = [ev async for ev in h]
    assert got == []


async def test_concurrent_consumer_awaits_until_push() -> None:
    h: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)

    async def produce() -> None:
        await asyncio.sleep(0.01)
        h._push(42)
        h._end()

    task = asyncio.create_task(produce())
    got = [ev async for ev in h]
    await task
    assert got == [42]
