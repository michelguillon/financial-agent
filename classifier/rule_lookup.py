"""rule_lookup.py — SQLite-backed classification lookup.

A1 (Phase 2 SPEC §3.4) migrated the hardcoded chain in
bank_statement_parser.py into the classification_rules table; this module
is now the only path. On miss the result is "Missing" with NULL subs.

A rule fires when:
  - its `pattern` REGEXPs the row's Memo (case-insensitive); AND
  - any of its optional conditions (account_match, type_match,
    amount_min, amount_max) either match or are NULL.

Order of insertion = order of evaluation (lowest id wins). The
classifier/rules_seed.py module is the canonical source list; db/seed_rules.py
loads it.

Usage:

    from classifier.rule_lookup import categories
    result = categories(df_row)   # pd.Series([main, sub, sub2, details])

The first call opens a module-level connection to the project DB.
Tests can call `reset_connection()` to drop it.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.database import get_connection, register_regexp  # noqa: E402


# ---------------------------------------------------------------------------
# Connection management (lazy, module-level)
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = get_connection()
        register_regexp(_conn)
    return _conn


def reset_connection() -> None:
    """Close and drop the module-level connection. Used by tests."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

_LOOKUP_SQL = """
    SELECT category_main, category_sub, category_sub2, details
    FROM classification_rules
    WHERE ? REGEXP pattern
      AND (account_match IS NULL OR account_match = ?)
      AND (type_match    IS NULL OR type_match    = ?)
      AND (amount_min    IS NULL OR ? >= amount_min)
      AND (amount_max    IS NULL OR ? <  amount_max)
    ORDER BY id LIMIT 1
"""
# Convention: amount_min is INCLUSIVE (>=), amount_max is EXCLUSIVE (<).
# Mirrors Python's range(start, stop). The seed PRET-café rule uses
# amount_max=5 to mean "strictly under £5" — preserving the original
# hardcoded chain's `abs(amount) < 5` semantics.


def lookup_in_rules_table(
    memo: str | None,
    account_number: str | None = None,
    type_: str | None = None,
    amount: float | None = None,
) -> tuple | None:
    """Search classification_rules for a matching row.

    Returns (category_main, category_sub, category_sub2, details) on hit,
    None on miss. Earlier rules (lower id) win on conflict.

    `amount` is compared as absolute value — the convention is that
    amount_min/amount_max in the rules table refer to the cost of the
    transaction regardless of sign.
    """
    if memo is None:
        return None
    abs_amount = abs(amount) if amount is not None else None
    conn = _get_conn()
    row = conn.execute(
        _LOOKUP_SQL,
        (memo, account_number, type_, abs_amount, abs_amount),
    ).fetchone()
    if row is None:
        return None
    return (row["category_main"], row["category_sub"],
            row["category_sub2"], row["details"])


def categories(df: pd.Series) -> pd.Series:
    """Classify one transaction. Returns pd.Series([main, sub, sub2, details]).

    Falls back to ["Missing", None, None, None] when no rule matches.
    """
    hit = lookup_in_rules_table(
        df.get("Memo"),
        df.get("Account Number"),
        df.get("Type"),
        df.get("Amount"),
    )
    if hit is not None:
        return pd.Series(list(hit))
    return pd.Series(["Missing", None, None, None])
