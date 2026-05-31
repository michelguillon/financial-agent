"""Exhaustive round-trip verifier for A1's rules-in-table migration.

For every row in the committed synthetic CSV, run the memo + conditions
through classifier.rule_lookup.categories() and assert agreement with the
CSV's pre-assigned (category_main, category_sub, category_sub2, details).

Pre-assigned `Missing` rows MUST come back as `Missing` — the noise four
(NETFLIX, AIRBNB, TRAINLINE, DISNEY+) stay noise.

If this test fails after a rule edit, the failure assertion lists the
first ~20 (memo → expected vs actual) mismatches so the missing/wrong
rule is obvious.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from classifier.rule_lookup import categories, reset_connection

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "transactions_synthetic.csv"


def _norm(v):
    """Treat empty-string CSV cells as None for comparison."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


@pytest.fixture
def _conn_to_tmp_db(tmp_db):
    """Force rule_lookup's module-level connection onto the per-test tmp_db.

    rule_lookup caches a connection in _conn at first use. The conftest
    monkeypatches db.database.DB_PATH but rule_lookup's cached conn — if
    any — points at the old path. Reset before each test.
    """
    reset_connection()
    yield
    reset_connection()


def test_synthetic_csv_round_trips_with_table_classifier(tmp_db, _conn_to_tmp_db):
    rows = list(csv.DictReader(SYNTHETIC_CSV.open(encoding="utf-8")))
    assert len(rows) > 18_000, f"expected ~18,780 rows, got {len(rows)}"

    mismatches: list[tuple[str, tuple, tuple]] = []
    for r in rows:
        # rule_lookup.categories takes a pd.Series with Title-Case keys
        # (matches bank_statement_parser's input convention).
        series = pd.Series({
            "Memo": r["memo"],
            "Account Number": r["account_number"],
            "Type": r["type"],
            "Amount": float(r["amount"]),
        })
        got = tuple(categories(series))
        expected = (
            _norm(r["category_main"]),
            _norm(r["category_sub"]),
            _norm(r["category_sub2"]),
            _norm(r["details"]),
        )
        # Normalise None vs "" in `got` too — pd.Series may carry NaN.
        got_norm = tuple(_norm(v) if not (isinstance(v, float) and v != v) else None
                         for v in got)
        if got_norm != expected:
            mismatches.append((r["memo"], expected, got_norm))

    if mismatches:
        sample = "\n".join(
            f"  {memo!r}\n      expected {exp}\n      got      {got}"
            for memo, exp, got in mismatches[:20]
        )
        pytest.fail(
            f"{len(mismatches)} / {len(rows)} rows mismatched. First 20:\n{sample}"
        )


def test_noise_four_still_classify_as_missing(tmp_db, _conn_to_tmp_db):
    """Regression guard: NETFLIX/AIRBNB/TRAINLINE/DISNEY+ stay Missing
    even after A2 adds the new taxonomy."""
    for memo_prefix in ("NETFLIX.COM", "AIRBNB UK", "TRAINLINE.COM",
                        "DISNEY+ SUBSCRIPTION"):
        series = pd.Series({
            "Memo": f"{memo_prefix} 1234",
            "Account Number": "ACCOUNT_CC",
            "Type": "PURCHASE",
            "Amount": -14.14,
        })
        result = tuple(categories(series))
        assert result[0] == "Missing", \
            f"{memo_prefix} should be Missing, got {result}"
