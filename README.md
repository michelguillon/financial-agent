# Personal Finance Agent

A conversational agent that classifies a backlog of unrecognised bank
transactions with human approval, and answers forward-looking financial
questions ("if my mortgage rate doubles, where should I cut back?") grounded
in real transaction history.

Built as a Week 2 AI Agents portfolio project. Designed to keep working
after the interview — the classifier gets smarter with each approved rule,
and the same agent loop powers both the maintenance task and the planning
conversations.

> **Status:** under construction. Step 1 (synthetic data generator) is
> complete; Steps 2–5 are tracked in [SPEC_AGENT.md §8](SPEC_AGENT.md#8-build-sequence).

---

## What's interesting about the architecture

The full spec is in [SPEC_AGENT.md](SPEC_AGENT.md). The highlights:

- **One agent, two tool groups.** Classification tools (suggest regex rules,
  process the `Missing` backlog with human approval) and scenario tools
  (spending summaries, fixed/discretionary split, scenario modelling) share
  one agent loop, one SQLite store, one conversation. The shared substrate
  is the point: better classifications → better scenario answers.
  ([§2](SPEC_AGENT.md#2-what-this-system-does))

- **Two memory layers, separate implementations.** A messages array for
  in-session continuity (finance conversations are short — full replay
  costs almost nothing) and a SQLite `agent_state` table for durable facts
  the agent has learned about the user's finances across sessions.
  ([§3.1](SPEC_AGENT.md#31--what-stateful-means-hybrid-session--cross-session-memory))

- **Raw Anthropic API tool use — no framework.** No LangChain, no
  LangGraph. The agent loop is ~30 lines of explicit Python: build context,
  call API, dispatch tool calls, inject results, repeat. Every termination
  condition and every error path is visible in the code.
  ([§3.2](SPEC_AGENT.md#32--orchestration-raw-api-tool-use-no-framework))

- **Two-phase classifier migration.** Phase 1 (this build): a SQLite rules
  table is checked first; if no rule matches, fall through to the existing
  hardcoded `if/elif` chain that already classifies real exports. Phase 2
  (future): the hardcoded chain gets migrated into the rules table itself.
  Phase 1 ships value without a rewrite.
  ([§3.4](SPEC_AGENT.md#34--classification-engine-migration-two-phase))

- **Demo-mode without auth.** A data-layer switch: if `data/real/` exists,
  use it; otherwise fall back to the synthetic dataset committed to the
  repo. Recruiters get a working demo immediately; real data stays on the
  home server. ([§3.6](SPEC_AGENT.md#36--demo-mode))

---

## What's built so far

| Step | Component | Status |
|------|-----------|--------|
| 1 | Synthetic data generator (15y of UK transactions) | ✅ |
| 2 | SQLite schema + CSV ingestion | ⏳ |
| 3 | SQLite-first rule lookup wrapper | ⏳ |
| 4 | Tool implementations (classification + scenario + state) | ⏳ |
| 5 | Agent loop | ⏳ |
| 6 | UI (optional — CLI demo first) | ⏳ |

Methodology notes and surprises from each step are logged in
[LEARNINGS.md](LEARNINGS.md). The aim is that the *how* of each step is
reusable, not just the *what*.

---

## Try it

The synthetic dataset is committed (no API key, no setup required):

```powershell
python data/synthetic/generate_synthetic.py
```

Outputs `data/synthetic/transactions_synthetic.csv` — 18,780 transactions
spanning 2011-01-01 → 2025-12-31 across 5 accounts (current, savings, three
credit cards). Deterministic via fixed seed, stdlib only.

Categories follow the taxonomy in [SPEC_AGENT.md §4](SPEC_AGENT.md#4-database-schema).
About 5% of the variable spend is intentionally tagged `Missing` (recognisable
real merchants the classifier doesn't yet know) — that's the agent's
classification backlog on first run.

---

## Privacy

Real bank data lives only on the author's local network — never in this
repo, never on a remote.

The classifier code (`bank_statement_parser.py`, copied from a private repo
later in the build) is redacted before commit per
[SPEC_AGENT.md §9](SPEC_AGENT.md#9-privacy-and-access-pattern): account
numbers, employer names, cleaner names, cardholder details, and loan
references all become generic placeholders. The synthetic generator uses
the same placeholders, so synthetic and real data flow through identical
classifier code paths.

`.gitignore` enforces the boundary: `data/real/`, `finance.db`, `.env`,
and `logs/` can never be staged accidentally.

---

## Layout

```
financial-agent/
├── SPEC_AGENT.md          architecture spec
├── LEARNINGS.md           methodology log, one entry per build step
├── README.md              this file
├── data/
│   ├── synthetic/         committed — generator + 15y of fake transactions
│   └── real/              gitignored — never enters the repo
├── agent/                 (Step 4–5) tools and agent loop
├── classifier/            (Step 3) rule lookup + redacted parser
└── db/                    (Step 2) schema, migration, helpers
```
