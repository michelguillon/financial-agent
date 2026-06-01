"""classification.py — agent tools for processing the Missing backlog (SPEC §5.1).

Tools:
  - get_unclassified_transactions(limit)
  - list_categories()
  - suggest_classification(memo, amount, account_name)   [calls Claude Haiku, sync]
  - preview_rule_application(pattern, ...)               [no mutation]
  - apply_classification_rule(pattern, ...)              [mutates Missing rows; B1 gated]
  - preview_taxonomy_extension(...)                       [no mutation]
  - apply_taxonomy_extension(...)                         [mutates; B1 gated]
  - bulk_classify_async(memos)                           [C2: Anthropic Batch API submit]
  - check_batch_results(batch_id)                        [C2: poll/retrieve + persist]

Two-step rule flow (per user decision, Step 4 plan):
  preview_rule_application -> show user how many rows would match
  apply_classification_rule -> on explicit user approval, write the rule
                               and update matching Missing rows in one
                               transaction.

The two-step flow exists so the agent can present a preview to the user
before a destructive write. SPEC §5.1's text still describes the older
single-step add_classification_rule; this module is the authoritative
implementation for Phase 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow `python -m agent.tools.classification` to find sibling packages.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.database import get_data_source, open_db, register_regexp  # noqa: E402


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------

def get_unclassified_transactions(
    limit: int = 20, source: str | None = None
) -> list[dict]:
    """Return the most recent rows where category_main = 'Missing'.

    Result rows include id, date, amount, type, memo, account_name —
    the fields the agent needs to suggest a classification.
    """
    src = source or get_data_source()
    with open_db() as conn:
        rows = conn.execute(
            """
            SELECT id, date, amount, type, memo, account_name
            FROM transactions
            WHERE category_main = 'Missing' AND data_source = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (src, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_categories(source: str | None = None) -> dict:
    """Return the full taxonomy in use by the data.

    Shape: {main: {sub: [sub2 values, possibly including null]}}.
    Lets the LLM choose categories that already exist instead of inventing
    new ones — keeps the taxonomy from drifting.
    """
    src = source or get_data_source()
    with open_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT category_main, category_sub, category_sub2
            FROM transactions
            WHERE data_source = ?
              AND category_main IS NOT NULL
            ORDER BY category_main, category_sub, category_sub2
            """,
            (src,),
        ).fetchall()

    taxonomy: dict[str, dict[str, list]] = {}
    for r in rows:
        main = r["category_main"]
        sub = r["category_sub"]
        sub2 = r["category_sub2"]
        taxonomy.setdefault(main, {})
        if sub is not None:
            taxonomy[main].setdefault(sub, [])
            if sub2 not in taxonomy[main][sub]:
                taxonomy[main][sub].append(sub2)
    return taxonomy


# ---------------------------------------------------------------------------
# Rule preview + apply (two-step)
# ---------------------------------------------------------------------------

def preview_rule_application(
    pattern: str,
    category_main: str,
    category_sub: str | None = None,
    category_sub2: str | None = None,
    details: str | None = None,
    source: str | None = None,
    sample_limit: int = 5,
) -> dict:
    """How many Missing rows would this rule reclassify? Show a sample.

    No DB writes. Returns:
        {
          would_match: N,
          sample_matches: [{id, date, amount, memo, account_name}, ...],
          proposed_classification: {category_main, category_sub, category_sub2, details}
        }
    """
    src = source or get_data_source()
    with open_db() as conn:
        register_regexp(conn)
        (n,) = conn.execute(
            """
            SELECT COUNT(*) FROM transactions
            WHERE category_main = 'Missing' AND data_source = ? AND memo REGEXP ?
            """,
            (src, pattern),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT id, date, amount, memo, account_name
            FROM transactions
            WHERE category_main = 'Missing' AND data_source = ? AND memo REGEXP ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (src, pattern, sample_limit),
        ).fetchall()

    return {
        "would_match": n,
        "sample_matches": [dict(r) for r in rows],
        "proposed_classification": {
            "category_main": category_main,
            "category_sub": category_sub,
            "category_sub2": category_sub2,
            "details": details,
        },
    }


def apply_classification_rule(
    pattern: str,
    category_main: str,
    category_sub: str | None = None,
    category_sub2: str | None = None,
    details: str | None = None,
    source: str | None = None,
) -> dict:
    """Write the rule and reclassify all matching Missing rows in one transaction.

    Wraps INSERT(rule) + UPDATE(transactions) + UPDATE(rule.times_matched) in
    a single SQL transaction so partial failure rolls back cleanly.

    Returns {rules_added: 1, rule_id: N, transactions_reclassified: M}.

    Human approval is enforced at the agent-loop level (SPEC §6), not here:
    the agent must present preview_rule_application's output to the user
    and receive explicit approval before calling this function.
    """
    src = source or get_data_source()
    with open_db() as conn:
        register_regexp(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO classification_rules
                    (pattern, category_main, category_sub, category_sub2,
                     details, added_by, approved_by, approved_at)
                VALUES (?, ?, ?, ?, ?, 'agent', 'human', CURRENT_TIMESTAMP)
                """,
                (pattern, category_main, category_sub, category_sub2, details),
            )
            rule_id = cur.lastrowid

            cur = conn.execute(
                """
                UPDATE transactions
                SET category_main = ?, category_sub = ?,
                    category_sub2 = ?, details = ?
                WHERE category_main = 'Missing'
                  AND data_source = ?
                  AND memo REGEXP ?
                """,
                (category_main, category_sub, category_sub2, details, src, pattern),
            )
            n_reclassified = cur.rowcount

            conn.execute(
                "UPDATE classification_rules SET times_matched = ? WHERE id = ?",
                (n_reclassified, rule_id),
            )
        except Exception:
            conn.rollback()
            raise

    return {
        "rules_added": 1,
        "rule_id": rule_id,
        "transactions_reclassified": n_reclassified,
    }


