"""agent.py — the conversational loop (SPEC §6).

What this module does:
  - Builds the system prompt as two cacheable blocks (static + dynamic state).
  - Runs the per-turn dispatch loop: API call -> tool_use blocks -> dispatch
    -> tool_result blocks -> repeat until text-only response.
  - Tracks token usage and per-turn cost.
  - Stays pure of any I/O concerns — display goes through a Renderer
    protocol so the same loop powers the CLI, tests, and a future React UI.

What this module does NOT do:
  - Render anything (see cli.py for RichRenderer).
  - Persist anything (see transcript.py).
  - Provide the REPL entry point (see __main__.py).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.claude_helpers import (  # noqa: E402
    AGENT_MODEL,
    cacheable_text_block,
    call_with_retry,
    get_client,
)
from agent.tool_registry import ANTHROPIC_TOOLS, dispatch  # noqa: E402
from agent.tools.classification import list_categories  # noqa: E402
from db.database import get_data_source, open_db  # noqa: E402


# ---------------------------------------------------------------------------
# Pricing (Sonnet 4.6 — pinned 2026-05; source: https://www.anthropic.com/pricing)
# ---------------------------------------------------------------------------

PRICE_INPUT = 3.0 / 1_000_000
PRICE_OUTPUT = 15.0 / 1_000_000
PRICE_CACHE_READ = 0.30 / 1_000_000
PRICE_CACHE_CREATE = 3.75 / 1_000_000

# Safety cap on tool-dispatch iterations per turn.
MAX_TOOL_ITERATIONS = 10

# Cap each tool_result string at this many chars so a monster query doesn't
# blow the next turn's context budget. Large results still go in the
# transcript; only the model's view is trimmed.
MAX_TOOL_RESULT_CHARS = 8_000


# ---------------------------------------------------------------------------
# Renderer protocol — implemented by cli.RichRenderer and SilentRenderer
# ---------------------------------------------------------------------------

class Renderer(Protocol):
    def show_tool_call(self, name: str, input: dict) -> None: ...
    def show_tool_result(self, name: str, result: Any, is_error: bool = False) -> None: ...
    def show_assistant_text(self, text: str) -> None: ...
    def show_user_text(self, text: str) -> None: ...
    def show_usage(self, *, input_tokens: int, output_tokens: int,
                   cache_read: int, cache_creation: int,
                   cost_usd: float, turn: int) -> None: ...
    def show_error(self, where: str, detail: str) -> None: ...
    def prompt(self, label: str) -> str: ...


class SilentRenderer:
    """No-op renderer for tests and headless use."""
    def show_tool_call(self, name, input): pass
    def show_tool_result(self, name, result, is_error=False): pass
    def show_assistant_text(self, text): pass
    def show_user_text(self, text): pass
    def show_usage(self, **kwargs): pass
    def show_error(self, where, detail): pass
    def prompt(self, label): return ""


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class Session:
    data_source: str
    messages: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_usd: float = 0.0
    turn_count: int = 0


# ---------------------------------------------------------------------------
# System prompt — two cacheable blocks (static + dynamic)
# ---------------------------------------------------------------------------

_STATIC_PROMPT = """You are a personal finance assistant for a UK user. You help with two things:

1. **Classification housekeeping** — process the backlog of `Missing` transactions and grow the user's classification rules with their approval.
2. **Forward-looking financial reasoning** — answer questions about spending, income, and scenarios ("what if my rate changes", "what if I lose my job") grounded in the user's real transaction history.

You have 13 tools spanning state (agent_state), classification (get_unclassified, list_categories, suggest_classification, preview/apply rule, preview/apply taxonomy extension), and scenarios (spending summary, income summary, fixed/discretionary split, model_scenario). Call list_categories early in any classification conversation so your suggestions stay inside the existing taxonomy.

# Contracts you must follow

**Preview-before-apply.** `apply_classification_rule` mutates the transactions table and is irreversible by you. NEVER call it without first:
  (a) calling `preview_rule_application` with the same arguments,
  (b) showing the user the would-match count and a couple of sample rows, AND
  (c) receiving explicit user approval ("yes", "go ahead", "looks right"). If the user is silent or ambiguous, ask.

**Taxonomy growth.** If a Missing transaction has no good fit in the existing taxonomy, use `extend_taxonomy` (paired `preview_taxonomy_extension` / `apply_taxonomy_extension`) instead of `apply_classification_rule`. Reserve `apply_classification_rule` for rules that map to a category already in the taxonomy. If you're unsure whether a tuple is new, call `list_categories` first.

