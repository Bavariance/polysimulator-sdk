"""Transport wiring: pump a frame source through a pure adapter into a handle.

``run_stream`` opens an async-generator frame source (one of the
``polysim_sdk`` transports), starts a background task that pulls dict frames,
runs the PURE adapter to produce py-sdk events (filtered by the spec), and
``_push``es them into an :class:`AsyncSubscriptionHandle`. Closing the handle
cancels the task and closes the underlying generator — no leaked task or stream.

The adapter is injected, so this runner is transport- and topic-agnostic: the
three CORE topics differ only in (which transport generator, which adapter,
which spec).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from polysim_polymarket.streams._handle import AsyncSubscriptionHandle

_E = TypeVar("_E")
_S = TypeVar("_S")

DEFAULT_QUEUE_SIZE = 1024


async def _aclose_source(source: AsyncIterator[Any]) -> None:
    """Close the frame source if it exposes ``aclose`` (async generators do).

    The transports return async generators at runtime (which have ``aclose``);
    the static type is the wider ``AsyncIterator``, so probe for the method.
    """
    aclose = getattr(source, "aclose", None)
    if aclose is not None:
        await aclose()


def run_stream(
    *,
    source: AsyncIterator[dict[str, Any]],
    adapt: Callable[[dict[str, Any], _S], list[_E]],
    spec: _S,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> AsyncSubscriptionHandle[_E]:
    """Wire ``source`` -> ``adapt(frame, spec)`` -> handle and return the handle.

    A background task pumps frames from ``source`` through ``adapt`` and pushes
    each produced event into the handle. The handle's ``close()`` cancels the
    pump and closes ``source``.
    """
    handle: AsyncSubscriptionHandle[_E] = AsyncSubscriptionHandle(queue_size=queue_size)

    async def pump() -> None:
        try:
            async for frame in source:
                for event in adapt(frame, spec):
                    handle._push(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — surface at end-of-stream
            handle._end(exc)
            return
        handle._end()

    task = asyncio.create_task(pump())

    async def on_close(_h: AsyncSubscriptionHandle[_E]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        with contextlib.suppress(Exception):
            await _aclose_source(source)

    handle._bind_close(on_close)
    return handle


__all__ = ["DEFAULT_QUEUE_SIZE", "run_stream"]
