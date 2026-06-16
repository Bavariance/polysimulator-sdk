"""Exception hierarchy.

All SDK errors derive from ``PolySimError`` so callers can catch the
whole tree with a single ``except PolySimError`` and still get
fine-grained classes (rate-limit vs. invalid request vs. server error)
when they want them.
"""

from __future__ import annotations

from typing import Any


class PolySimError(Exception):
    """Base class for every error this SDK raises."""


class ApiError(PolySimError):
    """The server returned a non-2xx response.

    ``status_code`` is the HTTP status; ``code`` is the
    ``error.code`` field from the JSON body (e.g. ``"INVALID_KEY"``,
    ``"INSUFFICIENT_BALANCE"``); ``payload`` is the full body.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}
        self.request_id = request_id
        suffix = f" [code={code}]" if code else ""
        rid = f" [request_id={request_id}]" if request_id else ""
        super().__init__(f"HTTP {status_code}: {message}{suffix}{rid}")


class RateLimitError(ApiError):
    """HTTP 429 — caller exceeded the per-key or per-IP rate limit.

    ``retry_after`` is the number of seconds the server suggested
    waiting (from the ``Retry-After`` header). The auto-pacing in
    ``PolySimClient`` consumes this and sleeps before retrying;
    callers that catch this directly can read it themselves.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: float = 1.0,
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(
            status_code=429,
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            payload=payload,
            request_id=request_id,
        )


class ValidationError(ApiError):
    """HTTP 400/422 — the request was malformed or violated business rules."""


class EdgeBlockedError(ApiError):
    """The CDN edge (Cloudflare WAF / Bot-Fight Mode) blocked the request
    before it ever reached the API.

    The give-away is a non-JSON body carrying Cloudflare's plain-text
    ``error code: 1XXX`` marker (e.g. ``error code: 1010``) rather than our
    ``{error, code, message}`` shape. The usual cause is a disallowed
    ``User-Agent``: Python's stdlib ``urllib`` default ``Python-urllib/x.y``
    is blocked at the edge. This SDK sends a branded UA that passes, so you'll
    normally only see this if you override the UA, sit behind a proxy that
    rewrites it, or the edge tightens its rules. Fix: use this SDK,
    ``requests``, or ``httpx`` — or set a custom ``User-Agent`` header.

    ``code`` is ``"EDGE_BLOCKED"`` and ``status_code`` is whatever the edge
    returned (typically ``403``).
    """
