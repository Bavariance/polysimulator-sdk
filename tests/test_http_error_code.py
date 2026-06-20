"""``ApiError.code`` is sourced from the ``X-Polysim-Code`` response header.

The backend's default error envelope is the Polymarket single-field shape
``{"error": "<human message>"}`` with the **machine** code carried in the
``X-Polysim-Code`` response header (always exposed). The human ``error`` string
is a sentence, not a stable code — e.g. a warm-up 404 has body
``{"error": "Market not found: 0x… <guidance>"}`` and header
``X-Polysim-Code: MARKET_NOT_FOUND``.

So ``raise_for_status`` must prefer the header for ``ApiError.code`` and only
fall back to the body's ``error``/``code`` field when the header is absent.
Without the header preference, ``exc.code`` becomes the human message, which
breaks code-based dispatch like the warm-up retry helper's
``exc.code == "MARKET_NOT_FOUND"`` predicate.
"""

from __future__ import annotations

import httpx
import pytest

from polysim_sdk import ApiError, ValidationError
from polysim_sdk._http import raise_for_status


def _resp(status: int, *, text: str = "", json_body=None, headers=None) -> httpx.Response:
    req = httpx.Request("GET", "https://api.polysimulator.test/v1/orders")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req, headers=headers or {})
    return httpx.Response(status, text=text, request=req, headers=headers or {})


# The live warm-up 404: header carries the machine code, body carries only a
# human sentence. The full sentence is what a real user traceback showed
# leaking into ``exc.code`` before the header preference was added.
_WARMUP_BODY = {
    "error": (
        "Market not found: 0x68281f. The market_id field must be a Polymarket "
        "condition_id present in our market catalog. Use GET /v1/markets to list "
        "known markets."
    )
}


class TestErrorCodeFromHeader:
    def test_code_prefers_x_polysim_code_header(self):
        # Live envelope: header = machine code, body = human message only.
        with pytest.raises(ApiError) as ei:
            raise_for_status(
                _resp(404, json_body=_WARMUP_BODY, headers={"X-Polysim-Code": "MARKET_NOT_FOUND"})
            )
        err = ei.value
        # The authoritative machine code — NOT the human message from the body.
        assert err.code == "MARKET_NOT_FOUND"
        assert err.code != _WARMUP_BODY["error"]
        # The human message still surfaces as the exception text.
        assert "Market not found" in str(err)

    def test_header_read_case_insensitively_and_stripped(self):
        # httpx headers are case-insensitive; a proxy may pad the value.
        with pytest.raises(ApiError) as ei:
            raise_for_status(
                _resp(
                    404,
                    json_body=_WARMUP_BODY,
                    headers={"x-polysim-code": "  MARKET_NOT_FOUND  "},
                )
            )
        assert ei.value.code == "MARKET_NOT_FOUND"

    def test_falls_back_to_body_error_when_header_absent(self):
        # No header → preserve the prior behaviour (body-derived code), so a
        # backend that inlines ``{"error": "<CODE>", "message": "<text>"}`` (the
        # back-compat shape) still yields the code.
        with pytest.raises(ApiError) as ei:
            raise_for_status(
                _resp(
                    404,
                    json_body={"error": "MARKET_NOT_FOUND", "message": "Market not found"},
                )
            )
        assert ei.value.code == "MARKET_NOT_FOUND"

    def test_falls_back_to_body_code_when_header_absent(self):
        # The explicit ``code`` body field still wins over ``error`` when present.
        with pytest.raises(ApiError) as ei:
            raise_for_status(
                _resp(403, json_body={"code": "FORBIDDEN", "error": "FORBIDDEN", "message": "no"})
            )
        assert ei.value.code == "FORBIDDEN"

    def test_empty_header_falls_back_to_body(self):
        # An empty/whitespace header value must not clobber the body fallback.
        with pytest.raises(ApiError) as ei:
            raise_for_status(
                _resp(
                    404,
                    json_body={"error": "MARKET_NOT_FOUND", "message": "Market not found"},
                    headers={"X-Polysim-Code": "   "},
                )
            )
        assert ei.value.code == "MARKET_NOT_FOUND"

    def test_validation_error_also_prefers_header(self):
        # The 400/422 ValidationError branch shares the same code-resolution.
        with pytest.raises(ValidationError) as ei:
            raise_for_status(
                _resp(
                    422,
                    json_body={"error": "price 1.5 is out of range [0, 1]"},
                    headers={"X-Polysim-Code": "INVALID_PRICE"},
                )
            )
        assert ei.value.code == "INVALID_PRICE"
