"""seed_rules.py — load classifier/rules_seed.RULES_SEED into the DB.

Called by db/migrate.py after ingest so a fresh DB always has the canonical
rule set loaded. Idempotent: deletes all rows where `added_by='seed'`
first, then re-inserts in RULES_SEED order. Agent-added rows
(added_by='agent') are left alone.

Standalone usage:
    docker compose run --rm agent python -m db.seed_rules
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from classifier.rules_seed import RULES_SEED  # noqa: E402

_INSERT_SQL = """
    INSERT INTO classification_rules
        (pattern, category_main, category_sub, category_sub2, details,
         account_match, type_match, amount_min, amount_max,
         added_by, approved_by, approved_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'seed', 'human', CURRENT_TIMESTAMP)
"""


def seed(conn: sqlite3.Connection) -> int:
    """Replace all `added_by='seed'` rows with the current RULES_SEED.

    Returns the number of rows inserted.
    """
    conn.execute("DELETE FROM classification_rules WHERE added_by = 'seed'")
    rows = [
        (r["pattern"], r["category_main"], r.get("category_sub"),
         r.get("category_sub2"), r.get("details"),
         r.get("account_match"), r.get("type_match"),
         r.get("amount_min"), r.get("amount_max"))
        for r in RULES_SEED
    ]
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    from db.database import open_db
    with open_db() as conn:
        n = seed(conn)
        print(f"Seeded {n} rules into classification_rules (added_by='seed').")
