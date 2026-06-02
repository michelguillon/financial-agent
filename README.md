# Personal Finance Agent

A conversational agent that classifies a backlog of unrecognised bank
transactions with human approval, and answers forward-looking financial
questions ("if my mortgage rate doubles, where should I cut back?") grounded
in real transaction history.

Built as a Week 2 AI Agents portfolio project. Designed to keep working
after the interview — the classifier gets smarter with each approved rule,
and the same agent loop powers both the maintenance task and the planning
conversations.

> **Status:** Phase 1 (Steps 1–5) shipped, plus Phase 2 add-ons —
> A1/A2 (rules table + taxonomy expansion), A3 (extend_taxonomy tool),
> B1 (dispatch-layer code gate for `apply_*` tools), B2 (pytest with
> ~100 tests), C4 (web UI), D2 (transcript replay).
> **Live demo:** _hosted URL forthcoming — runs synthetic UK data,
> ephemeral sessions, $0.50 per-session budget cap._
> See [docs/PHASE_2_BACKLOG.md](docs/PHASE_2_BACKLOG.md) for what's done and what's deferred.

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

- **Classifier migrated to a SQLite rules table.** Phase 1 used a
  SQLite-first wrapper that fell through to the original hardcoded
  `if/elif` chain on misses; A1 (Phase 2) ported the ~40 rules into
  `classification_rules` and deleted the fallback chain. `rule_lookup.py`
  is now the only path. The agent can extend the taxonomy at runtime via
  the paired `preview_taxonomy_extension` / `apply_taxonomy_extension`
  tools (A3). ([SPEC §3.4](docs/SPEC_AGENT.md#34--classification-engine-migration-two-phase))

- **Demo-mode without auth.** A data-layer switch: if `data/real/` exists,
  use it; otherwise fall back to the synthetic dataset committed to the
  repo. Recruiters get a working demo immediately; real data stays on the
  home server. ([SPEC §3.6](docs/SPEC_AGENT.md#36--demo-mode))

---

## What's built so far

**Phase 1 — original build sequence (SPEC §8):**

| Step | Component | Status |
|------|-----------|--------|
| 1 | Synthetic data generator (15y of UK transactions) | ✅ |
| 2 | SQLite schema + CSV ingestion | ✅ |
| 3 | SQLite-first rule lookup wrapper | ✅ |
| 4 | Tool implementations (13 tools) + Docker | ✅ |
| 5 | Agent loop (Sonnet 4.6 + Haiku 4.5 + prompt caching) | ✅ |

**Phase 2 — add-ons shipped since the portfolio cut-off:**

- **A1 + A2** — ~40 hardcoded rules migrated into `classification_rules`; taxonomy extended with Travel/rail/video.
- **A3** — `extend_taxonomy` tool (paired `preview` + `apply`) so the agent can grow the taxonomy at runtime with user approval.
- **B1** — dispatch-layer code gate that blocks `apply_*` tool calls unless conversation history shows a matching preview + user approval (regex fast-path + Haiku 4.5 fallback).
- **B2** — pytest adoption, ~100 deterministic tests + 3 `@pytest.mark.llm` gated tests.
- **C4** — web UI (FastAPI + React + Vite + Tailwind, single Docker image, SSE streaming, per-session DB + cost cap).
- **D2** — `python -m agent.replay <path>` re-renders a recorded transcript through the existing Renderer protocol.

Methodology notes and surprises from each step + Phase 2 item are logged in
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

Run the test suite (no API key needed for the deterministic path):
```powershell
docker compose run --rm agent pytest --ignore=tests/test_web.py
# 100 deterministic tests in ~65s. To also run the LLM-touching ones
# (~$0.10 of Anthropic spend), set RUN_LLM_TESTS=1 in .env.
```

Replay a recorded session without spending API budget:
```powershell
docker compose run --rm agent python -m agent.replay logs/<timestamp>.jsonl
docker compose run --rm agent python -m agent.replay logs/<ts>.jsonl --delay-seconds 1   # paced for live demos
```

Web UI (recruiter-clickable demo, synthetic data only, per-session $0.50 cap):
```powershell
docker compose -f docker-compose.yml -f docker-compose.web.yml build web
docker compose -f docker-compose.yml -f docker-compose.web.yml up web
# Browse http://localhost:8000
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

The legacy importer (`budget_importer.py`, copied from a private repo and
preserved for C1) is redacted before commit per
[SPEC §9](docs/SPEC_AGENT.md#9-privacy-and-access-pattern): account
numbers, employer names, cleaner names, cardholder details, and loan
references all become generic placeholders. The synthetic generator uses
the same placeholders, so synthetic and real data flow through the same
classifier path. (The classifier itself moved into [classifier/rules_seed.py](classifier/rules_seed.py)
+ [classifier/rule_lookup.py](classifier/rule_lookup.py) in A1; the importer
is dormant until C1.)

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
├── docker-compose.web.yml          web UI overlay: builds the multi-stage web image
├── pytest.ini                      registers the `llm` marker, gates LLM tests
├── requirements.txt                anthropic, python-dotenv, pandas, rich, fastapi (web), …
├── .env.example
├── docs/
│   ├── SPEC_AGENT.md                  architecture spec
│   ├── LEARNINGS.md                   methodology log, one entry per step + Phase 2 item
│   ├── PHASE_2_BACKLOG.md             what's shipped vs deferred
│   └── AGENT_ARCHITECTURE_DIAGRAMS.html (open in a browser)
├── agent/
│   ├── agent.py                    conversational loop, Renderer protocol, prompt caching
│   ├── cli.py                      RichRenderer (terminal display layer)
│   ├── replay.py                   `python -m agent.replay <jsonl>` — re-render transcripts (D2)
│   ├── transcript.py               JSONL session logger
│   ├── __main__.py                 REPL entry: `python -m agent`
│   ├── claude_helpers.py           Anthropic client + retry + model constants
│   ├── tool_registry.py            schemas + dispatch + B1 apply-gate (13 tools)
│   └── tools/                      state, classification, scenarios
├── web/
│   ├── backend/                    FastAPI app, per-session DB, $0.50 cap, SSE renderer (C4)
│   └── frontend/                   React + Vite + Tailwind chat UI
├── tests/                          pytest suite — ~100 deterministic + 3 @pytest.mark.llm
├── classifier/
│   ├── budget_importer.py          legacy bank-CSV → Excel ingestion pipeline (dormant; preserved for C1)
│   ├── rule_lookup.py              SQLite-only lookup against classification_rules
│   └── rules_seed.py               canonical seed list of ~40 rules (loaded by db/seed_rules.py)
├── db/
│   ├── schema.sql                  CREATE TABLEs from SPEC §4
│   ├── database.py                 connection helpers, DATA_DIR, get_data_source, SESSION_DB_PATH
│   ├── migrate.py                  CSV → SQLite ingest
│   └── seed_rules.py               loads rules_seed.py into classification_rules
├── data/
│   ├── finance.db                  gitignored, SQLite store
│   ├── synthetic/                  committed — generator + 15y of fake transactions
│   └── real/                       gitignored — never enters the repo
└── logs/                           gitignored — JSONL session transcripts (replay-able via agent.replay)
```
