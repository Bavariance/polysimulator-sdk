"""Shared test fixtures.

All tests are respx-mocked: no real network, no credentials, no prod writes.
Floor pacing is zeroed in fixtures so the suite stays fast.
"""

from __future__ import annotations

import pytest

from polysim_sdk import AsyncPolySimClient, PolySimClient

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
def client() -> PolySimClient:
    c = PolySimClient(api_key=API_KEY, base_url=BASE_URL, floor_interval=0.0)
    yield c
    c.close()


@pytest.fixture
async def aclient() -> AsyncPolySimClient:
    c = AsyncPolySimClient(api_key=API_KEY, base_url=BASE_URL, floor_interval=0.0)
    yield c
    await c.aclose()
