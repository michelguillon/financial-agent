"""Tests for agent.replay — JSONL transcript reader + renderer dispatcher."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agent import SilentRenderer
from agent.replay import ReplayStats, load_transcript, main, replay


# ---------------------------------------------------------------------------
# load_transcript
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_load_transcript_parses_jsonl(tmp_path):
    path = tmp_path / "t.jsonl"
    records = [
        {"ts": "2026-06-01T00:00:00+00:00", "type": "session_start", "session_id": "s1"},
        {"ts": "2026-06-01T00:00:01+00:00", "type": "user", "content": "hi"},
        {"ts": "2026-06-01T00:00:02+00:00", "type": "session_end", "reason": "normal"},
    ]
    _write_jsonl(path, records)
    loaded = list(load_transcript(path))
    assert [r["type"] for r in loaded] == ["session_start", "user", "session_end"]


def test_load_transcript_skips_blank_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "user", "content": "a"}\n\n   \n{"type": "user", "content": "b"}\n',
        encoding="utf-8",
    )
    records = list(load_transcript(path))
    assert [r["content"] for r in records] == ["a", "b"]


def test_load_transcript_raises_on_malformed_json(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "user", "content": "ok"}\n'
        'this is not json\n'
        '{"type": "user", "content": "more"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        list(load_transcript(path))
    assert "line 2" in str(exc.value)
    assert str(path) in str(exc.value)


# ---------------------------------------------------------------------------
# replay — dispatch coverage
# ---------------------------------------------------------------------------

class RecordingRenderer:
    """Captures every Renderer method call as a (name, args, kwargs) tuple."""

    def __init__(self):
        self.calls: list[tuple] = []

    def show_tool_call(self, name, input):
        self.calls.append(("show_tool_call", (name, input), {}))

    def show_tool_result(self, name, result, is_error=False):
        self.calls.append(("show_tool_result", (name, result), {"is_error": is_error}))

    def show_assistant_text(self, text):
        self.calls.append(("show_assistant_text", (text,), {}))

    def show_user_text(self, text):
        self.calls.append(("show_user_text", (text,), {}))

    def show_usage(self, **kwargs):
        self.calls.append(("show_usage", (), kwargs))

    def show_error(self, where, detail):
        self.calls.append(("show_error", (where, detail), {}))

    def prompt(self, label):
        return ""


def test_replay_dispatches_to_renderer_methods(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [
        {"ts": "2026-06-01T00:00:00+00:00", "type": "session_start", "session_id": "s1"},
        {"ts": "2026-06-01T00:00:01+00:00", "type": "user", "content": "hello"},
        {
            "ts": "2026-06-01T00:00:02+00:00", "type": "assistant",
            "content": [
                {"type": "text", "text": "thinking..."},
                {"type": "tool_use", "id": "tu_1", "name": "get_spending_summary",
                 "input": {"months": 12}},
            ],
        },
        {
            "ts": "2026-06-01T00:00:03+00:00", "type": "tool_result",
            "tool_use_id": "tu_1", "name": "get_spending_summary",
            "is_error": False, "output": {"grand_total": 100.0},
        },
        {
            "ts": "2026-06-01T00:00:04+00:00", "type": "usage",
            "turn": 1, "tokens": {"in": 100, "out": 50, "cache_read": 800, "cache_creation": 0},
            "cost_usd": 0.005,
        },
        {"ts": "2026-06-01T00:00:05+00:00", "type": "session_end", "reason": "normal"},
    ])

    r = RecordingRenderer()
    stats = replay(path, r, show_header=False)

    method_names = [c[0] for c in r.calls]
    assert method_names == [
        "show_user_text",
        "show_assistant_text",
        "show_tool_call",
        "show_tool_result",
        "show_usage",
    ]
    # The user-text call carried the right content
    assert r.calls[0][1] == ("hello",)
    # The tool_use call passed the right name + input
    assert r.calls[2][1] == ("get_spending_summary", {"months": 12})
    # The tool_result call passed is_error=False
    assert r.calls[3][2] == {"is_error": False}

    assert stats.turn_count == 1
    assert stats.tokens_in == 100
    assert stats.tokens_out == 50
    assert stats.cache_read == 800
    assert stats.cost_usd == pytest.approx(0.005)
    assert stats.errors == 0


def test_replay_renders_error_records_and_counts_them(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [
        {"type": "error", "where": "tool:foo", "detail": "boom"},
    ])
    r = RecordingRenderer()
    stats = replay(path, r, show_header=False)
    assert r.calls[0][0] == "show_error"
    assert r.calls[0][1] == ("tool:foo", "boom")
    assert stats.errors == 1


def test_replay_handles_unknown_record_type(tmp_path, capsys):
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [
        {"type": "novel_thing", "payload": "ignored"},
        {"type": "user", "content": "hi"},
    ])
    r = RecordingRenderer()
    stats = replay(path, r, show_header=False)

    captured = capsys.readouterr()
    assert "novel_thing" in captured.err
    # The good record after the unknown one still rendered.
    assert any(c[0] == "show_user_text" for c in r.calls)
    assert stats.unknown_record_types == ["novel_thing"]


def test_replay_recomputes_stats_across_two_usage_records(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [
        {"type": "usage", "turn": 1,
         "tokens": {"in": 100, "out": 50, "cache_read": 0, "cache_creation": 200},
         "cost_usd": 0.005},
        {"type": "usage", "turn": 2,
         "tokens": {"in": 200, "out": 80, "cache_read": 500, "cache_creation": 0},
         "cost_usd": 0.012},
    ])
    stats = replay(path, SilentRenderer(), show_header=False)
    assert stats.turn_count == 2
    assert stats.tokens_in == 300
    assert stats.tokens_out == 130
    assert stats.cache_read == 500
    assert stats.cache_creation == 200
    assert stats.cost_usd == pytest.approx(0.017)


def test_replay_real_transcript_doesnt_crash():
    """Smoke test: pick the most-recent real log under ./logs/ and replay it
    through SilentRenderer. Guards against subtle JSONL schema drift."""
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
    if not logs_dir.exists():
        pytest.skip("no logs/ directory in this checkout")
    candidates = sorted(logs_dir.glob("*.jsonl"))
    if not candidates:
        pytest.skip("no transcript files found under logs/")

    stats = replay(candidates[-1], SilentRenderer(), show_header=False)
    # The latest log should have at least one turn worth of activity. If the
    # most recent log was --no-log or aborted before any turn, fall back to
    # the previous file.
    if stats.turn_count == 0 and len(candidates) >= 2:
        stats = replay(candidates[-2], SilentRenderer(), show_header=False)
    assert stats.turn_count >= 0  # never negative
    assert stats.tokens_in >= 0
    assert stats.cost_usd >= 0


# ---------------------------------------------------------------------------
# main — argparse
# ---------------------------------------------------------------------------

def test_main_reports_missing_file(capsys, tmp_path):
    rc = main([str(tmp_path / "does_not_exist.jsonl")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_main_silent_mode_prints_one_line_summary(capsys, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [
        {"type": "session_start", "session_id": "s1"},
        {"type": "usage", "turn": 1,
         "tokens": {"in": 10, "out": 20, "cache_read": 0, "cache_creation": 0},
         "cost_usd": 0.001},
        {"type": "session_end", "reason": "normal"},
    ])
    rc = main([str(path), "--silent"])
    assert rc == 0
    out_lines = [L for L in capsys.readouterr().out.splitlines() if L.strip()]
    # Exactly one line of output — the summary. No header chatter.
    assert len(out_lines) == 1, f"expected 1 line, got: {out_lines!r}"
    out = out_lines[0]
    assert "turns=1" in out
    assert "in=10" in out
    assert "out=20" in out
    assert "cost_usd=0.0010" in out


def test_main_reports_malformed_jsonl(capsys, tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("not valid json\n", encoding="utf-8")
    rc = main([str(path), "--silent"])
    assert rc == 2
    assert "malformed JSON" in capsys.readouterr().err