**Bulk classification.** If `get_unclassified_transactions` returns more than ~10 rows and the user wants to tackle the backlog, prefer `bulk_classify_async` over per-row `suggest_classification`: it costs 50% less but is asynchronous. After submitting, tell the user the `batch_id` and the rough ETA. The next session announces pending batches automatically. Retrieve via `check_batch_results(batch_id)`. For single rows or a small handful, stick with `suggest_classification` for the immediate response. Suggestions from a batch still go through the normal preview/apply approval flow before any rule lands.

**State store boundary.** Use `set_agent_state` ONLY for durable facts the next session would benefit from (e.g. `mortgage_rate_change_date`, `avg_monthly_groceries_6m`, `primary_income_source`). Do NOT store conversational scratch, intermediate calculations, or anything you can trivially re-derive from a tool call. Every `set_agent_state` call requires a real rationale.

**Taxonomy honesty.** The category taxonomy reflects this user's historical spending and has gaps (no `video` sub for streaming, no `rail` sub for trains, no `Travel` main). When `suggest_classification` returns an obviously-imperfect fit (e.g. NETFLIX → `Leisure/subscription/music`), say so out loud in your response — don't pretend it's a clean match. Offer to use the closest option AND note that a future taxonomy update could improve it.

# Style

Be concise and numerate. State your conclusion first, then the supporting numbers. Use £ for currency. When you've done a calculation that depends on assumptions (lookback window, what counts as fixed), surface the assumption in one short clause so the user can challenge it. Avoid hedging boilerplate and disclaimers — the user is a sophisticated adult.

# Tool-call mechanics

