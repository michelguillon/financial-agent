-- Personal Finance Agent — SQLite schema
-- Matches SPEC_AGENT.md §4. Re-runnable: every CREATE uses IF NOT EXISTS.

-- ---------------------------------------------------------------------------
-- transactions: the primary store.
--   amount is signed: negative = outgoing, positive = incoming.
--   data_source distinguishes real bank exports from the committed
--   synthetic dataset so both can coexist in one DB during development.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             DATE NOT NULL,
    account_number   TEXT,
    amount           REAL NOT NULL,
    type             TEXT,
    memo             TEXT,
    account_currency TEXT DEFAULT '£',
    account_type     TEXT,
    account_name     TEXT,
    category_main    TEXT,
    category_sub     TEXT,
    category_sub2    TEXT,
    details          TEXT,
    data_source      TEXT DEFAULT 'real'
);

CREATE INDEX IF NOT EXISTS idx_transactions_date
    ON transactions(date);

CREATE INDEX IF NOT EXISTS idx_transactions_category
    ON transactions(category_main, category_sub);

CREATE INDEX IF NOT EXISTS idx_transactions_source
    ON transactions(data_source);

-- Partial index — speeds up the "give me the backlog" query the
-- classification agent runs every session.
CREATE INDEX IF NOT EXISTS idx_transactions_missing
    ON transactions(category_main)
    WHERE category_main = 'Missing';


-- ---------------------------------------------------------------------------
-- classification_rules: regex rules the classifier uses to bucket
-- transactions into the taxonomy. Phase 2 (A1) migrated the previously
-- hardcoded chain in bank_statement_parser.py into rows here, so the
-- table is the authoritative source of truth.
--
-- Most rules condition only on Memo (pattern REGEXP). The four optional
-- columns below let a rule additionally require an exact Account Number,
-- exact Type, or an Amount within [amount_min, amount_max] (absolute
-- value — see classifier/rule_lookup.py). NULL = no condition.
--
-- `added_by`: 'seed' for rows inserted by db/seed_rules.py from
-- classifier/rules_seed.py; 'agent' for rows the agent adds at runtime
-- via apply_classification_rule. The split lets the seed step delete +
-- re-insert its own rows without touching agent-added ones.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS classification_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern        TEXT NOT NULL,
    category_main  TEXT NOT NULL,
    category_sub   TEXT,
    category_sub2  TEXT,
    details        TEXT,
    account_match  TEXT,
    type_match     TEXT,
    amount_min     REAL,
    amount_max     REAL,
    added_by       TEXT DEFAULT 'agent',
    approved_by    TEXT,
    approved_at    DATETIME,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    times_matched  INTEGER DEFAULT 0
);


-- ---------------------------------------------------------------------------
-- agent_state: cross-session knowledge store. The agent reads relevant
-- entries at the start of each session (injected into the system prompt)
-- and writes new entries via the set_agent_state tool when it learns a
-- durable fact worth keeping (SPEC §3.1, §5.3).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_state (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    value_type   TEXT NOT NULL,
    rationale    TEXT,
    confidence   TEXT DEFAULT 'inferred',
    session_id   TEXT,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ---------------------------------------------------------------------------
-- pending_batches: cross-session record of Anthropic Batch API jobs (C2).
-- bulk_classify_async inserts one row per submitted batch with
-- status='in_progress'; check_batch_results flips it to 'completed' (and
-- persists result_json + cost_usd) or 'failed' (with error_detail) when
-- the batch ends.
--
-- The agent loop's build_system_prompt reads this on each turn so a future
-- session can announce "you have N pending batches from earlier".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_batches (
    batch_id         TEXT PRIMARY KEY,
    submitted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at     DATETIME,
    status           TEXT NOT NULL DEFAULT 'in_progress',
                         -- 'in_progress' | 'completed' | 'failed' | 'expired'
    memos_count      INTEGER NOT NULL,
    transaction_ids  TEXT NOT NULL,        -- JSON list of ints
    result_json      TEXT,                 -- JSON list of suggestion dicts; NULL until completed
    cost_usd         REAL,                 -- realised cost (Haiku × BATCH_DISCOUNT); NULL until completed
    error_detail     TEXT,                 -- non-null when status='failed'
    data_source      TEXT NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX IF NOT EXISTS idx_pending_batches_status
    ON pending_batches(status);
