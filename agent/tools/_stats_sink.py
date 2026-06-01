"""_stats_sink.py — optional bridge from tools to web-side stats counters.

The agent tools (e.g. bulk_classify_async) run inside the agent loop, which
is hosted by either the CLI (no stats) or the FastAPI web app (live Stats
instance on app.state.stats). Tools need a way to record counter events
without taking a hard dependency on the web layer.

This module holds a single module-global `_sink`. The web app's lifespan
calls `register(stats)` to wire it up; the CLI leaves it unset.
`record_batch_submitted` / `record_batch_completed` no-op when unset.

Decoupling shape: anything with the right two methods satisfies the
Protocol, so this module never imports from `web.backend`.
"""
from __future__ import annotations

from typing import Protocol


class _SinkProtocol(Protocol):
    def record_batch_submitted(self) -> None: ...
    def record_batch_completed(self, cost_usd_delta: float) -> None: ...


_sink: _SinkProtocol | None = None


def register(sink: _SinkProtocol) -> None:
    """Wire up a sink that satisfies the protocol. Idempotent."""
    global _sink
    _sink = sink


def reset() -> None:
    """Drop the sink — used by tests that want a clean slate."""
    global _sink
    _sink = None


def record_batch_submitted() -> None:
    if _sink is not None:
        _sink.record_batch_submitted()


def record_batch_completed(cost_usd_delta: float) -> None:
    if _sink is not None:
        _sink.record_batch_completed(cost_usd_delta)
