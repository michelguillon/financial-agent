"""Tests for classifier.budget_importer — the legacy raw-CSV ingestion pipeline.

C1 (2026-06-02) wired the Budget class to use `data/real/raw/` +
`data/real/preprocessed/` and re-imported the missing `categories`
function from `classifier.rule_lookup`. These tests cover the simpler
import paths (current_account + amex) end-to-end against fixture CSVs.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from classifier import rule_lookup
from classifier.budget_importer import Budget, run_pipeline


# ---------------------------------------------------------------------------
# Fixture writers — each builds the minimal raw CSV the matching importer
# expects. Schemas mirror the per-importer headers in budget_importer.py.
# ---------------------------------------------------------------------------

def _write_current_account_csv(path: Path) -> None:
    """Schema: Number,Date,Account,Amount,Subcategory,Memo (header=0).

    Account names match the redaction placeholders so set_up_account_*
    helpers route the rows into the Current Account / Credit Card buckets.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            # PRET under £5 hits the conditional café rule (Leisure/food/drinks/café).
            {"Number": 1, "Date": "2026-06-01", "Account": "ACCOUNT_CURRENT",
             "Amount": -3.50, "Subcategory": "PAYMENT", "Memo": "PRET A MANGER"},
            # STARBUCKS hits the café rule unconditionally.
            {"Number": 2, "Date": "2026-06-01", "Account": "ACCOUNT_CURRENT",
             "Amount": -4.20, "Subcategory": "PAYMENT", "Memo": "STARBUCKS COFFEE"},
            # NETFLIX is deliberately in NOISE_MEMOS — stays Missing.
            {"Number": 3, "Date": "2026-06-02", "Account": "ACCOUNT_CURRENT",
             "Amount": -10.99, "Subcategory": "PAYMENT", "Memo": "NETFLIX.COM"},
        ]
    )
    df.to_csv(path, index=False)


def _write_amex_csv(path: Path) -> None:
    """Schema: Date,Memo,Amount (Amex export — sign is inverted by import_amex)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            # BOOKING.COM was added in A2 → Travel/accommodation/hotel.
            {"Date": "2026-06-03", "Memo": "BOOKING.COM HOTELS",
             "Amount": 120.00},  # positive in raw, negated by import_amex
        ]
    )
    df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Fixtures — wire BUDGET_DATA_DIR to a tmp dir + reseed rule_lookup against
# the tmp_db DB so categories() reads from the (per-test) finance.db.
# ---------------------------------------------------------------------------

@pytest.fixture
def budget_root(tmp_path, monkeypatch, tmp_db) -> Path:
    """Empty raw/preprocessed layout under a tmp BUDGET_DATA_DIR.

    `tmp_db` (from conftest) monkeypatches db.database.DB_PATH, so
    `categories()` queries the seeded per-test DB.
    """
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path))
    # Drop any stale rule_lookup connection — module-level globals persist
    # across tests, so a previous test could leave _conn pointing at an old
    # tmp DB. reset_connection() forces a fresh open against tmp_db.
    rule_lookup.reset_connection()
    yield tmp_path
    rule_lookup.reset_connection()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_init_creates_raw_and_preprocessed_subdirs(budget_root):
    b = Budget("2026_06_01")
    assert (budget_root / "raw").is_dir()
    assert (budget_root / "preprocessed").is_dir()
    # File paths anchored under the new layout, not the legacy tmp_data dir.
    assert Path(b.file_amex) == budget_root / "raw" / "2026_06_01_amex.csv"
    assert Path(b.file_preprocessed) == (
        budget_root / "preprocessed" / "2026_06_01_accounts_preprocessed.csv"
    )


def test_init_honours_explicit_budget_root(tmp_path, monkeypatch, tmp_db):
    # BUDGET_DATA_DIR is set, but the explicit arg should win.
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "fromenv"))
    rule_lookup.reset_connection()
    explicit = tmp_path / "fromarg"
    b = Budget("2026_06_01", budget_root=explicit)
    assert Path(b.path_to_raw).parent == explicit
    assert Path(b.path_to_preprocessed).parent == explicit


def test_run_pipeline_returns_preprocessed_csv_path(budget_root):
    _write_current_account_csv(budget_root / "raw" / "2026_06_01_accounts_download.csv")
    out = run_pipeline("2026_06_01")
    assert out.exists()
    assert out.name == "2026_06_01_accounts_preprocessed.csv"


def test_preprocessed_csv_classifies_known_rule_hits(budget_root):
    _write_current_account_csv(budget_root / "raw" / "2026_06_01_accounts_download.csv")
    out = run_pipeline("2026_06_01")
    df = pd.read_csv(out)

    # Schema check — Title-Case columns the preprocessed migrate path expects.
    for col in ["Date", "Account Number", "Amount", "Type", "Memo",
                "Account Currency", "Account Type", "Account Name",
                "Category - Main", "Category - Sub", "Category - Sub2", "Details"]:
        assert col in df.columns, f"missing column {col}"

    # Memo → category assertions.
    by_memo = {row["Memo"]: row for _, row in df.iterrows()}
    assert by_memo["PRET A MANGER"]["Category - Main"] == "Leisure"
    assert by_memo["PRET A MANGER"]["Category - Sub2"] == "café"
    assert by_memo["STARBUCKS COFFEE"]["Category - Sub2"] == "café"
    # NETFLIX is in NOISE_MEMOS by design — no seed rule should match.
    assert by_memo["NETFLIX.COM"]["Category - Main"] == "Missing"

    # Account derivations from set_up_account_type / set_up_account_name.
    assert by_memo["PRET A MANGER"]["Account Type"] == "Current Account"
    assert by_memo["PRET A MANGER"]["Account Name"] == "Current"
    assert by_memo["PRET A MANGER"]["Account Currency"] == "£"


def test_amex_path_classifies_and_inverts_sign(budget_root):
    _write_amex_csv(budget_root / "raw" / "2026_06_01_amex.csv")
    out = run_pipeline("2026_06_01")
    df = pd.read_csv(out)
    booking = df[df["Memo"] == "BOOKING.COM HOTELS"].iloc[0]
    # import_amex flips sign: 120 in raw → -120 in preprocessed.
    assert booking["Amount"] == -120.00
    # A2 added Travel/accommodation/hotel for BOOKING.COM.
    assert booking["Category - Main"] == "Travel"
    assert booking["Account Type"] == "Credit Card"


def test_empty_raw_directory_yields_empty_preprocessed(budget_root):
    """No input files → run_pipeline emits a header-only CSV without crashing."""
    out = run_pipeline("2026_06_01")
    df = pd.read_csv(out)
    assert len(df) == 0
