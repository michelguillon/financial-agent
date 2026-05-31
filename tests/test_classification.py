"""Tests for agent.tools.classification — preview/apply two-step + LLM suggest."""
from __future__ import annotations

import pytest

from agent.tools.classification import (
    apply_classification_rule,
    get_unclassified_transactions,
    list_categories,
    preview_rule_application,
    suggest_classification,
)


def test_get_unclassified_returns_missing_rows(tmp_db):
    rows = get_unclassified_transactions(limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert "memo" in r and "amount" in r and "date" in r


def test_list_categories_has_expected_mains(tmp_db):
    taxonomy = list_categories()
    expected = {"Income", "House", "Shopping", "Transport", "Leisure",
                "Bills", "Savings", "Withdrawal", "Health"}
    missing = expected - set(taxonomy.keys())
    assert not missing, f"taxonomy missing main categories: {missing}"


def test_preview_does_not_mutate(tmp_db):
    before = get_unclassified_transactions(limit=1000)
    preview_rule_application(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    after = get_unclassified_transactions(limit=1000)
    assert len(before) == len(after)


def test_preview_and_apply_counts_match(tmp_db):
    preview = preview_rule_application(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    assert preview["would_match"] >= 10

    result = apply_classification_rule(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    assert result["rules_added"] == 1
    assert result["transactions_reclassified"] == preview["would_match"]


def test_apply_removes_rows_from_missing(tmp_db):
    apply_classification_rule(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    remaining = get_unclassified_transactions(limit=1000)
    netflix = [r for r in remaining if "NETFLIX" in r["memo"]]
    assert netflix == []


@pytest.mark.llm
def test_suggest_classification_for_dishoom(tmp_db):
    out = suggest_classification(
        memo="DISHOOM SHOREDITCH 1234",
        amount=-45.20,
        account_name="Amex",
    )
    assert out["category_main"] == "Leisure", \
        f"Haiku misclassified Dishoom: {out['category_main']!r}"
    assert "DISHOOM" in out["suggested_pattern"].upper()
