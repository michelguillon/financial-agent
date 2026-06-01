"""Tests for the FastAPI web demo (web/backend).

Mocks run_turn so no Anthropic API call happens — the tests cover the
scaffolding (session lifecycle, rate limit, budget cap, SSE shape,
per-session DB isolation), not the agent loop itself (covered elsewhere).
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.backend import app as app_module
from web.backend.app import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient that triggers FastAPI startup/shutdown (lifespan)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_run_turn(monkeypatch):
    """Replace run_turn with a deterministic stub that emits a few events
    via the renderer and returns. Captures call args for assertions."""
    captured: dict = {}

    def fake_run_turn(session, user_text, renderer, transcript=None, *, dispatch_fn=None, now=None):
        captured["user_text"] = user_text
        captured["session"] = session
        renderer.show_tool_call("get_unclassified_transactions", {"limit": 3})
        renderer.show_tool_result(
            "get_unclassified_transactions",
            [{"id": 1, "memo": "FAKE 123", "amount": -9.99}],
        )
        renderer.show_assistant_text("You have 1 unclassified transaction: FAKE 123.")
        renderer.show_usage(
            input_tokens=100, output_tokens=50,
            cache_read=0, cache_creation=0,
            cost_usd=0.0025, turn=1,
        )
        session.cost_usd += 0.0025
        session.turn_count += 1
        return "You have 1 unclassified transaction: FAKE 123."

    monkeypatch.setattr(app_module, "run_turn", fake_run_turn)
    return captured


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_sse(body: str) -> list[dict]:
    """Split an SSE body into [{event, data}] dicts."""
    events: list[dict] = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        event_line = next((L for L in chunk.splitlines() if L.startswith("event:")), None)
        data_line = next((L for L in chunk.splitlines() if L.startswith("data:")), None)
        if event_line and data_line:
            events.append({
                "event": event_line.split(":", 1)[1].strip(),
                "data": json.loads(data_line.split(":", 1)[1].strip()),
            })
    return events


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_session_returns_id_and_budget(client: TestClient):
    r = client.post("/api/sessions")
    assert r.status_code == 201
    body = r.json()
    assert "session_id" in body
    assert body["budget_total_usd"] == 0.50
    assert body["budget_used_usd"] == 0.0
    assert body["turns_so_far"] == 0
    assert body["sessions_remaining_today"] == 2  # 3 max, 1 used


def test_delete_session_is_idempotent(client: TestClient):
    r1 = client.post("/api/sessions")
    sid = r1.json()["session_id"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.delete(f"/api/sessions/{sid}").status_code == 204  # again — still 204


def test_turn_on_unknown_session_returns_404(client: TestClient):
    r = client.post("/api/sessions/does-not-exist/turn", json={"user_text": "hi"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Turn / SSE
# ---------------------------------------------------------------------------

def test_turn_streams_expected_event_sequence(client: TestClient, mock_run_turn):
    sid = client.post("/api/sessions").json()["session_id"]

    with client.stream("POST", f"/api/sessions/{sid}/turn",
                       json={"user_text": "show me unclassified"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    events = parse_sse(body)
    event_types = [e["event"] for e in events]

    # First event = session.info, last = turn.completed.
    assert event_types[0] == "session.info"
    assert event_types[-1] == "turn.completed"
    # Mocked run_turn emitted tool_call → tool_result → assistant_text → usage.
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "assistant_text" in event_types
    assert "usage" in event_types

    # turn.completed carries the final text + cumulative cost.
    completed = events[-1]["data"]
    assert completed["final_text"] == "You have 1 unclassified transaction: FAKE 123."
    assert completed["cumulative_cost_usd"] == pytest.approx(0.0025)

    # mock_run_turn captured the user text the agent received.
    assert mock_run_turn["user_text"] == "show me unclassified"


def test_turn_over_budget_yields_budget_exceeded_event(client: TestClient, mock_run_turn):
    sid = client.post("/api/sessions").json()["session_id"]
    # Force the session over budget without invoking the agent.
    ws = client.app.state.sessions.get(sid)
    ws.agent_session.cost_usd = 0.49  # +0.06 estimate = 0.55 > 0.50 cap

    with client.stream("POST", f"/api/sessions/{sid}/turn",
                       json={"user_text": "another turn"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    events = parse_sse(body)
    assert len(events) == 1
    assert events[0]["event"] == "budget.exceeded"
    assert events[0]["data"]["used_usd"] == 0.49


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

def test_fourth_session_for_same_ip_returns_429(client: TestClient):
    for _ in range(3):
        assert client.post("/api/sessions").status_code == 201
    r = client.post("/api/sessions")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


# ---------------------------------------------------------------------------
# Per-session DB isolation
# ---------------------------------------------------------------------------

def test_per_session_db_paths_are_distinct(client: TestClient):
    a = client.post("/api/sessions").json()["session_id"]
    b = client.post("/api/sessions").json()["session_id"]
    sessions = client.app.state.sessions
    ws_a = sessions.get(a)
    ws_b = sessions.get(b)
    assert ws_a.db_path != ws_b.db_path
    assert ws_a.db_path.exists()
    assert ws_b.db_path.exists()
    # And both seeded from the shared seed.db.
    assert sessions._seed_db_path is not None
    assert sessions._seed_db_path.exists()


# ---------------------------------------------------------------------------
# D2 follow-up: web replay toggle
# ---------------------------------------------------------------------------

def _read_sse_events(text: str) -> list[dict]:
    """Parse a complete SSE stream into [{type, data}, ...]."""
    events: list[dict] = []
    for raw in text.split("\n\n"):
        if not raw.strip():
            continue
        event_name, data_json = "", ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_json += line[len("data:"):].strip()
        if event_name:
            events.append({"type": event_name, "data": json.loads(data_json) if data_json else {}})
    return events


def test_replays_catalogue_lists_demo(client: TestClient):
    r = client.get("/api/replays")
    assert r.status_code == 200
    payload = r.json()
    assert "replays" in payload
    ids = {entry["id"] for entry in payload["replays"]}
    assert "demo_3turn" in ids
    # Shape check: each entry has the keys the React side needs.
    for entry in payload["replays"]:
        assert {"id", "title", "summary"} <= set(entry.keys())


def test_replay_stream_404_on_unknown_id(client: TestClient):
    r = client.get("/api/replays/does_not_exist/stream?delay=0")
    assert r.status_code == 404


def test_replay_stream_emits_expected_event_types(client: TestClient):
    r = client.get("/api/replays/demo_3turn/stream?delay=0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _read_sse_events(r.text)
    types = [e["type"] for e in events]
    # Opening + closing markers.
    assert types[0] == "replay.info"
    assert types[-1] == "replay.completed"
    # The bundled 3-turn demo must surface each block category at least once.
    assert "user_text" in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert "assistant_text" in types
    assert "usage" in types

    # First user_text matches the canned demo's opening prompt.
    first_user = next(e for e in events if e["type"] == "user_text")
    assert "spending" in first_user["data"]["text"].lower()


def test_replay_stream_clamps_delay(client: TestClient):
    # delay=99 must clamp to MAX_REPLAY_DELAY_SECONDS = 5; we don't measure
    # wall clock — we assert the replay.info event reports the clamped value.
    r = client.get("/api/replays/demo_3turn/stream?delay=99")
    assert r.status_code == 200
    events = _read_sse_events(r.text)
    info = next(e for e in events if e["type"] == "replay.info")
    assert info["data"]["delay_seconds"] == app_module.MAX_REPLAY_DELAY_SECONDS


def test_replay_bypasses_rate_limit_and_session(client: TestClient):
    # No session created — replay should not require one.
    for _ in range(5):  # exceed the 3/day session rate limit
        r = client.get("/api/replays/demo_3turn/stream?delay=0")
        assert r.status_code == 200, (
            f"replay stream should be free of rate-limit gating, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# /admin/stats
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client(monkeypatch) -> Iterator[TestClient]:
    """A client that boots with ADMIN_TOKEN set. Use this for tests that
    need the admin endpoint to actually be reachable."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-token")
    with TestClient(app) as c:
        yield c


