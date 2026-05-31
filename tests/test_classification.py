"""Tests for agent.tools.classification — preview/apply two-step + LLM suggest."""
from __future__ import annotations

import pytest

from agent.tools.classification import (
    apply_classification_rule,
    apply_taxonomy_extension,
    get_unclassified_transactions,
    list_categories,
    preview_rule_application,
    preview_taxonomy_extension,
    suggest_classification,
)
from db.database import open_db


def test_get_unclassified_returns_missing_rows(tmp_db):
    rows = get_unclassified_transactions(limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert "memo" in r and "amount" in r and "date" in r


def test_list_categories_has_expected_mains(tmp_db):
    taxonomy = list_categories()
    expected = {"Income", "House", "Shopping", "Transport", "Leisure",
                "Bills", "Savings", "Withdrawal", "Health", "Travel"}
    missing = expected - set(taxonomy.keys())
    assert not missing, f"taxonomy missing main categories: {missing}"


def test_a2_new_subs_present_in_taxonomy(tmp_db):
    taxonomy = list_categories()
    assert "rail" in taxonomy.get("Transport", {}), \
        "A2: Transport/rail missing from taxonomy"
    leisure_sub_video = taxonomy.get("Leisure", {}).get("subscription", [])
    assert "video" in leisure_sub_video, \
        f"A2: Leisure/subscription/video missing; got {leisure_sub_video}"
    assert "accommodation" in taxonomy.get("Travel", {}), \
        "A2: Travel/accommodation missing from taxonomy"


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


# ---------------------------------------------------------------------------
# A3 — extend_taxonomy (preview + apply for NEW (main, sub, sub2) tuples)
# ---------------------------------------------------------------------------

# APPLE.COM/BILL is in generate_synthetic.py's NOISE_MEMOS pool and no seed
# rule matches it, so the synthetic CSV reliably contains APPLE.COM rows in
# Missing — usable as the test merchant for the new-taxonomy-entry flow.


def test_preview_taxonomy_extension_accepts_new_tuple(tmp_db):
    out = preview_taxonomy_extension(
        category_main="Shopping", category_sub="digital", category_sub2="apps",
        pattern=".*APPLE\\.COM",
    )
    assert out["is_new"] is True
    assert out["proposed_taxonomy_entry"]["category_main"] == "Shopping"
    assert out["would_match"] > 0
    assert len(out["sample_matches"]) > 0


def test_preview_taxonomy_extension_rejects_existing_tuple(tmp_db):
    # ('Travel', 'accommodation', 'hotel') already exists post-A2.
    with pytest.raises(ValueError, match="already exists"):
        preview_taxonomy_extension(
            category_main="Travel", category_sub="accommodation",
            category_sub2="hotel", pattern=".*BOOKING\\.COM",
        )


def test_preview_taxonomy_extension_rejects_zero_matches(tmp_db):
    with pytest.raises(ValueError, match="0 Missing rows"):
        preview_taxonomy_extension(
            category_main="Shopping", category_sub="digital", category_sub2="apps",
            pattern="ZZZ_NEVER_MATCHES_ANY_MEMO",
        )


def test_apply_taxonomy_extension_lands_rule_and_reclassifies(tmp_db):
    result = apply_taxonomy_extension(
        category_main="Shopping", category_sub="digital", category_sub2="apps",
        pattern=".*APPLE\\.COM",
    )
    assert result["taxonomy_entry_added"]["category_main"] == "Shopping"
    assert result["transactions_reclassified"] > 0

    # Rule landed with added_by='agent' (not 'seed' — survives migrate.py re-seed).
    with open_db() as conn:
        row = conn.execute(
            "SELECT added_by, category_main, category_sub, category_sub2 "
            "FROM classification_rules WHERE id = ?",
            (result["rule_id"],),
        ).fetchone()
    assert row["added_by"] == "agent"
    assert row["category_sub2"] == "apps"

    # New tuple now appears in list_categories.
    taxonomy = list_categories()
    assert "apps" in taxonomy["Shopping"].get("digital", []), \
        f"Shopping/digital/apps missing from taxonomy: {taxonomy.get('Shopping')}"

    # APPLE.COM rows no longer in Missing.
    remaining = get_unclassified_transactions(limit=1000)
    apple = [r for r in remaining if "APPLE.COM" in r["memo"]]
    assert apple == []


def test_apply_taxonomy_extension_rejects_existing_tuple(tmp_db):
    with pytest.raises(ValueError, match="already exists"):
        apply_taxonomy_extension(
            category_main="Leisure", category_sub="subscription",
            category_sub2="video", pattern=".*APPLE\\.COM",
        )


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
