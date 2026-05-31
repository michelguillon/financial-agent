"""Tests for db.migrate — CSV → SQLite ingestion."""
from __future__ import annotations

from pathlib import Path

from db.database import open_db
from db.migrate import detect_format, ingest, parse_date

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "transactions_synthetic.csv"


def test_synthetic_csv_round_trips_full_dataset(tmp_path):
    db_path = tmp_path / "fresh.db"
    with open_db(db_path) as conn:
        inserted = ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=False)
    assert inserted > 18_000, f"expected ~18,780 rows, got {inserted}"

    with open_db(db_path, init=False) as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE data_source='synthetic'"
        ).fetchone()
    assert n == inserted


def test_replace_flag_clears_existing(tmp_path):
    db_path = tmp_path / "fresh.db"
    with open_db(db_path) as conn:
        ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=False)
        n_first = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        # Re-ingest with replace=True should land at the same row count, not double.
        ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=True)
        n_second = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
    assert n_first == n_second


def test_detect_format_synthetic():
    assert detect_format(["date", "category_main", "data_source"]) == "synthetic"


def test_detect_format_preprocessed():
    assert detect_format(["Date", "Category - Main"]) == "preprocessed"


def test_parse_date_accepts_iso_and_uk():
    assert parse_date("2025-12-31").isoformat() == "2025-12-31"
    assert parse_date("31/12/2025").isoformat() == "2025-12-31"
    assert parse_date("31/12/25").isoformat() == "2025-12-31"
