"""streaming.py — WebSseRenderer + SSE adapter for the FastAPI turn route.

The agent loop's Renderer protocol is event-based at the block level
(show_tool_call, show_tool_result, etc.), which is exactly the
granularity a chat UI wants to stream. WebSseRenderer turns each
callback into a dict pushed onto an asyncio.Queue. The async generator
on the FastAPI side drains the queue and yields SSE-formatted strings.

Thread safety: run_turn is synchronous and runs in asyncio.to_thread.
asyncio.Queue is NOT thread-safe, so the renderer's callbacks use
loop.call_soon_threadsafe to schedule put_nowait on the event loop. This
is the stdlib-clean way to bridge a sync producer to an async consumer.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Sentinel value pushed onto the queue when run_turn finishes (or errors).
# Tells the async generator to stop draining.
_SENTINEL = object()


class WebSseRenderer:
    """Renderer that pushes each callback into an asyncio.Queue from a thread."""

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = loop

    # ---- callbacks (called from the worker thread) ----------------------

    def _put(self, event_type: str, **data: Any) -> None:
        payload = {"type": event_type, "data": data}
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    def show_tool_call(self, name: str, input: dict) -> None:
        self._put("tool_call", name=name, input=input)

    def show_tool_result(self, name: str, result: Any, is_error: bool = False) -> None:
        # Avoid pushing huge results across the queue — the agent loop already
        # truncates tool_result content for the model; mirror that here for UI.
        serialised = _safe_json(result)
        if len(serialised) > 12_000:
            serialised = serialised[:12_000] + f"\n... [truncated, +{len(serialised) - 12_000} chars]"
        self._put("tool_result", name=name, result=serialised, is_error=is_error)

    def show_assistant_text(self, text: str) -> None:
        self._put("assistant_text", text=text)

    def show_user_text(self, text: str) -> None:
        # Unused by the live web turn flow (the browser already shows what the
        # user typed), but kept here for protocol parity so a future web-replay
        # endpoint can subscribe without touching this renderer again.
        self._put("user_text", text=text)

    def show_usage(self, *, input_tokens: int, output_tokens: int,
                   cache_read: int, cache_creation: int,
                   cost_usd: float, turn: int) -> None:
        self._put(
            "usage",
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read=cache_read, cache_creation=cache_creation,
            cost_usd=cost_usd, turn=turn,
        )

    def show_error(self, where: str, detail: str) -> None:
        self._put("error", where=where, detail=detail)

    def prompt(self, label: str) -> str:
        # The web flow never asks the renderer for input — the user's text
        # comes in via the HTTP request body. Return empty for protocol
        # compliance.
        return ""


# ---------------------------------------------------------------------------
# SSE formatting + generator
# ---------------------------------------------------------------------------

def format_sse(event_type: str, data: dict) -> str:
    """SSE wire format: `event: <type>\\ndata: <json>\\n\\n`."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


async def stream_turn(
    queue: asyncio.Queue,
    run_turn_future: asyncio.Future,
    *,
    error_where: str = "run_turn",
):
    """Async generator: drains `queue` until SENTINEL, yields SSE strings.

    Also catches exceptions from `run_turn_future` and emits an `error`
    event so the browser sees a reason if the producer died mid-flight.
    `error_where` lets the replay path label its errors honestly.
    """
    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        if isinstance(item, dict):
            yield format_sse(item["type"], item["data"])

    # If the producer raised, surface it as a final error event.
    if run_turn_future.done():
        exc = run_turn_future.exception()
        if exc is not None:
            yield format_sse("error", {"where": error_where, "detail": str(exc)})


def push_sentinel(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
    """Signal the stream_turn generator to stop. Call from the worker thread."""
    loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"_serialisation_error": str(e)})
