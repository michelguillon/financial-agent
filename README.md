# Personal Finance Agent

A conversational agent that classifies a backlog of unrecognised bank
transactions with human approval, and answers forward-looking financial
questions ("if my mortgage rate doubles, where should I cut back?") grounded
in real transaction history.

Built as a Week 2 AI Agents portfolio project. Designed to keep working
after the interview — the classifier gets smarter with each approved rule,
and the same agent loop powers both the maintenance task and the planning
conversations.

> **Status:** Steps 1–5 complete (synthetic data, SQLite, classifier
> wrapper, tools + Docker, agent loop). Step 6 (web UI) is optional —
> see [SPEC §8](docs/SPEC_AGENT.md#8-build-sequence).

---

## What's interesting about the architecture

The full spec is in [docs/SPEC_AGENT.md](docs/SPEC_AGENT.md). The highlights:

- **One agent, two tool groups.** Classification tools (suggest regex rules,
  process the `Missing` backlog with human approval) and scenario tools
  (spending summaries, fixed/discretionary split, scenario modelling) share
  one agent loop, one SQLite store, one conversation. The shared substrate
  is the point: better classifications → better scenario answers.
  ([SPEC §2](docs/SPEC_AGENT.md#2-what-this-system-does))

- **Two memory layers, separate implementations.** A messages array for
  in-session continuity (finance conversations are short — full replay
  costs almost nothing) and a SQLite `agent_state` table for durable facts
  the agent has learned about the user's finances across sessions.
  ([SPEC §3.1](docs/SPEC_AGENT.md#31--what-stateful-means-hybrid-session--cross-session-memory))

- **Raw Anthropic API tool use — no framework.** No LangChain, no
  LangGraph. The agent loop is ~30 lines of explicit Python: build context,
  call API, dispatch tool calls, inject results, repeat. Every termination
  condition and every error path is visible in the code.
  ([SPEC §3.2](docs/SPEC_AGENT.md#32--orchestration-raw-api-tool-use-no-framework))

- **Two-phase classifier migration.** Phase 1 (this build): a SQLite rules
  table is checked first; if no rule matches, fall through to the existing
  hardcoded `if/elif` chain that already classifies real exports. Phase 2
  (future): the hardcoded chain gets migrated into the rules table itself.
  Phase 1 ships value without a rewrite.
  ([SPEC §3.4](docs/SPEC_AGENT.md#34--classification-engine-migration-two-phase))

- **Demo-mode without auth.** A data-layer switch: if `data/real/` exists,
  use it; otherwise fall back to the synthetic dataset committed to the
  repo. Recruiters get a working demo immediately; real data stays on the
  home server. ([SPEC §3.6](docs/SPEC_AGENT.md#36--demo-mode))

---

## What's built so far

| Step | Component | Status |
|------|-----------|--------|
| 1 | Synthetic data generator (15y of UK transactions) | ✅ |
| 2 | SQLite schema + CSV ingestion | ✅ |
| 3 | SQLite-first rule lookup wrapper | ✅ |
| 4 | Tool implementations (11 tools) + Docker | ✅ |
| 5 | Agent loop (Sonnet 4.6 + Haiku 4.5 + prompt caching) | ✅ |
| 6 | UI (optional — CLI demo first) | ⏳ |

Methodology notes and surprises from each step are logged in
[docs/LEARNINGS.md](docs/LEARNINGS.md). The aim is that the *how* of each step is
reusable, not just the *what*.

---

## Try it

Everything runs in Docker — no local Python install needed beyond Docker
Desktop itself.

```powershell
# One-time
docker compose build
copy .env.example .env       # then fill in ANTHROPIC_API_KEY

# Regenerate the synthetic dataset (committed, but you can re-roll)
docker compose run --rm agent python data/synthetic/generate_synthetic.py

# Load it into SQLite (creates data/finance.db on the host via volume mount)
docker compose run --rm agent python db/migrate.py --replace

# Talk to the agent — interactive REPL
docker compose run --rm -it agent python -m agent
```

Sample conversation:
```
You: What did I spend on this year?
› get_spending_summary(months=12)
╭ get_spending_summary ──────────────────────────────────╮
│ grand_total: £64,287.16   monthly_avg: £5,357.26       │
╰────────────────────────────────────────────────────────╯
Agent: Over the last 12 months you've spent around £64,300 —
44% fixed costs, 56% discretionary. House/Mortgage is your
biggest single category at £17,040…
[in 911 · out 642 · cache_read 0 · $0.0143 · turn 1]

You: What if my mortgage rate goes from 2% to 4%?
› model_scenario(scenario='rate_change', parameters={...})
╭ model_scenario ────────────────────────────────────────╮
│ scenario: rate_change                                  │
│ current_surplus: £  265.79/mo                          │
│ new_surplus:     £  -42.54/mo                          │
│ gap:             £  308.33/mo                          │
╰────────────────────────────────────────────────────────╯
Agent: A 2% rate rise on £185k would cost you an extra
£308/month — enough to tip you into the red…
[in 2,279 · out 565 · cache_read 9,378 · $0.0350 · turn 2]
```

Verify each component independently:
```powershell
docker compose run --rm agent python -m agent.tools.state
docker compose run --rm agent python -m agent.tools.classification
docker compose run --rm agent python -m agent.tools.scenarios
docker compose run --rm agent python -m agent.tool_registry
docker compose run --rm agent python -m agent.transcript
docker compose run --rm agent python -m agent.agent   # deterministic, no API
```

The synthetic dataset is 18,780 transactions spanning 2011-01-01 →
2025-12-31 across 5 accounts (current, savings, three credit cards).
Deterministic, stdlib only.

Categories follow the taxonomy in [SPEC §4](docs/SPEC_AGENT.md#4-database-schema).
About 5% of the variable spend is intentionally tagged `Missing` (recognisable
real merchants the classifier doesn't yet know) — that's the agent's
classification backlog on first run.

---

## Privacy

Real bank data lives only on the author's local network — never in this
repo, never on a remote.

The classifier code (`bank_statement_parser.py`, copied from a private repo
later in the build) is redacted before commit per
[SPEC §9](docs/SPEC_AGENT.md#9-privacy-and-access-pattern): account
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
├── README.md                       GitHub landing page
├── CLAUDE.md                       project conventions for Claude Code
├── Dockerfile                      python:3.13-slim, non-root agentuser
├── docker-compose.yml              dev convenience: volume-mounts data/ and logs/
├── requirements.txt                anthropic, python-dotenv, pandas, rich
├── .env.example
├── docs/
│   ├── SPEC_AGENT.md               architecture spec
│   └── LEARNINGS.md                methodology log, one entry per build step
├── agent/
│   ├── agent.py                    conversational loop, Renderer protocol, prompt caching
│   ├── cli.py                      RichRenderer (display layer)
│   ├── transcript.py               JSONL session logger
│   ├── __main__.py                 REPL entry: `python -m agent`
│   ├── claude_helpers.py           Anthropic client + retry + model constants
│   ├── tool_registry.py            schemas + dispatch (11 tools)
│   └── tools/                      state, classification, scenarios
├── classifier/
│   ├── bank_statement_parser.py    redacted copy of the private classifier
│   └── rule_lookup.py              SQLite-first wrapper
├── db/
│   ├── schema.sql                  CREATE TABLEs from SPEC §4
│   ├── database.py                 connection helpers, DATA_DIR, get_data_source
│   └── migrate.py                  CSV → SQLite ingest
└── data/
    ├── finance.db                  gitignored, SQLite store
    ├── synthetic/                  committed — generator + 15y of fake transactions
    └── real/                       gitignored — never enters the repo
```
