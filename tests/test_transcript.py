"""Tests for agent.transcript — JSONL session logger."""
from __future__ import annotations

import json

from agent.transcript import Transcript


def _read_records(path) -> list[dict]:
    return [json.loads(L) for L in path.read_text(encoding="utf-8").splitlines() if L.strip()]


def test_records_all_event_types(tmp_path):
    with Transcript(log_dir=tmp_path) as t:
        t.record("user", content="What did I spend on this year?")
        t.record(
            "assistant",
            content=[
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tu_1", "name": "get_spending_summary", "input": {"months": 12}},
            ],
        )
        t.record(
            "tool_result",
            tool_use_id="tu_1",
            name="get_spending_summary",
            output={"grand_total": 64287.16},
        )
        t.record("assistant", content=[{"type": "text", "text": "You spent £64,287."}])
        t.record("usage", turn=1, tokens={"in": 1248, "out": 187}, cost_usd=0.0058)
        path = t.path

    records = _read_records(path)
    assert records[0]["type"] == "session_start"
    assert records[-1]["type"] == "session_end"
    assert records[-1]["reason"] == "normal"
    types = {r["type"] for r in records}
    assert {"user", "assistant", "tool_result", "usage"} <= types


def test_session_end_records_exception(tmp_path):
    try:
        with Transcript(log_dir=tmp_path) as t:
            t.record("user", content="hello")
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    records = _read_records(t.path)
    assert records[-1]["type"] == "session_end"
    assert records[-1]["reason"] == "exception"
    assert "boom" in records[-1]["detail"]


def test_record_outside_context_raises(tmp_path):
    t = Transcript(log_dir=tmp_path)
    try:
        t.record("user", content="too early")
    except RuntimeError as e:
        assert "context manager" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
