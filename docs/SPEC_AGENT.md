# SPEC_AGENT.md — Personal Finance Agent
## Architecture Specification

**Project:** Week 2 Portfolio — AI Agents  
**Status:** Pre-implementation (architecture agreed, no code written)  
**Last updated:** pre-build  
**Deployment target:** M720q home server, Ubuntu Server 24.04, local network

---

## 1. Project Goals

Two goals, equal weight:

**Portfolio goal:** Demonstrate agent architecture patterns — human-in-the-loop approval, self-improving systems, stateful cross-session memory, tool use, scenario modelling — on real data that differentiates the project.

**Real-use goal:** Build something that continues to work after the interview. The classification engine should get smarter over time. The scenario agent should answer real financial questions against real data. The architecture decisions must support both goals, not just the demo.

---

## 2. What This System Does

**One agent, two tool groups.**

A single conversational agent with tools covering:

1. **Classification tools** — processes the backlog of unclassified (`Missing`) transactions, suggests regex rules, routes them through human approval, persists approved rules permanently.

2. **Scenario tools** — answers forward-looking financial questions grounded in real transaction history. Flagship use cases:
   - "I'm losing my job — where should I cut back?" → pulls spending by category, ranks discretionary vs fixed, gives specific £ targets
   - "My mortgage goes from 2% to 4% in March 2027 — how does that affect my budget?" → models new payment against real income/outgoings

The classification capability and the scenario capability are not separate agents. They share one agent loop, one state store, and one database. The connection: classification quality directly improves scenario accuracy.

---

## 3. Architecture Decisions

### 3.1 — What "stateful" means: Hybrid session + cross-session memory

**Decision:** Two distinct memory layers with separate implementations.

**Layer 1 — Session memory:** Standard messages array replay. Finance conversations are short (8–15 turns typical). Full replay costs almost nothing. Gives the agent continuity within a session ("you mentioned your mortgage is £1,200/month" can be referenced later in the same conversation). No extra infrastructure.

**Layer 2 — Cross-session knowledge:** SQLite `agent_state` table. Explicit facts the agent has learned about this user's finances, persisted across sessions. The agent reads relevant facts at the start of each session and writes new facts via a tool call when it learns something worth keeping.

Examples of what lives in the knowledge store:
- `avg_monthly_groceries_6m` → £412.50 (calculated, high confidence)
- `primary_income_source` → "salary" (inferred from transaction patterns)
- `mortgage_rate_change_date` → "2027-03-01" (user-confirmed)
- `fixed_cost_categories` → ["House/Mortgage", "Bills/utilities/water", ...] (inferred)

**What does NOT live in the knowledge store:** Conversational reasoning, intermediate calculations, things the user said that aren't durable facts. Those live and die in the messages array.

**The boundary rule:** If the agent would need to look it up again next session, store it. If it's reasoning about data the agent can always re-query, don't store it — just re-query.

### 3.2 — Orchestration: Raw API tool use, no framework

**Decision:** Raw Anthropic API tool use. No LangChain, no LangGraph.

The agent loop in plain terms:
1. Build context (system prompt + agent_state snapshot + messages array)
2. Call the API
3. If response contains `tool_use` blocks: execute the tools, inject results as `tool_result` blocks, go to step 2
4. If response is a text-only message: return it to the user
5. Go to step 1 on next user turn

All of this is explicit in the code. Loop termination, multi-tool handling, error injection — all visible and debuggable.

**Framework path:** LangGraph is the production successor once these fundamentals are understood. Not this project.

### 3.3 — Model routing: Sonnet 4.6 for the agent loop, Haiku 4.5 for classification

**Decision:** Two models, routed by task complexity. Not a single model for everything.

| Component | Model | Reason |
|-----------|-------|--------|
| Main agent loop (conversation, tool selection, scenario reasoning, recommendations) | `claude-sonnet-4-6` | Requires genuine reasoning over ambiguous financial questions and multi-step tool results |
| `suggest_classification()` internal call | `claude-haiku-4-5-20251001` | Short structured input (memo + amount), well-defined output (category + regex), no ambiguity — Haiku handles this class of task easily |