# ---------------------------------------------------------------------------
# Taxonomy extension (A3) — preview + apply for NEW (main, sub, sub2) tuples
# ---------------------------------------------------------------------------

def _validate_taxonomy_extension(
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    source: str | None,
) -> None:
    """Raise ValueError if (main, sub, sub2) already exists in the taxonomy.

    Reads the live taxonomy from list_categories() (transactions table).
    A tuple is "new" if at least one component is not present in the current
    taxonomy under its parent. If the full tuple already appears, the agent
    should use apply_classification_rule instead.
    """
    taxonomy = list_categories(source=source)
    existing_subs = taxonomy.get(category_main, {})
    existing_sub2s = existing_subs.get(category_sub, [])
    if category_sub in existing_subs and category_sub2 in existing_sub2s:
        raise ValueError(
            f"Taxonomy entry ({category_main!r}, {category_sub!r}, "
            f"{category_sub2!r}) already exists. Use apply_classification_rule "
            "instead — this isn't a new taxonomy entry."
        )


def preview_taxonomy_extension(
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    pattern: str,
    details: str | None = None,
    source: str | None = None,
    sample_limit: int = 5,
) -> dict:
    """Preview adding a new taxonomy entry + the rule that populates it.

    No DB writes. Validates:
      1. The (main, sub, sub2) tuple is genuinely new (not in list_categories).
      2. The pattern matches at least one Missing row — taxonomy stays
         grounded in actual data; no phantom categories.

    Returns:
        {
          is_new: True,
          proposed_taxonomy_entry: {main, sub, sub2},
          would_match: N,
          sample_matches: [{id, date, amount, memo, account_name}, ...],
        }

    Raises ValueError on validation failure (caller decides whether to
    retry with a different pattern or use apply_classification_rule).
    """
    _validate_taxonomy_extension(category_main, category_sub, category_sub2, source)
    preview = preview_rule_application(
        pattern=pattern,
        category_main=category_main,
        category_sub=category_sub,
        category_sub2=category_sub2,
        details=details,
        source=source,
        sample_limit=sample_limit,
    )
    if preview["would_match"] == 0:
        raise ValueError(
            f"Pattern {pattern!r} matches 0 Missing rows. extend_taxonomy "
            "requires at least one match so the new category lands on actual "
            "data — try a broader pattern, or wait for matching transactions."
        )
    return {
        "is_new": True,
        "proposed_taxonomy_entry": {
            "category_main": category_main,
            "category_sub": category_sub,
            "category_sub2": category_sub2,
        },
        "would_match": preview["would_match"],
        "sample_matches": preview["sample_matches"],
    }


