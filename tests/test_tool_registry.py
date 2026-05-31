"""Tests for agent.tool_registry — schema/dispatch pairing."""
from __future__ import annotations

import pytest

from agent.tool_registry import ANTHROPIC_TOOLS, TOOL_FUNCTIONS, dispatch


def test_every_schema_has_a_callable():
    for schema in ANTHROPIC_TOOLS:
        name = schema["name"]
        assert name in TOOL_FUNCTIONS, f"missing dispatch entry: {name}"
        assert callable(TOOL_FUNCTIONS[name])


def test_dispatch_read_only_tool(tmp_db):
    result = dispatch("get_unclassified_transactions", {"limit": 3})
    assert isinstance(result, list)
    assert len(result) <= 3


def test_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError, match="Unknown tool"):
        dispatch("nope_not_a_tool", {})


def test_registered_count_matches_modules():
    # 13 tools total across state (2), classification (7), scenarios (4)
    assert len(ANTHROPIC_TOOLS) == 13