**What this means in practice:**
The agent loop always runs on Sonnet 4.6. One tool — `suggest_classification()` — makes its own internal API call using Haiku 4.5. The agent receives the classification suggestion as a tool result and never needs to know which model produced it. The routing is invisible to the agent loop.

**Why Haiku for classification specifically:**
Classification calls will be the most frequent in the system — processing a backlog of `Missing` transactions means many calls per session. The input is always short (a memo string, an amount, an account name). The output is always structured (4-level category + a regex pattern + a rationale). This is exactly the task profile where Haiku performs at the same quality as Sonnet at ~20x lower cost.

**What stays on Sonnet:**
- Main conversation and intent understanding
- Scenario interpretation (reading tool results, generating recommendations)
- `set_agent_state()` decisions (requires judgment about what's worth persisting)
- Human-in-the-loop approval presentation
- Anything involving ambiguous or open-ended financial reasoning

**What this teaches:**
Model routing by task complexity is a standard production pattern. The principle: use the most capable model only where capability is the constraint. For structured, well-defined subtasks, a smaller model is not a compromise — it's the correct choice.

**Note on Sonnet versions:** Sonnet 4.5 and 4.6 are priced identically ($3/$15 per MTok). Switching Sonnet versions does not reduce cost. The tiers are the pricing unit, not the version within a tier.

Tool use API shape (Anthropic-specific):
```python
# Agent requests a tool:
{"type": "tool_use", "name": "get_spending_summary", "input": {"months": 6}}

# We inject the result:
{"type": "tool_result", "tool_use_id": "...", "content": "..."}

# Model constants — define once, use everywhere
AGENT_MODEL = "claude-sonnet-4-6"               # main loop
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"  # suggest_classification() only
```

### Cost levers beyond model routing

Model selection is the first lever. Two more apply directly to this project:

**Prompt caching — 90% off repeated input**

Every turn in a conversation resends the system prompt and the agent_state snapshot. These are identical across turns within a session. Marking them as cacheable means the first turn pays full input price; every subsequent turn pays 10% on those tokens.

```python
# System prompt block with cache_control
{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"}
}

# Agent state snapshot — also cacheable if state hasn't changed this turn
{
    "type": "text",
    "text": format_agent_state(state),
    "cache_control": {"type": "ephemeral"}
}
```

For a 10-turn scenario conversation with a 2K token system prompt + state snapshot, caching reduces input cost on those tokens by ~90% across turns 2–10. At Sonnet 4.6 rates, a typical cached conversation costs ~$0.05–0.10 total.

**Batch API — 50% off async classification**

Processing a backlog of `Missing` transactions is not a real-time task. The user submits the batch and checks back. This is exactly the Batch API use case: 50% discount across all models, results returned asynchronously.

Haiku 4.5 + Batch API = $0.50/$2.50 per million tokens — effectively negligible for personal use volumes.

```python
# Batch classification: use for bulk Missing transaction processing
# Interactive classification (human reviewing one at a time): standard API

BATCH_THRESHOLD = 10  # if more than 10 Missing transactions, use Batch API
```

**Effective cost summary:**

| Task | Model | Mechanism | Rate |
|------|-------|-----------|------|
| Conversation loop | Sonnet 4.6 | Prompt caching on system + state | ~$0.05–0.10 / conversation |
| `suggest_classification()` interactive | Haiku 4.5 | Standard | ~$0.001 / call |
| Bulk `Missing` backlog (>10 transactions) | Haiku 4.5 | Batch API | ~$0.0005 / call |

### 3.4 — Classification engine migration: Two-phase ✓ Done (A1, 2026-05-31)

**Phase 1 (Steps 1–5):** SQLite `classification_rules` table checked FIRST; on miss, fall through to the hardcoded `if/elif` chain in `bank_statement_parser.py`. Both paths active.

**Phase 2 (A1):** The hardcoded chain has been migrated. [`classifier/rules_seed.py`](../classifier/rules_seed.py) holds the canonical list of ~45 rules; [`db/seed_rules.py`](../db/seed_rules.py) loads them via `db/migrate.py` after every ingest. The hardcoded `categories()` is deleted. `classifier/rule_lookup.categories()` is the only path — table hit returns the row, miss returns `Missing`.

```python
def categories(df):
    hit = lookup_in_rules_table(
        df.get("Memo"), df.get("Account Number"),
        df.get("Type"), df.get("Amount"),
    )
    if hit is not None:
        return pd.Series(list(hit))
    return pd.Series(["Missing", None, None, None])
```

**Conditional rules.** Three of the ported rules condition on more than memo (cash-from-current, MTG-from-current, PRET under £5). The `classification_rules` schema gained four optional columns to express these without exception code: `account_match`, `type_match`, `amount_min`, `amount_max`. Convention: `amount_min` is inclusive, `amount_max` is exclusive (mirrors `range(start, stop)`), comparison is against `abs(Amount)`.

**REGEXP semantics.** SQLite's REGEXP operator is backed by `re.match` (start-anchored), matching the original chain's semantics. Patterns with `.*` prefix opt into "match anywhere" explicitly; patterns without it match only at the start of the memo. `^` is redundant but harmless.

**Round-trip verifier.** [`tests/test_round_trip.py`](../tests/test_round_trip.py) runs every synthetic CSV row through `rule_lookup.categories()` and asserts agreement with the CSV's pre-assigned category. 100% green is the regression net for any future rule edit.

### 3.5 — Database: SQLite

Single file, zero infrastructure, built into Python, naturally tabular data. `finance.db` is always gitignored regardless of content.

### 3.6 — Demo mode

Data-layer switch, no auth system:

```python
def get_data_source() -> str:
    if Path("data/real/transactions.csv").exists():
        return "real"
    return "synthetic"
```

All tool implementations accept an optional `source` parameter that defaults to `get_data_source()`. The `data_source` column on the transactions table allows both datasets to coexist in the same database if needed.

```
data/
  finance.db     ← gitignored, SQLite store
  real/          ← gitignored
    *.csv        ← original bank exports
  synthetic/     ← committed
    transactions_synthetic.csv
    generate_synthetic.py
```

### 3.7 — Containerisation

**Decision:** Everything runs in a Docker container. Mirrors the sibling
`../banking` project's pattern: `python:3.13-slim`, non-root `agentuser`
(uid 1000), deps installed from `requirements.txt`, no fixed `ENTRYPOINT`
so any module can be invoked ad-hoc via `docker compose run`.

**Why containerisation matters for this project specifically:**
- Deployment target is Ubuntu Server 24.04 (M720q home server); development
  is on Windows. Docker eliminates the cross-platform drift that would
  otherwise plague this.
- The agent has external dependencies (Anthropic SDK, pandas). Containerising
  them keeps the host Python clean and pins versions for the deployment
  target.
- Persistent state (`finance.db`) lives on a bind-mounted directory, so
  rebuilds don't lose it.

**Volume mount contract:**
```yaml
volumes:
  - ./data:/app/data    # finance.db, synthetic CSV, real CSVs
  - ./logs:/app/logs    # conversation logs (Step 5)
```

Mounting the `data/` *directory* (not the `finance.db` file) avoids
Docker's "file mount on a missing path creates a directory" gotcha on
fresh checkouts. `finance.db` is created at `data/finance.db` on first
migration.

**Workflow:**
```bash
docker compose build
docker compose run --rm agent python db/migrate.py --replace
docker compose run --rm agent python -m agent.tool_registry
docker compose run --rm -it agent bash          # interactive shell
docker compose run --rm -it agent python -m agent.agent  # Step 5 REPL
```

Code changes require `docker compose build` to take effect (the container's
code is COPY-baked at build time; bind-mounting source would re-introduce
host/container Python-version drift).

---

## 4. Database Schema

```sql
-- Primary transaction store
CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             DATE NOT NULL,
    account_number   TEXT,
    amount           REAL NOT NULL,          -- negative = outgoing, positive = incoming
    type             TEXT,                   -- PAYMENT, CASH, DD, etc.
    memo             TEXT,                   -- primary classification field
    account_currency TEXT DEFAULT '£',
    account_type     TEXT,                   -- Current Account, Savings, Credit Card
    account_name     TEXT,                   -- Current, Pot of Gold, Barclaycard, Amex, Sainsbury
    category_main    TEXT,                   -- Income, House, Shopping, Transport, Leisure, Bills, Savings, Withdrawal, Missing
    category_sub     TEXT,
    category_sub2    TEXT,
    details          TEXT,                   -- 4th category level (NOT free-text notes)
    data_source      TEXT DEFAULT 'real'     -- 'real' | 'synthetic'
);

CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_category ON transactions(category_main, category_sub);
CREATE INDEX idx_transactions_source ON transactions(data_source);
CREATE INDEX idx_transactions_missing ON transactions(category_main) WHERE category_main = 'Missing';

-- All classification rules. After A1, this is the authoritative source —
-- the hardcoded chain in bank_statement_parser.py is gone.
CREATE TABLE classification_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern        TEXT NOT NULL,            -- regex against Memo (start-anchored via re.match)
    category_main  TEXT NOT NULL,
    category_sub   TEXT,
    category_sub2  TEXT,
    details        TEXT,
    -- Optional conditions (NULL = no constraint). See classifier/rule_lookup.py.
    account_match  TEXT,                     -- exact Account Number match
    type_match     TEXT,                     -- exact Type match (e.g. 'CASH')
    amount_min     REAL,                     -- abs(Amount) >= amount_min (inclusive)
    amount_max     REAL,                     -- abs(Amount) <  amount_max (exclusive)
    added_by       TEXT DEFAULT 'agent',     -- 'seed' (from rules_seed.py) | 'agent' | 'manual'
    approved_by    TEXT,
    approved_at    DATETIME,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    times_matched  INTEGER DEFAULT 0
);

-- Cross-session knowledge store
CREATE TABLE agent_state (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,              -- JSON-serialised
    value_type   TEXT NOT NULL,              -- 'float', 'str', 'list', 'dict'
    rationale    TEXT,                       -- why the agent stored this
    confidence   TEXT DEFAULT 'inferred',    -- 'inferred' | 'calculated' | 'user_confirmed'
    session_id   TEXT,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Category taxonomy** (post-A2; canonical source is `classifier/rules_seed.py`):

| Main | Sub examples | Sub2 examples |
|------|-------------|---------------|
| Income | Salary | — |
| House | Mortgage, Maintenance | kitchen/bathroom |
| Shopping | Groceries, Household, Clothes, CreditCard, electronics | Supermarket, Corner Shop, veg box, wine, DIY |
| Transport | Automotive, taxi, tube, **rail** | Petrol, Road tax, parking & fees |
| Leisure | food/drinks, sport, subscription, entertainment | restaurant, pub, café, gym, amazon, music, newspapers, **video** |
| Bills | utilities, Bank Fees, Charity, Household, loan | water, gas/elec, council tax, Mobile Phone, TV License, broadband, cleaner |
| **Travel** | **accommodation** | **hotel** |
| Savings | Transfer, Interest | — |
| Withdrawal | — | — |
| Missing | — | — |

**Bold** = added by A2. Deliberately *not* matched by seed rules: NETFLIX/DISNEY+ (would go to `Leisure/subscription/video`), AIRBNB (would go to `Travel/accommodation`), TRAINLINE (would go to `Transport/rail`). These stay in the `NOISE_MEMOS` pool so the synthetic dataset keeps a Missing backlog the agent can demo classification with.

---

## 5. Tool Definitions

All tools are Python functions registered with the Anthropic API as JSON schema tool definitions.

### 5.1 Classification Tools

```python
get_unclassified_transactions(limit: int = 20) -> list[dict]
```
Returns transactions where `category_main = 'Missing'`, ordered by date descending. Each result includes: `{id, date, amount, type, memo, account_name}`.

---

```python
suggest_classification(
    memo: str,
    amount: float,
    account_name: str
) -> dict
```
LLM-powered tool (internal API call). Given a transaction's fields, returns:
```json
{
  "category_main": "Leisure",
  "category_sub": "food/drinks",
  "category_sub2": "restaurant",
  "details": null,
  "suggested_pattern": ".*DISHOOM",
  "rationale": "Dishoom is a well-known Indian restaurant chain"
}
```
The pattern is a regex against Memo. Category names must match existing taxonomy.

---

```python
preview_rule_application(
    pattern: str,
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    details: str | None
) -> dict
```
Shows the blast radius of a candidate rule before it's committed. No DB writes. Returns:
```json
{
  "would_match": 24,
  "sample_matches": [{"id": ..., "date": ..., "amount": ..., "memo": ..., "account_name": ...}, ...],
  "proposed_classification": {"category_main": "Leisure", ...}
}
```

The agent uses this to show the user "this rule would reclassify N transactions; here are 5 of them" *before* asking for approval.

---

```python
apply_classification_rule(
    pattern: str,
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    details: str | None
) -> dict
```
Writes the rule to `classification_rules` with `approved_by = 'human'`, `approved_at = now()`, AND retroactively updates all matching `Missing` transactions — all in one SQL transaction so partial failure rolls back. Returns: `{rules_added: 1, rule_id: N, transactions_reclassified: M}`.

**Two-step flow rationale.** Rule application mutates the transactions table, so the agent must present `preview_rule_application` output to the user and receive explicit approval before calling `apply_classification_rule`. Splitting the read (preview) from the write (apply) gives the user a real chance to refuse a rule that would over-match, and it makes the conversation flow explicit:
1. Agent calls `suggest_classification` → drafts a rule
2. Agent calls `preview_rule_application` → shows would-match count + samples
3. User approves
4. Agent calls `apply_classification_rule`

The approval gate is enforced by prompt instruction (Phase 1); a Phase 2 code gate could check conversation history for an approval signal before executing `apply_classification_rule`.

---

```python
list_categories() -> dict
```
Returns the full category taxonomy derived from existing transactions. Used by the LLM when suggesting categories to ensure consistency with existing names.

```json
{
  "Income": ["Salary"],
  "Shopping": ["Groceries", "Household", "Clothes"],
  "Leisure": ["food/drinks", "sport", "subscription"],
  ...
}
```

---

```python
preview_taxonomy_extension(
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    pattern: str,
    details: str | None = None
) -> dict
```
Preview adding a NEW `(main, sub, sub2)` tuple to the taxonomy via a seed rule. Validates (a) the tuple is genuinely new (not in `list_categories()`) and (b) the pattern matches at least one Missing row. Raises `ValueError` on either failure. No DB writes. Returns `{is_new: True, proposed_taxonomy_entry, would_match, sample_matches}`.

```python
apply_taxonomy_extension(
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    pattern: str,
    details: str | None = None
) -> dict
```
Inserts the rule into `classification_rules` (`added_by='agent'`, `approved_by='human'`) and reclassifies matching Missing rows in one SQL transaction. Re-validates before mutating. Returns `{taxonomy_entry_added, rule_id, transactions_reclassified}`.

**Why this is a separate tool from `apply_classification_rule`.** Both write to `classification_rules`, but `apply_taxonomy_extension` validates that the proposed `(main, sub, sub2)` tuple is unprecedented. This makes "I am growing the taxonomy" an explicit agent action distinct from "I am adding a rule for an existing category". The agent calls `list_categories` first to decide which tool applies. A3 added these tools — see [LEARNINGS — A3](LEARNINGS.md#a3--extend_taxonomy-tool).

### 5.2 Scenario Tools

```python
get_spending_summary(
    months: int,
    category_main: str | None = None
) -> dict
```
Returns spending totals and monthly averages, optionally filtered to one top-level category. Excludes `Income`, `Savings`, `CreditCard` from totals by default (configurable).

```json
{
  "period": "2024-12 to 2025-05",
  "months": 6,
  "by_category": {
    "Shopping/Groceries": {"total": 2475.00, "monthly_avg": 412.50},
    "Leisure/food/drinks": {"total": 1890.00, "monthly_avg": 315.00}
  },
  "grand_total": 8420.00,
  "monthly_avg_total": 1403.33
}
```

---

```python
get_income_summary(months: int) -> dict
```
Analyses `Income` category transactions. Returns average monthly income, detected sources, and stability assessment.

```json
{
  "monthly_avg": 4200.00,
  "sources": [{"name": "UTIQ", "type": "Salary", "monthly_avg": 4200.00}],
  "stability": "stable",
  "months_analysed": 6
}
```

---

```python
classify_fixed_vs_discretionary(months: int) -> dict
```
Splits spending into fixed costs (same amount, same date each month: mortgage, utilities, council tax, mobile) vs discretionary (variable: groceries, restaurants, leisure). Returns both groups with totals.

---

```python
model_scenario(
    scenario: str,           # "job_loss" | "rate_change" | "expense_change"
    parameters: dict
) -> dict
```

`job_loss` parameters: `{"income_reduction_pct": 100}` or `{"new_monthly_income": 1500}`

`rate_change` parameters: `{"current_rate": 0.02, "new_rate": 0.04, "mortgage_balance": 185000, "effective_date": "2027-03-01"}`

Returns:
```json
{
  "current_monthly_surplus": 650.00,
  "new_monthly_surplus": -890.00,
  "gap": 1540.00,
  "recommendations": [
    {
      "category": "Leisure/food/drinks",
      "current_monthly": 315.00,
      "suggested_monthly": 150.00,
      "potential_saving": 165.00,
      "type": "discretionary"
    }
  ],
  "fixed_costs_unchanged": ["House/Mortgage", "Bills/utilities/water", ...]
}
```

### 5.3 State Tools

```python
get_agent_state(key: str) -> dict | None
```
Returns `{value, confidence, rationale, updated_at}` or `None` if not found.

---

```python
set_agent_state(
    key: str,
    value: any,
    rationale: str,
    confidence: str = "inferred"   # "inferred" | "calculated" | "user_confirmed"
) -> dict
```
Persists a fact to `agent_state`. The `rationale` parameter is required — the agent must explain why this is worth storing. Returns `{success: bool}`.

---

## 6. Agent Loop

```
System prompt:
  - Agent role and capabilities
  - Current date
  - Injected agent_state snapshot (key facts as structured context)
  - Data source (real | synthetic)

Per-turn:
  1. Append user message to messages array
  2. Call Claude API with tools registered
  3. If tool_use blocks present:
     a. Execute each tool
     b. For apply_classification_rule: verify human approval was given in conversation
     c. Append tool_results to messages
     d. Go to step 2
  4. Return assistant text response to user
  5. Append assistant response to messages array
```

**Human-in-the-loop enforcement for rule addition:**
The agent cannot call `apply_classification_rule` speculatively. The conversation flow is:
1. Agent calls `suggest_classification` → drafts a rule
2. Agent calls `preview_rule_application` → reports how many Missing rows would be reclassified and shows samples
3. User explicitly approves ("yes", "add it", "looks right")
4. Only then does the agent call `apply_classification_rule`

This is enforced by prompt instruction in Phase 1, and additionally by a dispatch-layer code gate from B1 onward. The gate (`agent/tool_registry.py:check_approval`) inspects conversation history for any `apply_*` tool listed in `GATED_TOOLS` (currently `apply_classification_rule` and `apply_taxonomy_extension`), finds the most recent matching `preview_*` call, and requires an approval signal in the user message that came after the preview's `tool_result`. Approval detection is hybrid: a regex fast-path over an approve/deny phrase list, falling back to a Haiku 4.5 classifier on ambiguous replies. Failures raise `ApprovalRequiredError`, which the loop's existing `try/except` converts into an `is_error` tool_result so the agent can self-correct.

---

## 7. System Components

```
personal-finance-agent/
├── agent/
│   ├── agent.py              ← main agent loop (Step 5)
│   ├── tools/
│   │   ├── classification.py ← get_unclassified, suggest, preview/apply, list_categories
│   │   ├── scenarios.py      ← spending_summary, income, fixed_vs_disc, model_scenario
│   │   └── state.py          ← get/set agent_state
│   └── tool_registry.py      ← schemas + dispatch
├── db/
│   ├── schema.sql            ← CREATE TABLE statements above
│   ├── database.py           ← SQLite connection + helpers
│   └── migrate.py            ← CSV ingestion (handles synthetic + preprocessed formats)
├── classifier/
│   ├── bank_statement_parser.py   ← original script, redacted per §9
│   └── rule_lookup.py             ← Phase 1 SQLite-first wrapper
├── data/
│   ├── finance.db            ← gitignored, SQLite store
│   ├── real/                 ← gitignored
│   └── synthetic/
│       ├── generate_synthetic.py
│       └── transactions_synthetic.csv
├── Dockerfile                ← python:3.13-slim, non-root agentuser (§3.7)
├── docker-compose.yml        ← dev convenience: mounts data/, logs/, optional .env
├── .dockerignore
├── requirements.txt          ← anthropic, python-dotenv, pandas
├── claude_helpers.py         ← adapted from mistral_helpers.py
├── .env                      ← gitignored; ANTHROPIC_API_KEY, optional RUN_LLM_TESTS
└── .env.example              ← committed template
```

---

## 8. Build Sequence

This is the order of implementation. Each step produces something testable before the next begins.

**Step 1 — Synthetic data generator**
Build `generate_synthetic.py`. Produces 15 years of realistic transactions using the known category taxonomy. Verify output looks right in SQLite before touching agent code. This is the safety net — if the real data migration breaks anything, the synthetic data still works.

**Step 2 — SQLite migration**
Build `migrate.py`. Loads existing CSV exports into `transactions` table. Runs the existing `categories()` function to populate category columns. Validates: spot-check categorisation quality, count `Missing` rows, verify date range.

**Step 3 — Rule lookup wrapper**
Build `rule_lookup.py`. Adds the SQLite-first lookup to `categories()`. Write one test rule manually, verify it takes precedence over the hardcoded chain. This is the foundation Agent 1 builds on.

**Step 4 — Tool implementations**
Build all tool functions. Test each independently with direct function calls before plugging into the agent loop. No agent loop yet.

**Step 5 — Agent loop**
Build `agent.py`. Wire tools to the loop. Test with synthetic data first, then real data.

**Step 6 — Web UI ✓ Done (C4, 2026-05-31)**

Shipped: React + Vite + Tailwind frontend served by a FastAPI backend; single Docker image (multi-stage Node→Python). SSE for streaming agent events (one event per Renderer callback). Three new concerns the CLI never had drove most of the work:

- **Per-session isolation** — each visitor gets a `shutil.copy` of the seed SQLite DB under `/tmp/agent-sessions/<id>/`. Threaded through the agent tools via the `SESSION_DB_PATH` ContextVar in [db/database.py](../db/database.py); propagates across `asyncio.to_thread` automatically (Python 3.10+).
- **Cost defense** — per-session $0.50 hard cap (enforced before each turn via [web/backend/limits.py](../web/backend/limits.py)); per-IP 3-sessions/24h rate limit. Anthropic API key stays server-side.
- **Streaming** — `WebSseRenderer` ([web/backend/streaming.py](../web/backend/streaming.py)) implements the existing Renderer protocol but pushes events into an `asyncio.Queue` via `loop.call_soon_threadsafe`, since `run_turn` is synchronous and runs in a worker thread.

Deployment shape: single image, served behind a tunnel (Cloudflare Tunnel or equivalent) that terminates HTTPS and sets `X-Forwarded-For`. See [LEARNINGS — C4](LEARNINGS.md#c4--web-ui) for the threading details and what got cut to ship.

---

## 9. Privacy and Access Pattern

```
.gitignore entries (mandatory):
  data/real/
  data/finance.db
  data/finance_real.db
  logs/
  .env
```

Demo URL for recruiters: loads synthetic data automatically (no real data present in repo). No auth, no user accounts, no expiry logic — same reasoning as RFI project.

Real data access: local network only. M720q home server. No remote access currently. SQLite file stays on the server, never in the repo.

### Redacting bank_statement_parser.py before first commit

`bank_statement_parser.py` is copied from the private repo into `classifier/`. Before committing it to the public repo, replace all personally identifying values with generic placeholders. The logic is preserved; the personal details are not.

**Account numbers** — replace with role-based names:
```python
# Before
if df["Account Number"] == "20-71-74 20451770":
# After
if df["Account Number"] == "ACCOUNT_CURRENT":
```

Apply consistently across `set_up_account_number()`, `set_up_account_type()`, `set_up_account_name()`, and any pattern matches in `categories()`.

**Employer names in Income regex** — replace with generic placeholders:
```python
# Before
elif bool(re.match('APPNEXUS|IMAGINATION TECH|UTIQ', df['Memo'])):
# After
elif bool(re.match('COMPANY_A|COMPANY_B|COMPANY_C', df['Memo'])):
```

**Cleaner / personal names in Bills regex** — replace with generic placeholders:
```python
# Before
elif bool(re.match(r'.*EMELYN\s*COMINTAN|.*CELY.*CASTILLO', df['Memo'])):
# After
elif bool(re.match(r'.*CLEANER_A|.*CLEANER_B', df['Memo'])):
```

**Credit card numbers in Shopping regex** — replace with masked placeholders:
```python
# Before
bool(re.match('.*MR MICHEL GUILLON     4929499678409008', df['Memo']))
# After
bool(re.match('.*CARDHOLDER_NAME_CARDNUMBER', df['Memo']))
```

**Loan reference numbers** — replace with generic label:
```python
# Before
bool(re.match('^BARCLAYS BANK UK      1021B6293224459', df['Memo']))
# After
bool(re.match('^LENDER_NAME_REFERENCE', df['Memo']))
```

**File paths and Windows username** — the `__init__` method contains a hardcoded Windows path with a username. Replace with the environment variable path only:
```python
# Before
budget_data_dir = os.environ.get('BUDGET_DATA_DIR',
                                 r'C:\Users\miche\OneDrive\Documents\Budget\General')
# After
budget_data_dir = os.environ.get('BUDGET_DATA_DIR', './data')
```

**Checklist before first commit:**
- [ ] No real account numbers (sort code + account number format `XX-XX-XX XXXXXXXX`)
- [ ] No employer names
- [ ] No personal names (cleaners, cardholders)
- [ ] No card numbers
- [ ] No loan reference numbers
- [ ] No hardcoded file paths with usernames
- [ ] Run `git diff --staged` and read it line by line before `git commit`

---

## 10. Out of Scope (this build)

- Authentication / user accounts
- Remote access to real data
- Phase 2 rule migration (hardcoded chain → SQLite)
- Multi-user support
- Push notifications / scheduled analysis
- React UI (CLI demo sufficient for Week 2)