def apply_taxonomy_extension(
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    pattern: str,
    details: str | None = None,
    source: str | None = None,
) -> dict:
    """Add a new taxonomy entry by inserting the seed rule + reclassifying.

    Re-validates (don't trust preview): the tuple must still be unprecedented
    and the pattern must still match >0 Missing rows. Delegates the write
    to apply_classification_rule.

    Returns:
        {
          taxonomy_entry_added: {main, sub, sub2},
          rule_id: N,
          transactions_reclassified: M,
        }
    """
    _validate_taxonomy_extension(category_main, category_sub, category_sub2, source)

    # Re-check match count before mutating.
    src = source or get_data_source()
    with open_db() as conn:
        register_regexp(conn)
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE category_main = 'Missing' AND data_source = ? AND memo REGEXP ?",
            (src, pattern),
        ).fetchone()
    if n == 0:
        raise ValueError(
            f"Pattern {pattern!r} matches 0 Missing rows. extend_taxonomy "
            "requires at least one match."
        )

    result = apply_classification_rule(
        pattern=pattern,
        category_main=category_main,
        category_sub=category_sub,
        category_sub2=category_sub2,
        details=details,
        source=source,
    )
    return {
        "taxonomy_entry_added": {
            "category_main": category_main,
            "category_sub": category_sub,
            "category_sub2": category_sub2,
        },
        "rule_id": result["rule_id"],
        "transactions_reclassified": result["transactions_reclassified"],
    }


# ---------------------------------------------------------------------------
# suggest_classification — Haiku 4.5 call
# ---------------------------------------------------------------------------

# We use Anthropic tool-use to force the model to emit a structured object.
# Defining a single "submit_classification" tool and forcing it via
# tool_choice means the model can't return free text.
_CLASSIFY_TOOL = {
    "name": "submit_classification",
    "description": "Return the classification result for one transaction.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category_main": {
                "type": "string",
                "description": "Top-level category — must match an existing main category from the taxonomy, or 'Missing' if no good fit.",
            },
            "category_sub": {
                "type": ["string", "null"],
                "description": "Sub-category — must match an existing sub for the chosen main, or null.",
            },
            "category_sub2": {
                "type": ["string", "null"],
                "description": "Third-level — must match an existing sub2 for the chosen sub, or null.",
            },
            "details": {
                "type": ["string", "null"],
                "description": "Fourth-level detail — typically null.",
            },
            "suggested_pattern": {
                "type": "string",
                "description": (
                    "Python regex (case-insensitive) that will match this memo and "
                    "future variants. General enough to catch store-number variations, "
                    "specific enough not to false-positive."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "One short sentence: why this classification.",
            },
        },
        "required": [
            "category_main", "category_sub", "category_sub2", "details",
            "suggested_pattern", "rationale",
        ],
    },
}

_SYSTEM_PROMPT = """You are a UK personal-finance transaction classifier.

Given one bank transaction (memo, amount, account name), assign it to a
category from the existing taxonomy and suggest a regex that will match
this memo and future similar ones.

Rules:
- Use category names exactly as they appear in the taxonomy below.
- If no existing category is a good fit, set category_main to "Missing".
- The regex is matched with Python's re.match (start-anchored,
  case-insensitive) against the memo. Prefix with `.*` to match anywhere
  in the memo. Make it general enough to match future variants (different
  store numbers, dates, IDs) but specific enough to avoid false positives.
- For UK merchants, account for common bank-statement formatting (uppercase,
  trailing numbers, location suffixes).

You MUST call the submit_classification tool — do not respond with text.

Current taxonomy (main -> sub -> [sub2 values]):
{taxonomy}
"""


