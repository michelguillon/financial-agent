"""Tests for db.database — schema init + REGEXP registration."""
from __future__ import annotations

from db.database import init_schema, open_db, register_regexp


def test_open_db_creates_and_inits(tmp_path):
    path = tmp_path / "fresh.db"
    with open_db(path) as conn:
        # Schema includes a transactions table per db/schema.sql.
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "transactions" in tables
    assert "agent_state" in tables
    assert "classification_rules" in tables


def test_init_schema_is_idempotent(tmp_path):
    path = tmp_path / "fresh.db"
    with open_db(path) as conn:
        init_schema(conn)
        init_schema(conn)  # second call must not raise


def test_regexp_registers_and_matches(tmp_path):
    path = tmp_path / "fresh.db"
    with open_db(path) as conn:
        register_regexp(conn)
        (m,) = conn.execute("SELECT ? REGEXP ?", ("NETFLIX 1234", "NETFLIX")).fetchone()
        assert m == 1
        (m,) = conn.execute("SELECT ? REGEXP ?", ("SPOTIFY 9999", "NETFLIX")).fetchone()
        assert m == 0


def test_regexp_handles_nulls_and_bad_patterns(tmp_path):
    path = tmp_path / "fresh.db"
    with open_db(path) as conn:
        register_regexp(conn)
        (m,) = conn.execute("SELECT ? REGEXP ?", (None, "NETFLIX")).fetchone()
        assert m == 0
        (m,) = conn.execute("SELECT ? REGEXP ?", ("x", "[invalid")).fetchone()
        assert m == 0
