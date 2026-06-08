"""Shared fixtures for the pytest suite.

Fixture strategy (per [docs/AGENT_LEARNINGS.md → Testing strategy]):
- `seed_db` (session): build once from the synthetic CSV (~18k rows, ~2s).
- `tmp_db` (function): shutil.copy the seed into tmp_path and monkey-patch
  `db.database.DB_PATH` so tools that open the DB with no arg see the copy.

Read-only tests can take `seed_db` directly. Write-touching tests take
`tmp_db` so each test gets a fresh writable DB without paying the ingest
cost per test (~30ms copy vs ~2s ingest).

LLM-marked tests auto-skip unless RUN_LLM_TESTS=1 — same env-var convention
the inline __main__ smoke tests used before the pytest cutover.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import database  # noqa: E402
from db.migrate import ingest  # noqa: E402
from db.seed_rules import seed as seed_rules  # noqa: E402

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "transactions_synthetic.csv"


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seed") / "seed.db"
    with database.open_db(path) as conn:
        ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=False)
        seed_rules(conn)
    return path


@pytest.fixture
def tmp_db(seed_db, tmp_path, monkeypatch) -> Path:
    db_copy = tmp_path / "finance.db"
    shutil.copy(seed_db, db_copy)
    monkeypatch.setattr("db.database.DB_PATH", db_copy)
    return db_copy


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LLM_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="LLM test; set RUN_LLM_TESTS=1 to run")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
