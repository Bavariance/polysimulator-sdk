"""Async floor-pacing must hold under concurrency.

The async transport's ``_pace`` reads ``_last_request_ts``, sleeps, then writes
it back. Without a lock around that read-sleep-write, ``asyncio.gather``-ed
requests all read the same stale timestamp, all compute the same tiny wait, and
all fire in one burst — defeating the floor that exists to keep a tight loop
from tripping the per-second rate bucket. With serialization, N concurrent
calls pace one-after-another.

This is a real-clock timing test (no mocked sleep): serialized pacing makes the
wall time scale with N; the racy version collapses to ~one floor interval.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from polysim_sdk import AsyncPolySimClient

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"
FLOOR = 0.1
N = 5


async def test_async_pacing_is_serialized_under_gather(respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(200, json={"id": "ok"})
    )
    c = AsyncPolySimClient(api_key=API_KEY, base_url=BASE_URL, floor_interval=FLOOR)
    try:
        start = time.monotonic()
        await asyncio.gather(*(c.me() for _ in range(N)))
        elapsed = time.monotonic() - start
    finally:
        await c.aclose()

    # Serialized: (N-1) floor gaps between the N calls. Racy (no lock): all but
    # the first read the same stale ts and sleep in parallel -> ~one FLOOR.
    # Threshold sits well above the racy ceiling and below the serialized floor.
    min_serialized = (N - 1) * FLOOR  # 0.40s
    assert elapsed >= min_serialized * 0.75, (
        f"floor pacing not serialized under gather: {elapsed:.3f}s "
        f"(expected >= {min_serialized * 0.75:.3f}s)"
    )