def suggest_classification(
    memo: str,
    amount: float,
    account_name: str,
    source: str | None = None,
) -> dict:
    """Ask Claude Haiku to classify one transaction.

    Returns the 6-field dict from SPEC §5.1:
        {category_main, category_sub, category_sub2, details,
         suggested_pattern, rationale}

    Costs ~$0.001/call interactively; the agent loop should batch via
    Batch API for backlogs of >10 transactions (SPEC §3.3).
    """
    # Imports deferred so this module is importable even without `anthropic`
    # installed (only this function needs it).
    from agent.claude_helpers import CLASSIFIER_MODEL, call_with_retry, get_client

    taxonomy = list_categories(source=source)
    system = _SYSTEM_PROMPT.format(taxonomy=json.dumps(taxonomy, indent=2))
    user = (
        f"Memo:         {memo}\n"
        f"Amount:       £{amount:.2f}\n"
        f"Account:      {account_name}\n"
    )

    client = get_client()
    response = call_with_retry(
        client.messages.create,
        model=CLASSIFIER_MODEL,
        max_tokens=512,
        tools=[_CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "submit_classification"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_classification":
            return dict(block.input)
    raise RuntimeError(
        f"Haiku didn't call submit_classification. Got: {response.content!r}"
    )


# ---------------------------------------------------------------------------
# Bulk classification (C2) — Anthropic Batch API
# ---------------------------------------------------------------------------
#
# Two tools that together replace per-row suggest_classification when the
# backlog is large enough that the async UX is worth the 50% discount:
#   bulk_classify_async(memos)   submits → returns batch_id
#   check_batch_results(batch_id) polls once; returns suggestions on done
#
# State persists in pending_batches (see db/schema.sql) so a future session
# can announce "you have N pending batches from earlier".
# ---------------------------------------------------------------------------

def _build_batch_request(memo: str, amount: float, account_name: str,
                         taxonomy_json: str, transaction_id: int) -> dict:
    """Build one entry for client.messages.batches.create(requests=[...]).

    Mirrors suggest_classification's message shape (same _CLASSIFY_TOOL,
    same forced tool_choice) so the batch results parse identically.
    """
    # Local import to keep this module importable without the Anthropic SDK.
    from agent.claude_helpers import CLASSIFIER_MODEL

    system = _SYSTEM_PROMPT.format(taxonomy=taxonomy_json)
    user = (
        f"Memo:         {memo}\n"
        f"Amount:       £{amount:.2f}\n"
        f"Account:      {account_name}\n"
    )
    return {
        "custom_id": f"tx-{transaction_id}",
        "params": {
            "model": CLASSIFIER_MODEL,
            "max_tokens": 512,
            "tools": [_CLASSIFY_TOOL],
            "tool_choice": {"type": "tool", "name": "submit_classification"},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
    }


def bulk_classify_async(
    memos: list[dict],
    source: str | None = None,
) -> dict:
    """Submit a batch of classification requests to Anthropic's Batch API.

    Args:
        memos: list of {id, memo, amount, account_name}. The id is the
               transaction id from get_unclassified_transactions; it goes
               into custom_id so results can be matched back.
        source: data_source label persisted on the pending_batches row.

    Returns:
        {batch_id, status: 'in_progress', memos_count, submitted_at, eta_hint}.

    Does not block waiting for completion — the user is expected to come
    back later (or in a future session) and call check_batch_results.
    """
    if not memos:
        raise ValueError("bulk_classify_async needs at least one memo")
    for m in memos:
        for k in ("id", "memo", "amount", "account_name"):
            if k not in m:
                raise ValueError(f"memo missing required key {k!r}: {m}")

    src = source or get_data_source()
    taxonomy = list_categories(source=src)
    taxonomy_json = json.dumps(taxonomy, indent=2)

    requests = [
        _build_batch_request(
            m["memo"], float(m["amount"]), m["account_name"], taxonomy_json, int(m["id"]),
        )
        for m in memos
    ]

    from agent.claude_helpers import call_with_retry, get_client
    from agent.tools import _stats_sink

    response = call_with_retry(get_client().messages.batches.create, requests=requests)
    batch_id = response.id

    transaction_ids = [int(m["id"]) for m in memos]
    with open_db() as conn:
        conn.execute(
            "INSERT INTO pending_batches "
            "(batch_id, status, memos_count, transaction_ids, data_source) "
            "VALUES (?, 'in_progress', ?, ?, ?)",
            (batch_id, len(memos), json.dumps(transaction_ids), src),
        )

    _stats_sink.record_batch_submitted()

    return {
        "batch_id": batch_id,
        "status": "in_progress",
        "memos_count": len(memos),
        "transaction_ids": transaction_ids,
        "eta_hint": "Typically 1-5 minutes for batches under 100 memos; up to 24h ceiling.",
    }


def _parse_batch_result(result) -> dict:
    """Turn one batch result entry into the 6-field suggestion dict.

    The Anthropic SDK exposes each batch result as an object with .result
    (an envelope with .type='succeeded'|'errored'|... and, on success, a
    .message that mirrors a normal messages.create response). We pull the
    tool_use block matching submit_classification.
    """
    custom_id = getattr(result, "custom_id", None)
    transaction_id = None
    if custom_id and custom_id.startswith("tx-"):
        try:
            transaction_id = int(custom_id[3:])
        except ValueError:
            pass

    envelope = getattr(result, "result", None)
    res_type = getattr(envelope, "type", None) if envelope is not None else None

    if res_type != "succeeded":
        # Surface the error so check_batch_results can still aggregate; the
        # caller decides what to do with partial failures.
        err = getattr(envelope, "error", None)
        msg = getattr(err, "message", None) or str(envelope)
        return {
            "transaction_id": transaction_id,
            "error": msg,
        }

    message = getattr(envelope, "message", None)
    blocks = getattr(message, "content", []) or []
    for block in blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_classification":
            suggestion = dict(block.input)
            suggestion["transaction_id"] = transaction_id
            return suggestion
    return {
        "transaction_id": transaction_id,
        "error": "no submit_classification tool_use in batch result",
    }


def _batch_cost_usd(results: list) -> float:
    """Sum input/output tokens across a batch's results and apply Haiku
    rates × BATCH_DISCOUNT. Missing usage records contribute 0."""
    from agent.claude_helpers import (
        BATCH_DISCOUNT, HAIKU_PRICE_INPUT, HAIKU_PRICE_OUTPUT,
    )
    total_in = 0
    total_out = 0
    for r in results:
        envelope = getattr(r, "result", None)
        if envelope is None or getattr(envelope, "type", None) != "succeeded":
            continue
        message = getattr(envelope, "message", None)
        usage = getattr(message, "usage", None)
        if usage is None:
            continue
        total_in += getattr(usage, "input_tokens", 0) or 0
        total_out += getattr(usage, "output_tokens", 0) or 0
    return (total_in * HAIKU_PRICE_INPUT + total_out * HAIKU_PRICE_OUTPUT) * BATCH_DISCOUNT


def check_batch_results(batch_id: str) -> dict:
    """Poll Anthropic for `batch_id` once. Return cached suggestions if
    we've already completed it; otherwise update the DB row and return
    the current status.

    Returns one of:
        {status: 'in_progress', age_seconds, memos_count}
        {status: 'completed', suggestions: [...], cost_usd, completed_at, memos_count}
        {status: 'failed', error_detail, memos_count}
    """
    with open_db() as conn:
        row = conn.execute(
            "SELECT * FROM pending_batches WHERE batch_id = ?", (batch_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown batch_id {batch_id!r}")

        if row["status"] == "completed":
            return {
                "status": "completed",
                "suggestions": json.loads(row["result_json"]) if row["result_json"] else [],
                "cost_usd": row["cost_usd"],
                "completed_at": row["completed_at"],
                "memos_count": row["memos_count"],
            }
        if row["status"] == "failed":
            return {
                "status": "failed",
                "error_detail": row["error_detail"],
                "memos_count": row["memos_count"],
            }

    from agent.claude_helpers import call_with_retry, get_client
    from agent.tools import _stats_sink

    client = get_client()
    batch = call_with_retry(client.messages.batches.retrieve, batch_id)
    processing_status = getattr(batch, "processing_status", None)

    if processing_status != "ended":
        # Still cooking. Don't mutate the row.
        return {
            "status": "in_progress",
            "processing_status": processing_status,
            "memos_count": row["memos_count"],
        }

    # Ended — could be all-success, partial, or expired.
    results = list(call_with_retry(client.messages.batches.results, batch_id))

    # If every result errored AND the batch reports an end-state suggesting
    # expiry/cancellation, persist a failed row. Otherwise treat as
    # completed (with possibly-mixed entries).
    request_counts = getattr(batch, "request_counts", None)
    expired_count = getattr(request_counts, "expired", 0) or 0 if request_counts else 0
    canceled_count = getattr(request_counts, "canceled", 0) or 0 if request_counts else 0
    succeeded_count = getattr(request_counts, "succeeded", 0) or 0 if request_counts else 0

    if succeeded_count == 0 and (expired_count or canceled_count):
        with open_db() as conn:
            conn.execute(
                "UPDATE pending_batches "
                "SET status = 'failed', completed_at = CURRENT_TIMESTAMP, "
                "    error_detail = ? "
                "WHERE batch_id = ?",
                (
                    f"Batch ended with {expired_count} expired + {canceled_count} canceled, "
                    "0 succeeded.",
                    batch_id,
                ),
            )
        return {
            "status": "failed",
            "error_detail": (
                f"Batch ended with {expired_count} expired + {canceled_count} canceled, "
                "0 succeeded."
            ),
            "memos_count": row["memos_count"],
        }

    suggestions = [_parse_batch_result(r) for r in results]
    cost_usd = _batch_cost_usd(results)

    with open_db() as conn:
        conn.execute(
            "UPDATE pending_batches "
            "SET status = 'completed', completed_at = CURRENT_TIMESTAMP, "
            "    result_json = ?, cost_usd = ? "
            "WHERE batch_id = ?",
            (json.dumps(suggestions), cost_usd, batch_id),
        )

    _stats_sink.record_batch_completed(cost_usd)

    return {
        "status": "completed",
        "suggestions": suggestions,
        "cost_usd": cost_usd,
        "completed_at": None,  # CURRENT_TIMESTAMP filled in by SQLite; client can re-query if needed
        "memos_count": row["memos_count"],
    }


# ---------------------------------------------------------------------------
# JSON Schemas for the Anthropic tool registry
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "get_unclassified_transactions",
        "description": (
            "Return up to `limit` recent transactions whose category_main is "
            "'Missing'. The classification backlog — the agent's main "
            "self-improvement loop reads from here, suggests a rule per memo, "
            "and asks the user to approve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
            "required": [],
        },
    },
    {
        "name": "list_categories",
        "description": (
            "Return the full category taxonomy in use ({main: {sub: [sub2]}}). "
            "Call this before suggest_classification or before proposing any "
            "new rule so categories stay consistent with what exists."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "suggest_classification",
        "description": (
            "Ask Claude Haiku for a classification suggestion for one "
            "transaction. Returns category fields + a proposed regex + a "
            "one-line rationale. Use this to draft a rule, then present the "
            "result to the user for approval before calling "
            "preview_rule_application / apply_classification_rule."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memo": {"type": "string"},
                "amount": {"type": "number"},
                "account_name": {"type": "string"},
            },
            "required": ["memo", "amount", "account_name"],
        },
    },
    {
        "name": "preview_rule_application",
        "description": (
            "Show how many Missing transactions a candidate rule would "
            "reclassify, and a sample of those matches. NO mutation. Use "
            "before apply_classification_rule so the user sees the blast "
            "radius before you commit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex against Memo (case-insensitive)."},
                "category_main": {"type": "string"},
                "category_sub": {"type": ["string", "null"]},
                "category_sub2": {"type": ["string", "null"]},
                "details": {"type": ["string", "null"]},
            },
            "required": ["pattern", "category_main"],
        },
    },
    {
        "name": "apply_classification_rule",
        "description": (
            "Write the rule to classification_rules AND retroactively "
            "reclassify all matching Missing transactions in one DB "
            "transaction. ONLY call after the user has explicitly approved "
            "the preview from preview_rule_application. Use ONLY for rules "
            "that map to a category already in the taxonomy. For NEW "
            "taxonomy entries, use preview_taxonomy_extension / "
            "apply_taxonomy_extension instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "category_main": {"type": "string"},
                "category_sub": {"type": ["string", "null"]},
                "category_sub2": {"type": ["string", "null"]},
                "details": {"type": ["string", "null"]},
            },
            "required": ["pattern", "category_main"],
        },
    },
    {
        "name": "preview_taxonomy_extension",
        "description": (
            "Preview adding a NEW taxonomy entry (main / sub / sub2 tuple "
            "not currently in the taxonomy). Validates the tuple is "
            "unprecedented and the pattern matches at least one Missing row. "
            "No DB writes. Use this when the user encounters a transaction "
            "that doesn't fit any existing category and a new bucket is "
            "warranted (e.g. adding 'Health/Mental/therapy' for HEADSPACE). "
            "Call list_categories first to confirm the tuple is genuinely new."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category_main": {"type": "string"},
                "category_sub": {"type": ["string", "null"]},
                "category_sub2": {"type": ["string", "null"]},
                "pattern": {
                    "type": "string",
                    "description": "Python regex (start-anchored via re.match, case-insensitive). Prefix with `.*` to match anywhere.",
                },
                "details": {"type": ["string", "null"]},
            },
            "required": ["category_main", "pattern"],
        },
    },
    {
        "name": "apply_taxonomy_extension",
        "description": (
            "Add a new taxonomy entry by inserting the seed rule into "
            "classification_rules and reclassifying matching Missing rows in "
            "one transaction. ONLY call after the user has approved the "
            "preview from preview_taxonomy_extension. Re-validates that the "
            "tuple is still new and the pattern still matches >0 rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category_main": {"type": "string"},
                "category_sub": {"type": ["string", "null"]},
                "category_sub2": {"type": ["string", "null"]},
                "pattern": {"type": "string"},
                "details": {"type": ["string", "null"]},
            },
            "required": ["category_main", "pattern"],
        },
    },
    {
        "name": "bulk_classify_async",
        "description": (
            "Submit a batch of Missing transactions to Anthropic's Batch API "
            "for asynchronous classification. 50% cheaper than per-row "
            "suggest_classification but results aren't immediate. Use when "
            "the Missing backlog has more than ~10 rows; for smaller "
            "backlogs prefer suggest_classification for the immediate "
            "response. Returns {batch_id, status, memos_count, eta_hint}. "
            "Call check_batch_results(batch_id) later (this session or a "
            "future one — pending batches are announced in the system "
            "prompt) to retrieve suggestions. Suggestions still go through "
            "the normal preview_rule_application / apply_classification_rule "
            "approval flow before any DB mutation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memos": {
                    "type": "array",
                    "description": (
                        "List of transactions to classify. Each item is the "
                        "shape returned by get_unclassified_transactions."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "memo": {"type": "string"},
                            "amount": {"type": "number"},
                            "account_name": {"type": "string"},
                        },
                        "required": ["id", "memo", "amount", "account_name"],
                    },
                    "minItems": 1,
                },
            },
            "required": ["memos"],
        },
    },
    {
        "name": "check_batch_results",
        "description": (
            "Poll Anthropic for a previously-submitted batch_id. Returns "
            "{status: 'in_progress'} if not done, {status: 'completed', "
            "suggestions: [...]} on success, or {status: 'failed', "
            "error_detail} on expiry/cancellation. Idempotent: a completed "
            "batch is cached locally so repeated calls don't re-query "
            "Anthropic. Each suggestion includes a transaction_id matched "
            "back to the original Missing row."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
            },
            "required": ["batch_id"],
        },
    },
]
