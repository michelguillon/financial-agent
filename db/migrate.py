"""migrate.py — CSV → SQLite ingestion for the personal finance agent.

Accepts already-categorised CSVs in either of two formats:

  1. Synthetic (lowercase, has data_source col)
     date,account_number,amount,type,memo,account_currency,account_type,
     account_name,category_main,category_sub,category_sub2,details,data_source

  2. Real preprocessed (Title Case, from classifier/budget_importer.py output)
     Date,Account Number,Amount,Type,Memo,Account Currency,Account Type,
     Account Name,Category - Main,Category - Sub,Category - Sub2,Details

Format is detected from the header row. Real-preprocessed rows get
data_source='real' by default; that's overridable with --source.

Dates: ISO (YYYY-MM-DD) and UK (DD/MM/YYYY) both accepted; rows fall
through several format guesses before erroring.

Usage:
    python db/migrate.py                           # synthetic CSV, append
    python db/migrate.py --csv data/synthetic/...  # explicit CSV
    python db/migrate.py --replace                 # clear data_source first
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Allow `python db/migrate.py` to find sibling `database` module.
sys.path.insert(0, str(Path(__file__).parent))
from database import DB_PATH, PROJECT_ROOT, open_db  # noqa: E402

DEFAULT_CSV = PROJECT_ROOT / "data" / "synthetic" / "transactions_synthetic.csv"

# CSV header → transactions column name, for the real preprocessed format.
PREPROCESSED_HEADER_MAP = {
    "Date": "date",
    "Account Number": "account_number",
    "Amount": "amount",
    "Type": "type",
    "Memo": "memo",
    "Account Currency": "account_currency",
    "Account Type": "account_type",
    "Account Name": "account_name",
    "Category - Main": "category_main",
    "Category - Sub": "category_sub",
    "Category - Sub2": "category_sub2",
    "Details": "details",
}

DB_COLUMNS = [
    "date", "account_number", "amount", "type", "memo",
    "account_currency", "account_type", "account_name",
    "category_main", "category_sub", "category_sub2", "details",
    "data_source",
]


# ---------------------------------------------------------------------------
# Format detection & row normalisation
# ---------------------------------------------------------------------------

def detect_format(header: list[str]) -> str:
    if "data_source" in header and "category_main" in header:
        return "synthetic"
    if "Category - Main" in header and "Date" in header:
        return "preprocessed"
    raise ValueError(
        f"Unrecognised CSV header. Got: {header}\n"
        "Expected either the synthetic lowercase schema or the "
        "budget_importer preprocessed Title-Case schema."
    )


def parse_date(s: str) -> date:
    s = s.strip()
    # Try in order: ISO, UK with 4-digit year, UK with 2-digit year.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {s!r}")


def normalise_value(value: str) -> str | None:
    """Empty strings become NULL; everything else preserved as-is."""
    if value is None:
        return None
    v = value.strip()
    return v if v != "" else None


def row_from_synthetic(raw: dict, default_source: str) -> tuple:
    return (
        parse_date(raw["date"]),
        normalise_value(raw["account_number"]),
        float(raw["amount"]),
        normalise_value(raw["type"]),
        normalise_value(raw["memo"]),
        normalise_value(raw.get("account_currency", "")) or "£",
        normalise_value(raw["account_type"]),
        normalise_value(raw["account_name"]),
        normalise_value(raw["category_main"]),
        normalise_value(raw["category_sub"]),
        normalise_value(raw["category_sub2"]),
        normalise_value(raw["details"]),
        normalise_value(raw.get("data_source", "")) or default_source,
    )


def row_from_preprocessed(raw: dict, default_source: str) -> tuple:
    g = lambda src: normalise_value(raw.get(src, ""))
    return (
        parse_date(raw["Date"]),
        g("Account Number"),
        float(raw["Amount"]),
        g("Type"),
        g("Memo"),
        g("Account Currency") or "£",
        g("Account Type"),
        g("Account Name"),
        g("Category - Main"),
        g("Category - Sub"),
        g("Category - Sub2"),
        g("Details"),
        default_source,
    )


# ---------------------------------------------------------------------------
# Migration driver
# ---------------------------------------------------------------------------

def ingest(csv_path: Path, conn: sqlite3.Connection, source_default: str,
           replace: bool) -> int:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        fmt = detect_format(header)
        builder = row_from_synthetic if fmt == "synthetic" else row_from_preprocessed
        print(f"Detected format: {fmt}")

        if replace:
            cur = conn.execute(
                "DELETE FROM transactions WHERE data_source = ?", (source_default,)
            )
            print(f"Cleared {cur.rowcount:,} existing rows with data_source={source_default!r}")

        placeholders = ",".join(["?"] * len(DB_COLUMNS))
        sql = f"INSERT INTO transactions ({','.join(DB_COLUMNS)}) VALUES ({placeholders})"

        batch: list[tuple] = []
        total = 0
        for line_no, raw in enumerate(reader, start=2):  # row 1 is header
            try:
                batch.append(builder(raw, source_default))
            except (KeyError, ValueError) as e:
                raise ValueError(f"Row {line_no}: {e}") from e
            if len(batch) >= 1000:
                conn.executemany(sql, batch)
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(sql, batch)
            total += len(batch)

    conn.commit()
    return total


def validate(conn: sqlite3.Connection, source: str) -> None:
    """Print the SPEC §8 Step 2 validation checks."""
    cur = conn.cursor()

    (n,) = cur.execute(
        "SELECT COUNT(*) FROM transactions WHERE data_source = ?", (source,)
    ).fetchone()
    print(f"\n--- Validation (data_source={source!r}) ---")
    print(f"Total rows: {n:,}")
    if n == 0:
        return

    (dmin, dmax) = cur.execute(
        "SELECT MIN(date), MAX(date) FROM transactions WHERE data_source = ?",
        (source,),
    ).fetchone()
    print(f"Date range: {dmin} .. {dmax}")

    print("By category_main:")
    rows = cur.execute(
        "SELECT category_main, COUNT(*) AS n FROM transactions "
        "WHERE data_source = ? GROUP BY category_main ORDER BY category_main",
        (source,),
    ).fetchall()
    for r in rows:
        marker = "  <- backlog" if r["category_main"] == "Missing" else ""
        print(f"  {r['category_main']:<12} {r['n']:>6,}{marker}")

    (missing_n,) = cur.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE data_source = ? AND category_main = 'Missing'",
        (source,),
    ).fetchone()
    print(f"Missing rows (agent backlog): {missing_n:,}")

    print("\nSample Missing rows (spot-check):")
    samples = cur.execute(
        "SELECT date, account_name, amount, memo FROM transactions "
        "WHERE data_source = ? AND category_main = 'Missing' "
        "ORDER BY RANDOM() LIMIT 5",
        (source,),
    ).fetchall()
    for r in samples:
        print(f"  {r['date']}  {r['account_name']:<12} {r['amount']:>9.2f}  {r['memo']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                   help=f"CSV to ingest (default: {DEFAULT_CSV.relative_to(PROJECT_ROOT)})")
    p.add_argument("--source", default=None,
                   help="Override data_source value (default: 'synthetic' for "
                        "synthetic CSVs, 'real' for preprocessed CSVs)")
    p.add_argument("--replace", action="store_true",
                   help="Delete existing rows with matching data_source before inserting")
    p.add_argument("--db", type=Path, default=DB_PATH,
                   help=f"DB path (default: {DB_PATH.relative_to(PROJECT_ROOT)})")
    args = p.parse_args(argv)

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}", file=sys.stderr)
        return 1

    # Pick the default source from the CSV format when --source wasn't given.
    if args.source is None:
        with args.csv.open("r", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        fmt = detect_format(header)
        args.source = "synthetic" if fmt == "synthetic" else "real"

    print(f"DB:     {args.db}")
    print(f"CSV:    {args.csv}")
    print(f"Source: {args.source!r}  (replace={args.replace})")

    with open_db(args.db) as conn:
        inserted = ingest(args.csv, conn, args.source, args.replace)
        print(f"\nInserted {inserted:,} rows.")

        # Always re-seed the canonical rules (deletes added_by='seed' rows
        # and re-inserts from classifier/rules_seed.py). Agent-added rules
        # (added_by='agent') are left alone.
        from db.seed_rules import seed as seed_rules
        n_rules = seed_rules(conn)
        print(f"Seeded {n_rules} classifier rules.")

        validate(conn, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
