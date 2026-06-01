"""Tests for agent.agent — system prompt, dispatch loop, end-to-end (LLM)."""
from __future__ import annotations

from datetime import date

import pytest

from agent.agent import (
    Session,
    SilentRenderer,
    build_system_prompt,
    format_state_snapshot,
    run_turn,
)


# ---------------------------------------------------------------------------
# build_system_prompt + format_state_snapshot — deterministic
# ---------------------------------------------------------------------------

def test_system_prompt_has_two_cacheable_blocks(tmp_db):
    blocks = build_system_prompt("synthetic", date(2026, 5, 30))
    assert len(blocks) == 2
    assert all(b.get("cache_control") == {"type": "ephemeral"} for b in blocks)
    static_text = blocks[0]["text"]
    assert "preview-before-apply" in static_text.lower() \
        or "preview_rule_application" in static_text
    assert "synthetic" in blocks[1]["text"]


def test_format_state_snapshot_empty():
    assert "no facts" in format_state_snapshot([])


def test_system_prompt_includes_pending_batch_summary(tmp_db):
    """C2: pending batches surface as a one-liner in the dynamic block."""
    import json as _json

    from db.database import open_db

    with open_db() as conn:
        conn.execute(
            "INSERT INTO pending_batches "
            "(batch_id, status, memos_count, transaction_ids, data_source) "
            "VALUES (?, 'in_progress', ?, ?, ?)",
            ("batch_pending_001", 42, _json.dumps([1, 2, 3]), "synthetic"),
        )
    blocks = build_system_prompt("synthetic", date(2026, 6, 1))
    dyn = blocks[1]["text"]
    assert "batch_pending_001" in dyn
    assert "42 memos" in dyn
    assert "check_batch_results" in dyn


def test_format_state_snapshot_populated():
    out = format_state_snapshot([
        {"key": "test_a", "value": 412.50, "confidence": "calculated",
         "rationale": "6m grocery avg"},
        {"key": "test_b", "value": ["A", "B"], "confidence": "inferred",
         "rationale": "fixed cats"},
    ])
    assert "test_a" in out and "412.5" in out
    assert "[calculated; 6m grocery avg]" in out


# ---------------------------------------------------------------------------
# Dispatch error injection — mocks the Anthropic SDK with monkeypatch
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeBlock:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.usage = _FakeUsage()
        self.stop_reason = "end_turn"


class _FakeMessages:
    @staticmethod
    def create(**kw): pass


class _FakeClient:
    messages = _FakeMessages()


def test_apply_without_preview_blocked_by_gate(tmp_db, monkeypatch):
    """B1 end-to-end: assistant calls apply_classification_rule directly,
    with no preview earlier in the session. The dispatch gate raises
    ApprovalRequiredError, which the loop converts into an is_error
    tool_result, and the loop continues."""
    from agent.tool_registry import dispatch as real_dispatch

    call_log: list[str] = []

    def fake_create(**kw):
        if not call_log:
            call_log.append("tool_use_phase")
            return _FakeResp([_FakeBlock(
                "tool_use", id="tu_1", name="apply_classification_rule",
                input={"pattern": ".*tesco.*", "category_main": "Groceries"},
            )])
        call_log.append("text_phase")
        return _FakeResp([_FakeBlock("text", text="Sorry — I need to preview first.")])

    monkeypatch.setattr("agent.agent.call_with_retry",
                        lambda func, *a, **kw: fake_create(**kw))
    monkeypatch.setattr("agent.agent.get_client", lambda: _FakeClient())

    session = Session(data_source="synthetic")
    final = run_turn(session, "Apply the tesco rule now.", SilentRenderer(),
                     dispatch_fn=real_dispatch)

    # 4 messages: user prompt, assistant w/ tool_use, user w/ tool_result, assistant w/ text
    assert len(session.messages) == 4
    tool_result = session.messages[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is True
    assert "preview_rule_application" in tool_result["content"]
    assert final == "Sorry — I need to preview first."


def test_dispatch_error_propagates_as_tool_result(tmp_db, monkeypatch):
    call_log: list[str] = []

    def fake_create(**kw):
        if not call_log:
            call_log.append("tool_use_phase")
            return _FakeResp([_FakeBlock(
                "tool_use", id="tu_1", name="get_agent_state", input={"key": "nope"},
            )])
        call_log.append("text_phase")
        return _FakeResp([_FakeBlock("text", text="Done.")])

    def bad_dispatch(name, input, *, messages=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("agent.agent.call_with_retry",
                        lambda func, *a, **kw: fake_create(**kw))
    monkeypatch.setattr("agent.agent.get_client", lambda: _FakeClient())

    session = Session(data_source="synthetic")
    final = run_turn(session, "Anything for key=nope?", SilentRenderer(),
                     dispatch_fn=bad_dispatch)

    assert len(session.messages) == 4
    tool_result = session.messages[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is True
    assert "kaboom" in tool_result["content"]
    assert final == "Done."


# ---------------------------------------------------------------------------
# End-to-end LLM test — real API, ~$0.08
# ---------------------------------------------------------------------------

@pytest.mark.llm
def test_end_to_end_three_turn_conversation(tmp_db):
    from agent.transcript import Transcript
    from db.database import get_data_source

    renderer = SilentRenderer()
    with Transcript() as t:
        session = Session(data_source=get_data_source())

        r1 = run_turn(
            session, "What's my total spending over the last 12 months?",
            renderer, t,
        )
        assert r1, "turn 1 produced no text"

        r2 = run_turn(
            session,
            "What would happen to my budget if my mortgage rate went from 2% "
            "to 4% on a £185000 balance?",
            renderer, t,
        )
        assert r2, "turn 2 produced no text"
        assert session.cache_read > 0, \
            "expected cache hit on turn 2 (system prompt cached)"

        r3 = run_turn(
            session,
            "Show me 3 unclassified transactions and suggest categories for "
            "them. Don't apply anything without my approval.",
            renderer, t,
        )
        assert r3, "turn 3 produced no text"

    assert session.cost_usd > 0
    assert session.turn_count == 3
