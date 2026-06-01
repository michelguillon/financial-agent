"""stats.py — in-memory counters for GET /admin/stats.

Single mutable object pinned to app.state. Single-process, single-worker
(see web/backend/main.py: workers=1), so no locking is needed — the
FastAPI handler that mutates is the same handler that reads.

Restart wipes everything; matches the rest of the web demo's
state-is-ephemeral posture. Persistent stats are out of scope until
someone actually needs them.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol


class _SessionsLike(Protocol):
    def active_count(self) -> int: ...


class _RateLimiterLike(Protocol):
    def snapshot(self) -> dict: ...


@dataclass
class Stats:
    """Lifetime counters for the running web process."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sessions_created_total: int = 0
    turns_completed_total: int = 0
    turns_budget_blocked_total: int = 0
    turns_failed_total: int = 0
    spend_usd_total: float = 0.0
    replay_streams_started_total: int = 0
    replay_streams_by_id: Counter = field(default_factory=Counter)
    rate_limit_rejections_total: int = 0
    batches_submitted_total: int = 0
    batches_completed_total: int = 0
    batch_spend_usd_total: float = 0.0
    last_turn_at: datetime | None = None
    last_replay_at: datetime | None = None
    last_batch_at: datetime | None = None

    # ----- increment hooks -------------------------------------------------

    def record_session_created(self) -> None:
        self.sessions_created_total += 1

    def record_turn(
        self,
        *,
        kind: Literal["completed", "failed", "budget_blocked"],
        cost_delta_usd: float = 0.0,
    ) -> None:
        # Always credit the spend, even on partial/failed turns — the API
        # may have charged us for the input tokens before raising.
        if cost_delta_usd > 0:
            self.spend_usd_total += cost_delta_usd
        if kind == "completed":
            self.turns_completed_total += 1
            self.last_turn_at = datetime.now(timezone.utc)
        elif kind == "failed":
            self.turns_failed_total += 1
            self.last_turn_at = datetime.now(timezone.utc)
        elif kind == "budget_blocked":
            self.turns_budget_blocked_total += 1

    def record_replay_started(self, replay_id: str) -> None:
        self.replay_streams_started_total += 1
        self.replay_streams_by_id[replay_id] += 1
        self.last_replay_at = datetime.now(timezone.utc)

    def record_rate_limit_rejection(self) -> None:
        self.rate_limit_rejections_total += 1

    # ----- C2 batch hooks (called via agent.tools._stats_sink) -------------

    def record_batch_submitted(self) -> None:
        self.batches_submitted_total += 1
        self.last_batch_at = datetime.now(timezone.utc)

    def record_batch_completed(self, cost_usd_delta: float) -> None:
        self.batches_completed_total += 1
        if cost_usd_delta > 0:
            self.batch_spend_usd_total += cost_usd_delta
        self.last_batch_at = datetime.now(timezone.utc)

    # ----- serialise -------------------------------------------------------

    def to_dict(
        self,
        *,
        sessions: _SessionsLike,
        rate_limiter: _RateLimiterLike,
    ) -> dict:
        """Produce the GET /admin/stats payload using live read-throughs
        for fields that are authoritative elsewhere (active session count,
        per-IP usage)."""
        now = datetime.now(timezone.utc)
        rl = rate_limiter.snapshot()
        return {
            "process": {
                "started_at": _iso(self.started_at),
                "uptime_seconds": int((now - self.started_at).total_seconds()),
            },
            "sessions": {
                "active": sessions.active_count(),
                "created_total": self.sessions_created_total,
                "rate_limit_rejections_total": self.rate_limit_rejections_total,
                "unique_ips_seen_today": rl["unique_ips_today"],
                "events_today": rl["events_today"],
            },
            "turns": {
                "completed_total": self.turns_completed_total,
                "budget_blocked_total": self.turns_budget_blocked_total,
                "failed_total": self.turns_failed_total,
                "spend_usd_total": round(self.spend_usd_total, 6),
                "last_turn_at": _iso(self.last_turn_at),
            },
            "replays": {
                "streams_started_total": self.replay_streams_started_total,
                "by_id": dict(self.replay_streams_by_id),
                "last_replay_at": _iso(self.last_replay_at),
            },
            "batches": {
                "submitted_total": self.batches_submitted_total,
                "completed_total": self.batches_completed_total,
                "spend_usd_total": round(self.batch_spend_usd_total, 6),
                "last_batch_at": _iso(self.last_batch_at),
            },
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None
