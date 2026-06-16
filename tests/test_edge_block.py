"""CDN edge / WAF block detection (Cloudflare ``error code: 1XXX``).

A request that reaches the CDN edge with a blocked signature — e.g. Python's
stdlib ``urllib`` default User-Agent ``Python-urllib/x.y`` — gets a ``403``
whose body is the plain-text Cloudflare marker ``error code: 1010``, NOT our
JSON ``{error, code, message}`` shape. The transport must recognise this and
raise a clear, typed :class:`EdgeBlockedError` (an :class:`ApiError`) instead
of an opaque ``HTTP 403: Forbidden``, so a beta user immediately sees it's an
edge / User-Agent block and not an auth failure.

This SDK already sends a branded UA (``polysim-sdk/...``) that passes the edge,
so SDK users won't normally trip this — the detection is defence-in-depth for
overridden UAs, UA-rewriting proxies, or the edge tightening its rules.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from polysim_sdk import ApiError, EdgeBlockedError, PolySimError
from polysim_sdk._http import SyncTransport, raise_for_status


def _resp(status: int, *, text: str = "", json_body=None, headers=None) -> httpx.Response:
    req = httpx.Request("GET", "https://api.polysimulator.test/v1/markets")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req, headers=headers or {})
    return httpx.Response(status, text=text, request=req, headers=headers or {})


class TestEdgeBlockDetection:
    def test_cf_1010_raises_edge_blocked(self):
        with pytest.raises(EdgeBlockedError) as ei:
            raise_for_status(_resp(403, text="error code: 1010"))
        err = ei.value
        assert err.status_code == 403
        assert err.code == "EDGE_BLOCKED"
        msg = str(err).lower()
        assert "edge" in msg or "cloudflare" in msg
        assert "user-agent" in msg
        assert "1010" in msg  # surface the specific CF code for lookup

    def test_edge_blocked_is_an_api_error_and_polysim_error(self):
        err = EdgeBlockedError(status_code=403, message="x", code="EDGE_BLOCKED")
        assert isinstance(err, ApiError)
        assert isinstance(err, PolySimError)

    def test_other_cf_1xxx_codes_also_detected(self):
        with pytest.raises(EdgeBlockedError):
            raise_for_status(_resp(403, text="error code: 1020"))

    def test_real_json_403_stays_plain_api_error(self):
        # Our backend's own 403 is JSON — it must NOT be misread as an edge block.
        with pytest.raises(ApiError) as ei:
            raise_for_status(_resp(403, json_body={"error": "FORBIDDEN", "message": "no access"}))
        assert not isinstance(ei.value, EdgeBlockedError)
        assert ei.value.code == "FORBIDDEN"

    def test_plain_403_without_cf_marker_stays_plain(self):
        with pytest.raises(ApiError) as ei:
            raise_for_status(_resp(403, text="Forbidden"))
        assert not isinstance(ei.value, EdgeBlockedError)


@respx.mock
def test_transport_surfaces_edge_blocked_end_to_end():
    """A 403 edge block is deterministic — it must raise immediately (no retry
    burn) and surface as :class:`EdgeBlockedError` through the transport."""
    route = respx.route(method="GET").mock(
        return_value=httpx.Response(403, text="error code: 1010")
    )
    t = SyncTransport(api_key="k", base_url="https://api.polysimulator.test", floor_interval=0.0)
    try:
        with pytest.raises(EdgeBlockedError):
            t.request("GET", "/v1/markets")
    finally:
        t.close()
    assert route.call_count == 1  # not retried
