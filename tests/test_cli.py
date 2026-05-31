"""Crash-only smoke tests for agent.cli.RichRenderer.

Snapshotting Rich's output is brittle across terminal widths/colour modes,
so we just exercise each public method with mock blocks and assert no
exception. Real visual checking is done by `python -m agent.cli`.
"""
from __future__ import annotations

import pytest
from rich.console import Console

from agent.agent import SilentRenderer
from agent.cli import RichRenderer


@pytest.fixture
def renderer():
    # Force a fixed-width console with no colour so output is reproducible
    # even though we don't assert on it.
    console = Console(width=120, color_system=None, force_terminal=False)
    return RichRenderer(console=console)


def test_show_assistant_text_renders_markdown(renderer):
    renderer.show_assistant_text("# Hello\n\nThis is **bold**.\n- one\n- two")


def test_show_tool_call_and_result_spending(renderer):
    renderer.show_tool_call("get_spending_summary", {"months": 12})
    renderer.show_tool_result("get_spending_summary", {
        "period": "2025-01 to 2025-12", "months": 12,
        "by_category": {
            "House/Mortgage": {"total": 17040.00, "monthly_avg": 1420.00},
        },
        "grand_total": 64287.16, "monthly_avg_total": 5357.26,
    })


def test_show_tool_result_preview_and_apply(renderer):
    renderer.show_tool_result("preview_rule_application", {
        "would_match": 24,
        "sample_matches": [{"id": 1, "date": "2025-12-01",
                            "amount": -14.14, "memo": "NETFLIX.COM 9192"}],
        "proposed_classification": {
            "category_main": "Leisure", "category_sub": "subscription",
            "category_sub2": "music", "details": None,
        },
    })
    renderer.show_tool_result("apply_classification_rule", {
        "rules_added": 1, "rule_id": 7, "transactions_reclassified": 24,
    })


def test_show_tool_result_scenario(renderer):
    renderer.show_tool_result("model_scenario", {
        "scenario": "rate_change",
        "current_monthly_surplus": 265.79,
        "new_monthly_surplus": -42.54,
        "gap": 308.33,
        "monthly_payment_delta": 308.33,
        "recommendations": [{"category": "Shopping/Groceries",
                             "current_monthly": 1139.70,
                             "suggested_monthly": 569.85,
                             "potential_saving": 569.85,
                             "type": "discretionary"}],
    })


def test_show_tool_result_unknown_tool_falls_back_to_json(renderer):
    renderer.show_tool_result("some_unregistered_tool", {"any": "thing"})


def test_show_error_and_usage(renderer):
    renderer.show_error("tool:bad_thing", "Something went wrong")
    renderer.show_usage(
        input_tokens=1248, output_tokens=187,
        cache_read=12943, cache_creation=0,
        cost_usd=0.0058, turn=2,
    )


def test_silent_renderer_implements_protocol():
    sr = SilentRenderer()
    sr.show_tool_call("x", {})
    sr.show_tool_result("x", {})
    sr.show_assistant_text("x")
    sr.show_error("x", "y")
    sr.show_usage(input_tokens=0, output_tokens=0,
                  cache_read=0, cache_creation=0,
                  cost_usd=0.0, turn=1)
    assert sr.prompt("label") == ""
