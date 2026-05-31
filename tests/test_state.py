"""Tests for agent.tools.state — agent_state CRUD."""
from __future__ import annotations

import pytest

from agent.tools.state import get_agent_state, set_agent_state


ROUND_TRIP_CASES = [
    ("k_float", 412.50, "monthly grocery avg over 6m", "calculated"),
    ("k_str", "salary", "primary income source per pattern", "inferred"),
    ("k_list", ["House/Mortgage", "Bills/utilities/water"],
        "fixed cost categories inferred from variance", "inferred"),
    ("k_dict", {"rate": 0.04, "from": "2027-03-01"},
        "future mortgage rate change confirmed by user", "user_confirmed"),
    ("k_bool", True, "user has refused subscriptions cut", "user_confirmed"),
]


@pytest.mark.parametrize("key,value,rationale,confidence", ROUND_TRIP_CASES)
def test_round_trip_preserves_type_and_metadata(tmp_db, key, value, rationale, confidence):
    set_agent_state(key, value, rationale, confidence)
    got = get_agent_state(key)
    assert got is not None
    assert got["value"] == value
    assert type(got["value"]) is type(value)
    assert got["confidence"] == confidence
    assert got["rationale"] == rationale


def test_upsert_overwrites(tmp_db):
    set_agent_state("k", 412.50, "initial", "calculated")
    set_agent_state("k", 450.75, "updated", "calculated")
    got = get_agent_state("k")
    assert got["value"] == 450.75
    assert got["rationale"] == "updated"


def test_missing_key_returns_none(tmp_db):
    assert get_agent_state("does_not_exist_xyz") is None


def test_blank_rationale_rejected(tmp_db):
    with pytest.raises(ValueError, match="rationale"):
        set_agent_state("k", 1, "")


def test_invalid_confidence_rejected(tmp_db):
    with pytest.raises(ValueError, match="confidence"):
        set_agent_state("k", 1, "ok", confidence="guessed")
