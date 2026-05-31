"""limits.py — cost cap + per-IP rate limit for the public web demo.

Two guardrails, both running BEFORE any Anthropic API call so an
over-budget or rate-limited request never spends:

- Per-session hard cap. Cumulative cost (Session.cost_usd, accumulated by
  the agent loop) plus a conservative estimate for the next turn must fit
  under BUDGET_USD. Estimate is generous so we hit the cap BEFORE the
  expensive turn starts; worst-case overshoot from one in-flight turn is
  ~ESTIMATED_NEXT_TURN_USD ≈ $0.10.

- Per-IP daily rate limit. Stops a single IP creating unlimited sessions.
  Trust X-Forwarded-For only when the reverse proxy is known to set it
  honestly (Cloudflare Tunnel does).
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

BUDGET_USD = 0.50                  # per session, hard cap
ESTIMATED_NEXT_TURN_USD = 0.06     # generous Sonnet 4.6 turn estimate
MAX_SESSIONS_PER_IP_PER_DAY = 3
DAY_SECONDS = 24 * 60 * 60


class BudgetExceededError(Exception):
    """Raised when a session has spent (or is about to spend) past BUDGET_USD."""
    def __init__(self, used: float, budget: float):
        self.used = used
        self.budget = budget
        super().__init__(f"Budget exceeded: ${used:.4f} / ${budget:.2f}")


class RateLimitedError(Exception):
    """Raised when an IP has created too many sessions in the past 24h."""
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limited; retry after {retry_after_seconds}s")


def check_budget(cost_usd: float, *, budget: float = BUDGET_USD,
                 estimate: float = ESTIMATED_NEXT_TURN_USD) -> None:
    """Raise BudgetExceededError if running another turn would breach budget."""
    if cost_usd + estimate > budget:
        raise BudgetExceededError(used=cost_usd, budget=budget)


def budget_remaining(cost_usd: float, *, budget: float = BUDGET_USD) -> float:
    return max(0.0, budget - cost_usd)


class RateLimiter:
    """Per-IP session-creation counter with a 24h rolling window."""

    def __init__(self, max_per_day: int = MAX_SESSIONS_PER_IP_PER_DAY,
                 window_seconds: int = DAY_SECONDS):
        self._timestamps: dict[str, deque[datetime]] = defaultdict(deque)
        self._max = max_per_day
        self._window = timedelta(seconds=window_seconds)

    def _prune(self, ip: str, now: datetime) -> None:
        cutoff = now - self._window
        bucket = self._timestamps[ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def check(self, ip: str) -> None:
        """Raise RateLimitedError if `ip` is over its daily budget."""
        now = datetime.now(timezone.utc)
        self._prune(ip, now)
        bucket = self._timestamps[ip]
        if len(bucket) >= self._max:
            # Retry-After = time until the oldest entry falls out of the window.
            retry_after = int((bucket[0] + self._window - now).total_seconds())
            raise RateLimitedError(retry_after_seconds=max(retry_after, 1))

    def record(self, ip: str) -> None:
        """Mark a successful session creation for `ip`."""
        self._timestamps[ip].append(datetime.now(timezone.utc))

    def used(self, ip: str) -> int:
        """How many sessions `ip` has created in the current window."""
        now = datetime.now(timezone.utc)
        self._prune(ip, now)
        return len(self._timestamps[ip])