You may emit several tool_use blocks in one response when the calls are independent. Wait for all results before proceeding. If a tool returns an error, decide whether to retry with adjusted arguments, ask the user for clarification, or report the failure honestly."""


def format_state_snapshot(rows: list[dict]) -> str:
    """Render agent_state rows for embedding in the dynamic prompt block."""
    if not rows:
        return "(no facts persisted yet)"
    lines = []
    for r in rows:
        # value is already JSON-deserialised by get_agent_state-style reads;
        # for the snapshot, serialise compactly.
        val_str = json.dumps(r["value"], ensure_ascii=False)
        if len(val_str) > 200:
            val_str = val_str[:197] + "..."
        lines.append(f"  - {r['key']} = {val_str}  [{r['confidence']}; {r['rationale']}]")
    return "\n".join(lines)


def _read_all_agent_state() -> list[dict]:
    """Pull every row of agent_state, deserialise values for prompt rendering."""
    from agent.tools.state import _DESERIALISERS  # local import to avoid cycle
    with open_db() as conn:
        rows = conn.execute(
            "SELECT key, value, value_type, rationale, confidence, updated_at "
            "FROM agent_state ORDER BY key"
        ).fetchall()
    return [
        {
            "key": r["key"],
            "value": _DESERIALISERS[r["value_type"]](r["value"]),
            "rationale": r["rationale"],
            "confidence": r["confidence"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def _pending_batches_summary() -> str:
    """One short line describing in-progress bulk_classify_async batches.

    C2 cross-session announcement — when the agent boots in a future
    session, it should see (and be able to mention) batches that haven't
    been retrieved yet. Returns "" when nothing's pending.
    """
    with open_db() as conn:
        rows = conn.execute(
            "SELECT batch_id, memos_count, submitted_at "
            "FROM pending_batches WHERE status = 'in_progress' "
            "ORDER BY submitted_at",
        ).fetchall()
    if not rows:
        return ""
    parts = [
        f"{r['batch_id']} ({r['memos_count']} memos, submitted {r['submitted_at']})"
        for r in rows
    ]
    return (
        f"Pending classification batches ({len(rows)}): " + "; ".join(parts) +
        ". Call check_batch_results(batch_id) to retrieve suggestions."
    )


def build_system_prompt(
    data_source: str,
    now: date,
    state_snapshot: list[dict] | None = None,
    taxonomy: dict | None = None,
) -> list[dict]:
    """Return a two-block list: static instructions + dynamic context.

    Both blocks are marked cacheable. The dynamic block changes only when
    agent_state or the date does, so cache hits should be the rule, not
    the exception.
    """
    if state_snapshot is None:
        state_snapshot = _read_all_agent_state()
    if taxonomy is None:
        taxonomy = list_categories(source=data_source)

    pending = _pending_batches_summary()

    dynamic = (
        f"Today's date: {now.isoformat()}\n"
        f"Data source: {data_source}\n"
        f"\n"
        f"Current category taxonomy (main -> sub -> [sub2 values]):\n"
        f"{json.dumps(taxonomy, indent=2, ensure_ascii=False)}\n"
        f"\n"
        f"Persisted facts from prior sessions:\n"
        f"{format_state_snapshot(state_snapshot)}"
    )
    if pending:
        dynamic += f"\n\n{pending}"
    return [cacheable_text_block(_STATIC_PROMPT), cacheable_text_block(dynamic)]


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------

def compute_cost(usage) -> float:
    """USD cost for one API response, using Sonnet 4.6 rates."""
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * PRICE_INPUT
        + usage.output_tokens * PRICE_OUTPUT
        + cache_read * PRICE_CACHE_READ
        + cache_create * PRICE_CACHE_CREATE
    )


def _truncate(s: str) -> str:
    if len(s) <= MAX_TOOL_RESULT_CHARS:
        return s
    return s[: MAX_TOOL_RESULT_CHARS - 60] + f"\n... [truncated, {len(s) - MAX_TOOL_RESULT_CHARS + 60} more chars]"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run_turn(
    session: Session,
    user_text: str,
    renderer: Renderer,
    transcript=None,
    *,
    dispatch_fn=dispatch,
    now: date | None = None,
) -> str:
    """Run one full conversational turn.

    Returns the final assistant text response (last text block of the
    last API response). Mutates `session` in place. `dispatch_fn` is
    injectable so tests can substitute a mock.
    """
    if now is None:
        now = date.today()

    session.turn_count += 1
    session.messages.append({"role": "user", "content": user_text})
    if transcript is not None:
        transcript.record("user", content=user_text)

    system_blocks = build_system_prompt(session.data_source, now)
    client = get_client()
    final_text = ""

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = call_with_retry(
            client.messages.create,
            model=AGENT_MODEL,
            max_tokens=4096,
            system=system_blocks,
            tools=ANTHROPIC_TOOLS,
            messages=session.messages,
        )

        # Accumulate usage
        usage = response.usage
        session.tokens_in += usage.input_tokens
        session.tokens_out += usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        session.cache_read += cache_read
        session.cache_creation += cache_create
        turn_cost = compute_cost(usage)
        session.cost_usd += turn_cost

        # Append the assistant message (with tool_use blocks intact)
        # to messages, so the next iteration sees it.
        assistant_content = [_block_to_dict(b) for b in response.content]
        session.messages.append({"role": "assistant", "content": assistant_content})
        if transcript is not None:
            transcript.record("assistant", content=assistant_content)

        # Render any text blocks the model emitted this iteration.
        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text:
                renderer.show_assistant_text(block.text)
                final_text = block.text

        # If there are no tool_use blocks, we're done.
        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            renderer.show_usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read=cache_read,
                cache_creation=cache_create,
                cost_usd=turn_cost,
                turn=session.turn_count,
            )
            if transcript is not None:
                transcript.record(
                    "usage", turn=session.turn_count,
                    tokens={"in": usage.input_tokens, "out": usage.output_tokens,
                            "cache_read": cache_read, "cache_creation": cache_create},
                    cost_usd=turn_cost,
                )
            break

        # Otherwise dispatch each tool and inject the results.
        tool_results = []
        for tu in tool_uses:
            renderer.show_tool_call(tu.name, dict(tu.input))
            try:
                result = dispatch_fn(tu.name, dict(tu.input), messages=session.messages)
                is_error = False
                content_str = _truncate(json.dumps(result, default=str, ensure_ascii=False))
            except Exception as e:
                result = {"error": type(e).__name__, "message": str(e)}
                is_error = True
                content_str = _truncate(json.dumps(result, default=str, ensure_ascii=False))
                renderer.show_error(f"tool:{tu.name}", str(e))

            renderer.show_tool_result(tu.name, result, is_error=is_error)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": content_str,
                "is_error": is_error,
            })
            if transcript is not None:
                transcript.record(
                    "tool_result", tool_use_id=tu.id, name=tu.name,
                    is_error=is_error, output=result,
                )

        # All tool_results go into a single user message — the API
        # requires this shape (one user message per round of tool_uses).
        session.messages.append({"role": "user", "content": tool_results})
    else:
        renderer.show_error(
            "loop", f"Hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} without end_turn. Stopping."
        )
        if transcript is not None:
            transcript.record(
                "error", where="loop",
                detail=f"hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS}",
            )

    return final_text


def _block_to_dict(block) -> dict:
    """Convert an Anthropic content block (SDK object) to a plain dict
    suitable for re-sending in the messages array."""
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    # Future block types (thinking blocks, etc.) — keep raw best-effort.
    return {"type": t, **{k: getattr(block, k) for k in dir(block) if not k.startswith("_") and k not in ("type", "model_dump", "model_dump_json")}}
