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

### 3.3 — Model: Claude (claude-sonnet-4-20250514)

Chosen for tool use quality and the portfolio context. The `mistral_helpers.py` pattern from the RAG project carries forward as `claude_helpers.py` — same `call_with_retry()` pattern, different API shape.

Tool use API shape (Anthropic-specific):
```python
# Agent requests a tool:
{"type": "tool_use", "name": "get_spending_summary", "input": {"months": 6}}

# We inject the result:
{"type": "tool_result", "tool_use_id": "...", "content": "..."}
```

### 3.4 — Classification engine migration: Two-phase

**Phase 1 (this build):** SQLite `classification_rules` table is checked FIRST. If a rule matches, return its categories. If not, fall through to the existing hardcoded `if/elif` chain in `bank_statement_parser.py`. The hardcoded chain is preserved unchanged as the fallback.

```python
def categories(df):
    # Phase 1 addition: check SQLite rules first
    db_result = lookup_in_rules_table(df['Memo'])
    if db_result:
        return db_result
    
    # Original chain unchanged below:
    if (df['Account Number'] == '20-71-74 20451770') & (df['Type'] == 'CASH'):
        return pd.Series(['Withdrawal', None, None, None])
    # ... (all existing rules unchanged)
    else:
        return pd.Series(['Missing', None, None, None])
```

**Phase 2 (future):** Migrate the hardcoded chain into the rules table. The Python function becomes a thin wrapper around a SQL query. All rules become inspectable, editable, and exportable. The hardcoded chain is retired.

Phase 2 is documented here as the intended end state but is explicitly out of scope for this build.

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
  real/          ← gitignored, .gitignore entry
    *.csv        ← original bank exports
  synthetic/     ← committed
    transactions_synthetic.csv
    generate_synthetic.py
```

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

-- Rules added by the agent (checked before hardcoded chain)
CREATE TABLE classification_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern       TEXT NOT NULL,             -- regex against Memo field
    category_main TEXT NOT NULL,
    category_sub  TEXT,
    category_sub2 TEXT,
    details       TEXT,
    added_by      TEXT DEFAULT 'agent',      -- 'agent' | 'manual'
    approved_by   TEXT,                      -- 'human' (set on approval)
    approved_at   DATETIME,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    times_matched INTEGER DEFAULT 0          -- updated when rule matches a transaction
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

**Category taxonomy** (from existing `bank_statement_parser.py`):

| Main | Sub examples | Sub2 examples |
|------|-------------|---------------|
| Income | Salary | — |
| House | Mortgage, Maintenance | kitchen/bathroom |
| Shopping | Groceries, Household, Clothes, CreditCard, electronics | Supermarket, Corner Shop, veg box, wine, DIY |
| Transport | Automotive, taxi, tube | Petrol, Road tax, parking & fees |
| Leisure | food/drinks, sport, subscription, entertainment | restaurant, pub, café, gym, amazon, music, newspapers |
| Bills | utilities, Bank Fees, Charity, Household, loan | water, gas/elec, council tax, Mobile Phone, TV License, broadband, cleaner |
| Health | Dentist, Eyecare, General, GP | glasses, Medicine |
| Savings | Transfer, Interest | — |
| Withdrawal | — | — |
| Missing | — | — |

> **Note on `Bills/utilities/Mobile Phone`:** historically the classifier had a separate `phone` sub2 for landline/Skype era transactions. That sub2 has been merged into `Mobile Phone` since 2026 — the SKYPE memo pattern in the classifier now also returns `Mobile Phone`. When the classifier is copied into `classifier/` per §9, update that rule accordingly.

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
add_classification_rule(
    pattern: str,
    category_main: str,
    category_sub: str | None,
    category_sub2: str | None,
    details: str | None
) -> dict
```
Writes the rule to `classification_rules` with `approved_by = 'human'` and `approved_at = now()`. Then re-runs the rule against all `Missing` transactions and updates matches. Returns: `{rules_added: 1, transactions_reclassified: N}`.

Human approval is enforced in the agent loop, not in this function — the agent presents the suggestion, the human says yes/no, then (and only then) the agent calls `add_classification_rule`.

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
     b. For add_classification_rule: verify human approval was given in conversation
     c. Append tool_results to messages
     d. Go to step 2
  4. Return assistant text response to user
  5. Append assistant response to messages array
```

**Human-in-the-loop enforcement for rule addition:**
The agent cannot call `add_classification_rule` speculatively. The conversation flow is:
1. Agent calls `suggest_classification` → shows result to user
2. User explicitly approves ("yes", "add it", "looks right")
3. Only then does the agent call `add_classification_rule`

This is enforced by prompt instruction, not code gate (Phase 1). A code gate (checking conversation history for approval signal before executing the tool) is a Phase 2 hardening option.

---

## 7. System Components

```
personal-finance-agent/
├── agent/
│   ├── agent.py              ← main agent loop
│   ├── tools/
│   │   ├── classification.py ← get_unclassified, suggest, add_rule, list_categories
│   │   ├── scenarios.py      ← spending_summary, income, fixed_vs_disc, model_scenario
│   │   └── state.py          ← get/set agent_state
│   └── tool_registry.py      ← JSON schema definitions for all tools
├── db/
│   ├── schema.sql            ← CREATE TABLE statements above
│   ├── database.py           ← SQLite connection + helpers
│   └── migrate.py            ← one-time ingestion from CSV exports
├── classifier/
│   ├── bank_statement_parser.py   ← original script, preserved
│   └── rule_lookup.py             ← Phase 1 addition: SQLite lookup wrapper
├── data/
│   ├── real/                 ← gitignored
│   └── synthetic/
│       ├── generate_synthetic.py
│       └── transactions_synthetic.csv
├── claude_helpers.py         ← adapted from mistral_helpers.py
├── .env                      ← ANTHROPIC_API_KEY, BUDGET_DATA_DIR
└── finance.db                ← gitignored
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

**Step 6 — UI (optional for Week 2)**
CLI is sufficient for the portfolio demo. React + FastAPI upgrade follows the RFI project pattern and can be added post-Week 2.

---

## 9. Privacy and Access Pattern

```
.gitignore entries (mandatory):
  data/real/
  finance.db
  finance_real.db
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