ADMIN_HEADERS = {"X-Admin-Token": "test-token"}


def test_admin_stats_503_when_disabled(client: TestClient, monkeypatch):
    # Default `client` fixture doesn't set ADMIN_TOKEN — endpoint should
    # 503 rather than expose anything.
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    r = client.get("/admin/stats", headers=ADMIN_HEADERS)
    assert r.status_code == 503
    assert "ADMIN_TOKEN" in r.json()["detail"]


def test_admin_stats_401_on_missing_token(admin_client: TestClient):
    r = admin_client.get("/admin/stats")
    assert r.status_code == 401


def test_admin_stats_401_on_wrong_token(admin_client: TestClient):
    r = admin_client.get("/admin/stats", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_admin_stats_shape_when_authorised(admin_client: TestClient):
    r = admin_client.get("/admin/stats", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert {"process", "sessions", "turns", "replays", "batches"} <= set(body.keys())
    assert "uptime_seconds" in body["process"]
    assert body["sessions"]["active"] == 0
    assert body["sessions"]["created_total"] == 0
    assert body["turns"]["completed_total"] == 0
    assert body["replays"]["streams_started_total"] == 0
    assert body["replays"]["by_id"] == {}
    assert body["batches"]["submitted_total"] == 0
    assert body["batches"]["completed_total"] == 0
    assert body["batches"]["spend_usd_total"] == 0.0
    assert body["batches"]["last_batch_at"] is None


def test_admin_stats_increments_on_session_create(admin_client: TestClient):
    admin_client.post("/api/sessions")
    body = admin_client.get("/admin/stats", headers=ADMIN_HEADERS).json()
    assert body["sessions"]["created_total"] == 1
    assert body["sessions"]["active"] == 1


def test_admin_stats_increments_on_turn(admin_client: TestClient, mock_run_turn):
    session_id = admin_client.post("/api/sessions").json()["session_id"]
    admin_client.post(f"/api/sessions/{session_id}/turn", json={"user_text": "hi"})
    body = admin_client.get("/admin/stats", headers=ADMIN_HEADERS).json()
    assert body["turns"]["completed_total"] == 1
    assert body["turns"]["spend_usd_total"] > 0
    assert body["turns"]["last_turn_at"] is not None


def test_admin_stats_increments_on_replay(admin_client: TestClient):
    r = admin_client.get("/api/replays/demo_3turn/stream?delay=0")
    assert r.status_code == 200
    body = admin_client.get("/admin/stats", headers=ADMIN_HEADERS).json()
    assert body["replays"]["streams_started_total"] == 1
    assert body["replays"]["by_id"]["demo_3turn"] == 1
    assert body["replays"]["last_replay_at"] is not None


def test_admin_stats_counts_rate_limit_rejections(admin_client: TestClient):
    # 4th call exceeds MAX_SESSIONS_PER_IP_PER_DAY = 3.
    for _ in range(3):
        assert admin_client.post("/api/sessions").status_code == 201
    assert admin_client.post("/api/sessions").status_code == 429
    body = admin_client.get("/admin/stats", headers=ADMIN_HEADERS).json()
    assert body["sessions"]["rate_limit_rejections_total"] == 1
    assert body["sessions"]["created_total"] == 3
