"""Tests for db.database — schema init + REGEXP registration + per-session DB ContextVar."""
from __future__ import annotations

import asyncio

from db.database import SESSION_DB_PATH, get_connection, init_schema, open_db, register_regexp


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


# ---------------------------------------------------------------------------
# SESSION_DB_PATH ContextVar (C4 — web UI per-session DB isolation)
# ---------------------------------------------------------------------------

def test_session_db_path_overrides_module_default(tmp_path, monkeypatch):
    fallback = tmp_path / "fallback.db"
    session = tmp_path / "session.db"
    # Initialise both so get_connection succeeds.
    with open_db(fallback): pass
    with open_db(session): pass

    monkeypatch.setattr("db.database.DB_PATH", fallback)

    token = SESSION_DB_PATH.set(session)
    try:
        conn = get_connection()
        # sqlite3.Connection has no `.path` attribute — use PRAGMA database_list.
        info = conn.execute("PRAGMA database_list").fetchall()
        conn.close()
        assert str(session) in info[0]["file"]
    finally:
        SESSION_DB_PATH.reset(token)

    # After reset, falls back to monkeypatched DB_PATH.
    conn = get_connection()
    info = conn.execute("PRAGMA database_list").fetchall()
    conn.close()
    assert str(fallback) in info[0]["file"]


def test_session_db_path_propagates_across_to_thread(tmp_path):
    """The web turn handler sets SESSION_DB_PATH then runs the agent in
    asyncio.to_thread. ContextVar must propagate so tools see the right DB."""
    session = tmp_path / "via_thread.db"
    with open_db(session): pass

    def thread_body() -> str:
        conn = get_connection()
        info = conn.execute("PRAGMA database_list").fetchone()
        conn.close()
        return info["file"]

    async def runner() -> str:
        SESSION_DB_PATH.set(session)
        return await asyncio.to_thread(thread_body)

    seen = asyncio.run(runner())
    assert str(session) in seen


def test_explicit_db_path_arg_wins_over_context_var(tmp_path):
    via_arg = tmp_path / "via_arg.db"
    via_ctx = tmp_path / "via_ctx.db"
    with open_db(via_arg): pass
    with open_db(via_ctx): pass

    token = SESSION_DB_PATH.set(via_ctx)
    try:
        conn = get_connection(db_path=via_arg)
        info = conn.execute("PRAGMA database_list").fetchone()
        conn.close()
        assert str(via_arg) in info["file"]
    finally:
        SESSION_DB_PATH.reset(token)
