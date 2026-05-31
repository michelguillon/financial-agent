"""SQLite connection + helper utilities for the personal finance agent.

Single module, intentionally small. Adds new helpers here only when a tool
actually needs them — speculative helpers rot fastest.

The DB lives at `data/finance.db` (gitignored). It sits under data/ so the
Docker bind-mount of `./data:/app/data` covers it without needing a
separate single-file mount (which would create a directory on fresh
checkouts where the file doesn't yet exist). Demo mode (SPEC §3.6) is
determined by whether `data/real/` exists.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

# Project root = parent of the db/ directory this file lives in.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "finance.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
REAL_DATA_DIR = DATA_DIR / "real"


# ---------------------------------------------------------------------------
# Python 3.12 removed the built-in date/datetime adapters. Register explicit
# ISO-format adapters/converters so date columns round-trip correctly with
# `detect_types=PARSE_DECLTYPES`. Registered at import time, once.
# ---------------------------------------------------------------------------
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat(sep=" "))
sqlite3.register_converter("date", lambda b: date.fromisoformat(b.decode()))
sqlite3.register_converter("datetime", lambda b: datetime.fromisoformat(b.decode()))


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    - row_factory = sqlite3.Row so callers can use column-name access
    - foreign_keys = ON for the day we add them
    - PARSE_DECLTYPES so DATE/DATETIME columns come back as native types
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Idempotent — all CREATEs use IF NOT EXISTS."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


@contextmanager
def open_db(db_path: Path | None = None, *, init: bool = True):
    """Context manager: open conn, optionally init schema, commit on exit."""
    conn = get_connection(db_path)
    try:
        if init:
            init_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_data_source() -> str:
    """Demo-mode switch per SPEC §3.6.

    Returns 'real' if data/real/ contains any *.csv file, else 'synthetic'.
    """
    if REAL_DATA_DIR.exists() and any(REAL_DATA_DIR.glob("*.csv")):
        return "real"
    return "synthetic"


# ---------------------------------------------------------------------------
# SQLite REGEXP — Python regex backing for the `X REGEXP Y` operator.
# Used by classifier/rule_lookup.py and agent/tools/classification.py so the
# pattern matching is consistent everywhere (case-insensitive re.search).
# ---------------------------------------------------------------------------

def _regexp(pattern: str | None, value: str | None) -> bool:
    """Backing function for SQLite's REGEXP operator.

    Uses `re.match` (start-anchored) to match the original hardcoded
    chain's semantics — patterns with a `.*` prefix opt into "match
    anywhere" explicitly, patterns without it match only at the start of
    the memo. `^` in a pattern is redundant but harmless.

    Returns False (not error) on NULL inputs or invalid patterns, so a
    single bad rule can't crash queries that scan many rows.
    """
    if pattern is None or value is None:
        return False
    try:
        return re.match(pattern, value, re.IGNORECASE) is not None
    except re.error:
        return False


def register_regexp(conn: sqlite3.Connection) -> None:
    """Register the Python REGEXP function on a SQLite connection.

    Call once per connection that needs `WHERE col REGEXP ?` queries.
    Idempotent — re-registering the same name is harmless.
    """
    conn.create_function("REGEXP", 2, _regexp)
