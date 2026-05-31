# CLAUDE.md

Conventions and gotchas for this project. Read [docs/SPEC_AGENT.md](docs/SPEC_AGENT.md) for the architecture and [docs/LEARNINGS.md](docs/LEARNINGS.md) for the methodology log behind each decision.

---

## Project shape

Personal finance agent: 13 tools (state, classification, scenarios) wrapped in a conversational loop using Anthropic Sonnet 4.6 + Haiku 4.5. Real-data classifier (`classifier/bank_statement_parser.py`) is a redacted copy of a private repo; synthetic-data generator produces 18,780 transactions matching the same taxonomy so the system is demoable without real data.

**Build status: Phase 1 complete.** Steps 1–5 of [SPEC §8](docs/SPEC_AGENT.md#8-build-sequence) are all shipped, verified end-to-end, and pushed. The Phase 2 backlog ([docs/PHASE_2_BACKLOG.md](docs/PHASE_2_BACKLOG.md)) lists deferred items from across the SPEC and LEARNINGS, organised by category with rough scope estimates. Architecture diagrams: [docs/AGENT_ARCHITECTURE_DIAGRAMS.html](docs/AGENT_ARCHITECTURE_DIAGRAMS.html) (open in a browser).

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
Project-wide default. The classification flow is the worked example: `preview_rule_application` (no writes) → user approval → `apply_classification_rule` (mutates). Same shape for any new tool that performs a write the user can't trivially undo. See [docs/LEARNINGS.md → Preview-before-apply](docs/LEARNINGS.md#preview-before-apply-for-destructive-agent-tools).

### Two model tiers, routed by task complexity
- `AGENT_MODEL = "claude-sonnet-4-6"` — main agent loop, scenario reasoning, anything ambiguous.
- `CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"` — `suggest_classification` only (constrained structured output, runs frequently).

Constants in `agent/claude_helpers.py`. Rationale in [SPEC §3.3](docs/SPEC_AGENT.md#33--model-routing-sonnet-46-for-the-agent-loop-haiku-45-for-classification).

### State-store boundary rule
`set_agent_state` is for durable facts the next session would benefit from (e.g. `mortgage_rate_change_date`). Not for conversational scratch, not for things re-derivable from a tool call. Rationale: [SPEC §3.1](docs/SPEC_AGENT.md#31--what-stateful-means-hybrid-session--cross-session-memory).

### Taxonomy is table-defined; agent extends only with approval
Post-A2 the taxonomy lives in `classifier/rules_seed.py` (loaded into `classification_rules` by `db/seed_rules.py`). A2 added `Travel`/`Transport/rail`/`Leisure/subscription/video`. NETFLIX/AIRBNB/TRAINLINE/DISNEY+ are deliberately kept in `data/synthetic/generate_synthetic.py:NOISE_MEMOS` so the agent demo loop still has Missing transactions to classify into the new categories. When the agent encounters something with no good fit, it must say so honestly (in the system prompt). A future A3 will let the agent propose new taxonomy entries via a paired preview/apply tool; until then, taxonomy edits require a source change to `rules_seed.py`.

### Currency convention
Transaction data: **£** (UK).
API costs: **$** (Anthropic billing).
Both appear in the CLI footer. Don't convert; be explicit which is which.

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

Reset the in-memory rate-limit counter in dev: restart the container (`docker compose ... restart web`). State is process-local.

The demo runs synthetic-data-only forever — real-data ingestion via the web is explicitly out of scope.

---

## Verify-by-running (pytest)

The test suite lives under [tests/](tests/) — one `test_<module>.py` per source module, with shared fixtures in [tests/conftest.py](tests/conftest.py). Deterministic tests run in <10s; LLM-touching tests are marked `@pytest.mark.llm` and auto-skip unless `RUN_LLM_TESTS=1` is set. See [LEARNINGS → pytest adoption](docs/LEARNINGS.md#b2--pytest-adoption).

```powershell
# Deterministic — no API key needed
docker compose run --rm agent pytest                          # full suite, llm skipped
docker compose run --rm agent pytest -k state                 # one module
docker compose run --rm agent pytest tests/test_classification.py -v

# Generators and migrators are still ad-hoc entry points (not pytest):
docker compose run --rm agent python data/synthetic/generate_synthetic.py
docker compose run --rm agent python db/migrate.py --replace

# Manual eyeball check on the CLI rendering layer:
docker compose run --rm agent python -m agent.cli
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
- **Pricing changes → update `agent/agent.py`'s `PRICE_*` constants and `claude_helpers.AGENT_MODEL`/`CLASSIFIER_MODEL` if model versions changed.**
- **New `set_agent_state` use cases → check the state-store boundary rule.** Is this really a durable fact for next session, or just conversational scratch?
- **New LEARNINGS entry → append to `docs/LEARNINGS.md` Step N section, or add a Cross-cutting decision** if the lesson generalises beyond the step.
