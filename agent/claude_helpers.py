"""Anthropic API helpers — mirrors the mistral_helpers.py pattern from the
sibling rag-pipeline project.

Centralises:
  - Model identifiers (SPEC §3.3 model routing)
  - Lazy Anthropic client
  - call_with_retry — exponential backoff on 429/5xx with Retry-After honoured
  - Message-builder helper that supports cache_control for prompt caching
    (used by the Step 5 agent loop; Step 4 tools don't need caching)

Reads ANTHROPIC_API_KEY from the environment via python-dotenv. Fails loudly
if the key is missing — agent code should never half-work because of a
silently absent credential.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Model routing — SPEC §3.3
# ---------------------------------------------------------------------------

AGENT_MODEL = "claude-sonnet-4-6"
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"

# Threshold above which the agent loop (Step 5) should batch Missing
# transactions through the Batch API rather than calling sequentially.
# Documented hint, not code-enforced: the agent decides per turn based on
# the system-prompt nudge in agent.agent._STATIC_PROMPT (C2).
BATCH_THRESHOLD = 10

# Haiku 4.5 pricing per token (Sonnet's prices live in agent/agent.py;
# Haiku is broken out here because bulk_classify_async / check_batch_results
# need to compute realised cost without importing agent.agent).
HAIKU_PRICE_INPUT = 1.0 / 1_000_000
HAIKU_PRICE_OUTPUT = 5.0 / 1_000_000

# Anthropic Batch API discount applied to both input and output rates.
BATCH_DISCOUNT = 0.5


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client = None


def get_client():
    """Return a lazily-instantiated Anthropic client.

    Raises RuntimeError with a clear message if ANTHROPIC_API_KEY is missing,
    so failures point at the misconfiguration instead of an SDK stack trace.
    """
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env and "
                "fill in your key (https://console.anthropic.com)."
            )
        # Lazy import so modules that don't actually call the API don't need
        # the anthropic package installed.
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Retry wrapper — same shape as rag-pipeline/mistral_helpers.call_with_retry
# ---------------------------------------------------------------------------

def call_with_retry(
    func: Callable[..., Any],
    *args,
    max_retries: int = 5,
    base_delay: float = 1.0,
    **kwargs,
) -> Any:
    """Call `func(*args, **kwargs)` with retry on transient Anthropic errors.

    Retryable: 429 (RateLimitError), 5xx (APIStatusError with status>=500),
    APIConnectionError.
    Non-retryable: 400, 401, 404 — raised immediately.

    Backoff: exponential (base_delay * 2^(attempt-1)), but honours the
    Retry-After header on 429 when present.
    """
    # Lazy import — if you don't call this, you don't need anthropic installed.
    from anthropic import (
        APIConnectionError,
        APIStatusError,
        RateLimitError,
    )

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)

        except RateLimitError as e:
            last_exc = e
            delay = _retry_after_seconds(e) or base_delay * (2 ** (attempt - 1))
            logging.warning(
                "claude_helpers: rate limit (attempt %d/%d), sleeping %.1fs",
                attempt, max_retries, delay,
            )

        except APIStatusError as e:
            if not (500 <= e.status_code < 600):
                raise  # 400/401/404/etc — caller's problem, not transient
            last_exc = e
            delay = base_delay * (2 ** (attempt - 1))
            logging.warning(
                "claude_helpers: server %d (attempt %d/%d), sleeping %.1fs",
                e.status_code, attempt, max_retries, delay,
            )

        except APIConnectionError as e:
            last_exc = e
            delay = base_delay * (2 ** (attempt - 1))
            logging.warning(
                "claude_helpers: connection error (attempt %d/%d), sleeping %.1fs",
                attempt, max_retries, delay,
            )

        if attempt < max_retries:
            time.sleep(delay)

    assert last_exc is not None  # for the type checker
    raise last_exc


def _retry_after_seconds(exc) -> float | None:
    """Extract Retry-After header value from a RateLimitError, if present."""
    try:
        headers = getattr(exc.response, "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw else None
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Caching helper — used by the Step 5 agent loop
# ---------------------------------------------------------------------------

def cacheable_text_block(text: str) -> dict:
    """Build a content block with ephemeral cache_control set.

    Used for the system prompt and agent_state snapshot — both are stable
    across turns of a single session, so caching them cuts input cost by ~90%
    on turns 2+. SPEC §3.3 cost section.
    """
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }
