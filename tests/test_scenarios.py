"""Tests for agent.tools.scenarios — read-only summaries + scenario modelling.

All assertions are numeric-range checks against the synthetic dataset
(deterministic seed=42). Use `tmp_db` rather than `seed_db` so other tests
in the suite can run in any order without read-vs-write isolation worries.
"""
from __future__ import annotations

import pytest

from agent.tools.scenarios import (
    classify_fixed_vs_discretionary,
    get_income_summary,
    get_spending_summary,
    model_scenario,
)


def test_spending_summary_within_expected_range(tmp_db):
    s = get_spending_summary(months=12)
    assert 30_000 < s["grand_total"] < 100_000, \
        f"spend total looks off: {s['grand_total']}"
    assert s["monthly_avg_total"] > 0
    assert len(s["by_category"]) > 0


def test_income_summary_includes_recent_employer(tmp_db):
    inc = get_income_summary(months=12)
    assert inc["monthly_avg"] > 0
    assert any(s["name"] == "COMPANY_C" for s in inc["sources"]), \
        f"COMPANY_C missing from recent income: {inc['sources']}"


def test_fixed_vs_discretionary_split(tmp_db):
    fd = classify_fixed_vs_discretionary(months=12)
    assert fd["fixed"]["total"] > 15_000, \
        f"fixed total looks low: {fd['fixed']['total']}"
    assert fd["discretionary"]["total"] > 0


def test_job_loss_scenario_yields_negative_surplus(tmp_db):
    r = model_scenario("job_loss", {"income_reduction_pct": 100})
    assert r["new_monthly_surplus"] < 0
    assert len(r["recommendations"]) > 0


def test_rate_change_delta_matches_arithmetic(tmp_db):
    # 2% extra on £185k = £3,700/yr ≈ £308.33/mo
    r = model_scenario("rate_change", {
        "current_rate": 0.02, "new_rate": 0.04,
        "mortgage_balance": 185_000, "effective_date": "2027-03-01",
    })
    assert 250 < r["monthly_payment_delta"] < 400, \
        f"rate delta calc off: {r['monthly_payment_delta']}"


def test_expense_change_subtracts_from_surplus(tmp_db):
    r = model_scenario("expense_change", {
        "category": "Leisure/food/drinks", "monthly_delta": 200,
    })
    assert abs(r["new_monthly_surplus"] - (r["current_monthly_surplus"] - 200)) < 0.01
