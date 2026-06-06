# CLAUDE.md

Conventions and gotchas for this project. Read [docs/SPEC_AGENT.md](docs/SPEC_AGENT.md) for the architecture and [docs/LEARNINGS_AGENT.md](docs/LEARNINGS_AGENT.md) for the methodology log behind each decision.

---

## Project shape

Personal finance agent: 15 tools (state, classification incl. C2 batch, scenarios) wrapped in a conversational loop using Anthropic Sonnet 4.6 + Haiku 4.5. The redacted legacy ingest pipeline ([classifier/budget_importer.py](classifier/budget_importer.py)) is wired into `python -m db.migrate --raw` (C1) for real-data ingestion; today's classifier path lives in [classifier/rule_lookup.py](classifier/rule_lookup.py) (SQLite-backed). The synthetic-data generator produces 18,780 transactions matching the same taxonomy so the system is demoable without real data.

**Build status: Phase 1 + 13 Phase 2 items shipped.** Steps 1–5 of [SPEC §8](docs/SPEC_AGENT.md#8-build-history) all shipped. From Phase 2: A1 + A2 (rules migrated into `classification_rules` + taxonomy expansion), A3 (`extend_taxonomy` tool), B1 (dispatch-layer apply-gate), B2 (pytest — 116 agent + 21 web deterministic tests + 3 LLM-gated), B2 CI (GitHub Actions on push/PR), B3 (`bank_statement_parser.py` renamed to `budget_importer.py`), C1 (real-data ingestion CLI), C2 (Batch API for bulk Missing classification, 50% Haiku discount), C4 (web UI), D1 (CLI footer shows both `$0.0058 / £0.0046`), D2 (transcript replay) + the web Live/Replay toggle, /admin/stats (operator monitoring). See [SPEC §8](docs/SPEC_AGENT.md#8-build-history) for the full build history and [SPEC §10](docs/SPEC_AGENT.md#10-out-of-scope) for what's deferred. Architecture diagrams: [docs/AGENT_ARCHITECTURE_DIAGRAMS.html](docs/AGENT_ARCHITECTURE_DIAGRAMS.html) (open in a browser).

---

## Everything runs in Docker

Do not `pip install` on the host. Always go through the container:

```powershell
docker compose build                                          # rebuild after code changes
docker compose run --rm agent python -m <module>              # run any module
docker compose run --rm -it agent python -m agent             # interactive REPL
docker compose run --rm -it agent bash                        # poke around
```

The image bakes the code at build time (no source bind-mount). Source edits require `docker compose build` before they take effect.

`data/` and `logs/` are bind-mounted from the host, so `data/finance.db` and `logs/*.jsonl` persist across container restarts.

---

## Hard conventions

These came out of explicit design discussions — don't relitigate without good reason. Each links to the underlying record.

### Preview-before-apply for any irreversible-write tool
Project-wide default. The classification flow is the worked example: `preview_rule_application` (no writes) → user approval → `apply_classification_rule` (mutates). Same shape for any new tool that performs a write the user can't trivially undo. See [docs/LEARNINGS_AGENT.md → Preview-before-apply](docs/LEARNINGS_AGENT.md#preview-before-apply-for-destructive-agent-tools).

Since B1, this is additionally enforced at the dispatch layer: [agent/tool_registry.py](agent/tool_registry.py) `GATED_TOOLS` lists every `apply_*` tool that requires a matching `preview_*` call + user approval in conversation history before it runs. When you add a new irreversible-write tool, add it to `GATED_TOOLS` alongside its preview tool. See [docs/LEARNINGS_AGENT.md → B1](docs/LEARNINGS_AGENT.md#b1--code-gate-for-apply_-tools).

### Two model tiers, routed by task complexity
- `AGENT_MODEL = "claude-sonnet-4-6"` — main agent loop, scenario reasoning, anything ambiguous.
- `CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"` — `suggest_classification` (single row, sync) and `bulk_classify_async` (Batch API, ~50% cheaper, async). The agent picks between them per turn based on the backlog size (>10 rows → batch). Both go through the same forced-tool-use shape so the output dict is identical.

Constants in `agent/claude_helpers.py` (incl. `HAIKU_PRICE_*` and `BATCH_DISCOUNT`). Rationale in [SPEC §3.3](docs/SPEC_AGENT.md#33--model-routing-sonnet-46-for-the-agent-loop-haiku-45-for-classification).

### State-store boundary rule
`set_agent_state` is for durable facts the next session would benefit from (e.g. `mortgage_rate_change_date`). Not for conversational scratch, not for things re-derivable from a tool call. Rationale: [SPEC §3.1](docs/SPEC_AGENT.md#31--what-stateful-means-hybrid-session--cross-session-memory).

### Taxonomy is table-defined; agent extends only with approval
Post-A2 the taxonomy lives in `classifier/rules_seed.py` (loaded into `classification_rules` by `db/seed_rules.py`). A2 added `Travel`/`Transport/rail`/`Leisure/subscription/video`. NETFLIX/AIRBNB/TRAINLINE/DISNEY+ are deliberately kept in `data/synthetic/generate_synthetic.py:NOISE_MEMOS` so the agent demo loop still has Missing transactions to classify into the new categories. When the agent encounters something with no good fit, it must say so honestly (in the system prompt). Post-A3 the agent can extend the taxonomy at runtime via the paired `preview_taxonomy_extension` / `apply_taxonomy_extension` tools (B1 enforces the preview-before-apply contract at dispatch time). Edits to the canonical seed list still require a source change to `rules_seed.py`.

### Currency convention
Transaction data: **£** (UK).
API costs: tracked internally in **$** (Anthropic billing reality).
CLI footer renders **both** per-turn: `$0.0058 / £0.0046` (D1, 2026-06-02). The £ side uses a hardcoded `USD_TO_GBP` constant in [agent/claude_helpers.py](agent/claude_helpers.py); refresh it when the displayed figure visibly drifts. Web UI stays $-only (recruiter-facing; the budget cap is in $).

### Redaction discipline at the data-generation layer
The redacted classifier and the synthetic data generator share the same placeholders (`ACCOUNT_CURRENT`, `COMPANY_A`, `CLEANER_A`, `CARDHOLDER_NAME_CARDNUMBER`, `LENDER_NAME_REFERENCE`). When introducing new redactable values, update both. See [SPEC §9](docs/SPEC_AGENT.md#9-privacy-and-access-pattern).

---

## Web UI (C4)

The recruiter-clickable demo lives in [web/](web/). Backend = FastAPI (`web/backend/`), frontend = React + Vite + Tailwind (`web/frontend/`). One Docker image, multi-stage build (node compiles the React bundle, python serves both API and the built static files).

```powershell
# Build + run locally
docker compose -f docker-compose.yml -f docker-compose.web.yml build web
docker compose -f docker-compose.yml -f docker-compose.web.yml up web
# Browse http://localhost:8000

# Backend tests (deterministic, no API)
docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm web pytest tests/test_web.py -v

# Frontend dev (hot reload, proxies /api to backend on :8000)
cd web/frontend && npm install && npm run dev      # serves http://localhost:5173
```

Guardrails baked in:
- **Per-session DB** ([web/backend/sessions.py](web/backend/sessions.py)) — each visitor gets a `shutil.copy` of the seed DB under `/tmp/agent-sessions/<id>/`. Threaded via the `SESSION_DB_PATH` ContextVar in [db/database.py](db/database.py).
- **Per-session cost cap** ([web/backend/limits.py](web/backend/limits.py)) — $0.50, checked before every turn so an over-budget request never spends.
- **Per-IP rate limit** — 3 sessions / 24h, in-memory.

The header has a Live/Replay toggle. Replay mode streams a curated transcript from [web/replays/](web/replays/) through the existing `WebSseRenderer` event protocol — so recruiters can watch a canned demo without spending their budget. Replay routes (`GET /api/replays`, `GET /api/replays/{id}/stream`) deliberately bypass the cost cap, session DB, and rate limit (they're a public read of bundled content with no API call). To add another canned demo: drop a JSONL into `web/replays/` and add an entry to `REPLAY_CATALOGUE` in [web/backend/replays.py](web/backend/replays.py).

Operator monitoring: `GET /admin/stats` returns a JSON snapshot (session counts, demo spend, replay streams, rate-limit rejections) gated by `X-Admin-Token` matched against the `ADMIN_TOKEN` env var. Unset by default → 503 ("admin disabled"). Run locally with `-e ADMIN_TOKEN=devtoken` and `curl -H "X-Admin-Token: devtoken" http://localhost:8000/admin/stats | jq .`. Counters live on `app.state.stats` (in-memory; restart wipes).

Reset the in-memory rate-limit counter in dev: restart the container (`docker compose ... restart web`). State is process-local.

The demo runs synthetic-data-only forever — real-data ingestion via the web is explicitly out of scope.

---

## Verify-by-running (pytest)

The test suite lives under [tests/](tests/) — one `test_<module>.py` per source module, with shared fixtures in [tests/conftest.py](tests/conftest.py). 116 agent-side deterministic tests run in ~65s + 21 web tests in ~25s; LLM-touching tests are marked `@pytest.mark.llm` and auto-skip unless `RUN_LLM_TESTS=1` is set. See [LEARNINGS → pytest adoption](docs/LEARNINGS_AGENT.md#b2--pytest-adoption).

```powershell
# Deterministic — no API key needed
docker compose run --rm agent pytest --ignore=tests/test_web.py   # full suite, llm skipped
docker compose run --rm agent pytest -k state                 # one module
docker compose run --rm agent pytest tests/test_classification.py -v

# (test_web.py needs the web image; run via the web compose overlay — see Web UI section.)

# Generators and migrators are still ad-hoc entry points (not pytest):
docker compose run --rm agent python data/synthetic/generate_synthetic.py
docker compose run --rm agent python db/migrate.py --replace

# C1 — real-data ingestion (drop raw exports into data/real/raw/ first):
docker compose run --rm agent python -m db.migrate --raw 2026_06_02
#   reads data/real/raw/<date>_*.csv → writes data/real/preprocessed/<date>_*.csv
#   → ingests into data/finance.db as data_source='real'.

# Manual eyeball check on the CLI rendering layer:
docker compose run --rm agent python -m agent.cli

# Replay a recorded transcript (D2) — re-renders without spending API budget:
docker compose run --rm agent python -m agent.replay logs/<timestamp>.jsonl
docker compose run --rm agent python -m agent.replay logs/<ts>.jsonl --delay-seconds 1   # paced
```

LLM-touching tests are gated by `RUN_LLM_TESTS=1`. Set it in `.env` alongside `ANTHROPIC_API_KEY` to include them:

```powershell
docker compose run --rm -e RUN_LLM_TESTS=1 agent pytest       # full incl. ~$0.10 LLM
```

A full 3-turn end-to-end conversation costs ~$0.08 at current Sonnet 4.6 rates.

---

## Sensitive paths (never commit, never enter the image)

- `data/real/` — personal bank exports. gitignored, dockerignored.
- `data/finance.db` — may contain real-data rows after migration. gitignored, mounted as volume.
- `.env` — API key and runtime flags. gitignored.
- `logs/` — conversation transcripts may contain anything the user typed. gitignored.

If you're about to commit something from any of those paths, stop and ask.

---

## When making changes

- **Code edits → `docker compose build` before testing.** The image doesn't bind-mount source.
- **New tools → update both** the function and its `SCHEMAS` entry in the same module. `tool_registry.py` validates the pairing at import time.
- **New irreversible-write tools → add a paired `preview_*` tool.** See the preview-before-apply convention above.
- **New cross-session state tables → add to [db/schema.sql](db/schema.sql).** All `CREATE`s use `IF NOT EXISTS`; `db/database.py:open_db` runs the schema on every connect. The C2 `pending_batches` table is the most recent example.
- **Real-data ingest (`python -m db.migrate --raw <date>`)** reads from `data/real/raw/<date>_*.csv` and writes `data/real/preprocessed/<date>_*.csv` before ingesting. Root is `BUDGET_DATA_DIR` (default `./data/real`); override per-invocation with `--budget-root`. `main()` sets `SESSION_DB_PATH=args.db` so `classifier.rule_lookup` opens against the right DB even when `--db` differs from the module default.
- **Pricing changes → update `agent/agent.py`'s `PRICE_*` constants and `claude_helpers.AGENT_MODEL`/`CLASSIFIER_MODEL` if model versions changed.**
- **New `set_agent_state` use cases → check the state-store boundary rule.** Is this really a durable fact for next session, or just conversational scratch?
- **New LEARNINGS entry → append to `docs/LEARNINGS_AGENT.md` Step N section, or add a Cross-cutting decision** if the lesson generalises beyond the step.
