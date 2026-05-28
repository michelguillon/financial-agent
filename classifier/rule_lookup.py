"""rule_lookup.py — Phase 1 SQLite-first wrapper around the hardcoded
categories() chain in bank_statement_parser.

SPEC §3.4: when categorising a row, consult the `classification_rules`
table first; on miss, fall through to the unchanged hardcoded chain.
That hardcoded chain stays the fallback for everything not yet promoted
into a SQL rule.

Phase 2 (out of scope) migrates the hardcoded chain itself into the
rules table.

Usage:

    from classifier.rule_lookup import categories
    result = categories(df_row)   # drop-in replacement for the original

The first call opens a module-level connection to the project DB.
Tests can call `reset_connection()` to drop it.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# Allow `python classifier/rule_lookup.py` or `from classifier.rule_lookup ...`
# to find the sibling db package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.database import get_connection  # noqa: E402
from classifier.bank_statement_parser import categories as _hardcoded_categories  # noqa: E402


# ---------------------------------------------------------------------------
# Connection management (lazy, module-level)
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _regexp(pattern: str | None, value: str | None) -> bool:
    """SQLite REGEXP operator backed by Python's re.search (case-insensitive).

    Returns False (not error) on invalid pattern or NULL inputs, so a single
    malformed rule can't crash the whole lookup.
    """
    if pattern is None or value is None:
        return False
    try:
        return re.search(pattern, value, re.IGNORECASE) is not None
    except re.error:
        return False


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = get_connection()
        _conn.create_function("REGEXP", 2, _regexp)
    return _conn


def reset_connection() -> None:
    """Close and drop the module-level connection. Used by tests."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Lookup + wrapper
# ---------------------------------------------------------------------------

def lookup_in_rules_table(memo: str | None) -> tuple | None:
    """Search classification_rules for a regex match on memo.

    Returns (category_main, category_sub, category_sub2, details) on hit,
    None on miss. Earlier rules (lower id) win on conflict.
    """
    if memo is None:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT category_main, category_sub, category_sub2, details "
        "FROM classification_rules "
        "WHERE ? REGEXP pattern "
        "ORDER BY id LIMIT 1",
        (memo,),
    ).fetchone()
    if row is None:
        return None
    return (row["category_main"], row["category_sub"],
            row["category_sub2"], row["details"])


def categories(df: pd.Series) -> pd.Series:
    """Drop-in replacement for bank_statement_parser.categories.

    1. Check the SQLite rules table.
    2. On miss, fall through to the unchanged hardcoded chain.

    The same pd.Series shape as the original is returned, so existing
    callers (the Budget class, the migration tool) don't need to change.
    """
    hit = lookup_in_rules_table(df.get("Memo"))
    if hit is not None:
        return pd.Series(list(hit))
    return _hardcoded_categories(df)
