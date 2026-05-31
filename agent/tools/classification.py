"""classification.py — agent tools for processing the Missing backlog (SPEC §5.1).

Five tools:
  - get_unclassified_transactions(limit)
  - suggest_classification(memo, amount, account_name)   [calls Claude Haiku]
  - preview_rule_application(pattern, ...)               [no mutation]
  - apply_classification_rule(pattern, ...)              [mutates Missing rows]
  - list_categories()

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
- The regex is Python's re.search applied case-insensitively against the
  memo. Make it general enough to match future variants (different store
  numbers, dates, IDs) but specific enough to avoid false positives.
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
            "the preview from preview_rule_application."
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
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    print("=== get_unclassified_transactions ===")
    rows = get_unclassified_transactions(limit=5)
    assert 0 < len(rows) <= 5, f"unexpected row count: {len(rows)}"
    for r in rows:
        print(f"  {r['date']} {r['account_name']:<12} {r['amount']:>8.2f}  {r['memo']}")

    print("\n=== list_categories ===")
    taxonomy = list_categories()
    expected_mains = {"Income", "House", "Shopping", "Transport", "Leisure",
                      "Bills", "Savings", "Withdrawal", "Health"}
    found_mains = set(taxonomy.keys())
    missing = expected_mains - found_mains
    assert not missing, f"taxonomy is missing main categories: {missing}"
    print(f"  Found {len(taxonomy)} main categories: {sorted(taxonomy.keys())}")
    print(f"  Shopping subs: {sorted(taxonomy['Shopping'].keys())}")

    print("\n=== preview_rule_application (NETFLIX -> Leisure/entertainment) ===")
    preview = preview_rule_application(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    print(f"  would_match: {preview['would_match']}")
    assert preview["would_match"] >= 10, \
        f"Expected >=10 NETFLIX rows in Missing, got {preview['would_match']}"
    for s in preview["sample_matches"][:3]:
        print(f"    {s['date']} {s['amount']:>7.2f}  {s['memo']}")

    # Snapshot the IDs that will be mutated so we can restore after the test.
    target_ids = [s["id"] for s in preview["sample_matches"]]
    # Get all ids that would be matched (not just the sample) for restore.
    from db.database import open_db as _open_db
    with _open_db() as _c:
        from db.database import register_regexp as _rr
        _rr(_c)
        all_target_ids = [r["id"] for r in _c.execute(
            "SELECT id FROM transactions WHERE category_main='Missing' "
            "AND data_source='synthetic' AND memo REGEXP ?", ("NETFLIX",)
        ).fetchall()]
    expected_n = len(all_target_ids)
    assert expected_n == preview["would_match"]

    print("\n=== apply_classification_rule ===")
    result = apply_classification_rule(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    print(f"  rules_added: {result['rules_added']}")
    print(f"  transactions_reclassified: {result['transactions_reclassified']}")
    assert result["transactions_reclassified"] == expected_n
    print(f"  PASS — preview count {expected_n} matches apply count")

    # Verify Missing count dropped by exactly that many.
    new_missing = get_unclassified_transactions(limit=1000)
    netflix_in_missing = [r for r in new_missing if "NETFLIX" in r["memo"]]
    assert len(netflix_in_missing) == 0, \
        f"NETFLIX rows still in Missing after apply: {len(netflix_in_missing)}"
    print(f"  Missing count now: {len(new_missing)} (NETFLIX rows: 0)")

    # Cleanup: restore the mutated rows to Missing and delete the test rule.
    with _open_db() as _c:
        placeholders = ",".join(["?"] * len(all_target_ids))
        _c.execute(
            f"UPDATE transactions SET category_main='Missing', category_sub=NULL, "
            f"category_sub2=NULL, details=NULL WHERE id IN ({placeholders})",
            all_target_ids,
        )
        _c.execute("DELETE FROM classification_rules WHERE id = ?", (result["rule_id"],))
    print(f"  Cleanup: restored {expected_n} rows to Missing, deleted test rule")

    # --- LLM-gated test ---------------------------------------------------
    if os.environ.get("RUN_LLM_TESTS") == "1":
        print("\n=== suggest_classification (LLM) ===")
        out = suggest_classification(
            memo="DISHOOM SHOREDITCH 1234",
            amount=-45.20,
            account_name="Amex",
        )
        print(f"  got: {out}")
        assert out["category_main"] == "Leisure", \
            f"Haiku misclassified Dishoom: {out['category_main']!r}"
        assert "DISHOOM" in out["suggested_pattern"].upper()
        print("  PASS")
    else:
        print("\n=== suggest_classification (skipped — set RUN_LLM_TESTS=1 to run) ===")

    print("\nAll classification.py smoke tests passed.")
