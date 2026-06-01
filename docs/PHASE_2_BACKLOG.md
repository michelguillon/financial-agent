# Phase 2 Backlog

Phase 1 (the Week-2 portfolio build, SPEC §8 Steps 1–5) is complete. This file consolidates every deferred item from across the SPEC and LEARNINGS so a future session has a single place to pick from.

Each item has: **what** • **why** • **where it came from** • **rough scope**.

Pick one or more to work on next session. Most are independent; dependencies are flagged in the "depends on" lines.

---

## A. Classification engine evolution

### ~~A1 — Migrate hardcoded chain into `classification_rules` table~~ ✓ Done (2026-05-31)
Shipped: ~40 rules ported to [classifier/rules_seed.py](../classifier/rules_seed.py); [db/seed_rules.py](../db/seed_rules.py) loads them via `migrate.py` after every ingest. Schema extended with `account_match`/`type_match`/`amount_min`/`amount_max` for the 3 conditional rules. Hardcoded `categories()` deleted from [classifier/bank_statement_parser.py](../classifier/bank_statement_parser.py). REGEXP backed by `re.match` (start-anchored) to preserve original semantics. [tests/test_round_trip.py](../tests/test_round_trip.py) verifies 100% agreement. See [LEARNINGS — A1 + A2](LEARNINGS.md#a1--a2--rules-into-table--taxonomy-expansion).

### ~~A2 — Taxonomy expansion (Travel main, rail sub, video sub)~~ ✓ Done (2026-05-31)
Shipped alongside A1: `Travel/accommodation/hotel`, `Transport/rail`, `Leisure/subscription/video` added to the taxonomy with baseline pre-classified rows (BOOKING.COM, AVANTI WEST COAST, NOW TV). NETFLIX/AIRBNB/TRAINLINE/DISNEY+ deliberately stay in `NOISE_MEMOS` as Missing so the agent demo loop has classification work in the new categories. Test coverage: `tests/test_classification.py:test_a2_new_subs_present_in_taxonomy`.

### ~~A3 — `extend_taxonomy(main, sub, sub2)` tool~~ ✓ Done (2026-05-31)
Shipped: paired `preview_taxonomy_extension` + `apply_taxonomy_extension` tools in [agent/tools/classification.py](../agent/tools/classification.py). Validates the proposed tuple is unprecedented (rejects if `list_categories()` already returns it) and that the pattern matches >0 Missing rows (rejects to keep the taxonomy grounded in actual data — no phantom categories). Wraps the existing preview/apply rule path; no new schema. Bonus: fixed the stale `re.search` → `re.match` mention in `suggest_classification`'s system prompt (semantics changed in A1). See [LEARNINGS — A3](LEARNINGS.md#a3--extend_taxonomy-tool).

---

## B. Safety & hardening

### ~~B1 — Code gate for `apply_*` tools~~ ✓ Done (2026-06-01)
Shipped: `agent/tool_registry.py` declares `GATED_TOOLS` mapping `apply_classification_rule` and `apply_taxonomy_extension` to their required preview tools. `dispatch()` grew an optional `messages` kwarg; the agent loop threads `session.messages` through. `check_approval()` finds the most recent matching `preview_*` tool_use, locates the first plain-string user reply after it, runs a regex fast-path over an approve/deny phrase list, and falls back to Haiku 4.5 (forced tool-use) on ambiguous replies. Failures raise `ApprovalRequiredError`, which the loop converts into an `is_error` tool_result so the agent self-corrects. Test coverage: 14 deterministic tests + 1 `@pytest.mark.llm`. See [LEARNINGS — B1 — code gate for apply_* tools](LEARNINGS.md#b1--code-gate-for-apply_-tools).

### ~~B2 — Adopt pytest~~ ✓ Done (2026-05-31)
Shipped: 47 deterministic tests in ~8s + 2 `@pytest.mark.llm` tests gated by `RUN_LLM_TESTS=1`. Hybrid DB fixture (session-scoped seed + per-test `shutil.copy`), monkeypatched `db.database.DB_PATH`, `__main__` smoke blocks deleted from 6 modules. See [LEARNINGS — B2 — pytest adoption](LEARNINGS.md#b2--pytest-adoption).

**Residual:** CI (GitHub Actions) was deferred. ~1h follow-up to add `.github/workflows/test.yml` that runs `docker compose run --rm agent pytest -m "not llm"` on push. ANTHROPIC_API_KEY not needed for the gated path.

**Residual:** scripted-conversation pytest fixture for prompt-regression catching against mock responses (no LLM cost). Phase 3 territory.

### B3 — Slim down `classifier/bank_statement_parser.py`
**What.** Split the preserved `Budget` class (Excel import, raw-CSV combining, the Phase-4 incremental Excel methods) out into its own module — `classifier/budget_importer.py` maybe. The agent's classification path then only needs `pandas` and `re`, not `openpyxl` and `python-dotenv`.
**Why.** Flagged as tech debt in [LEARNINGS — Step 3 surprises](LEARNINGS.md#step-3--rule-lookup-wrapper) ("if __name__ == \"__main__\":` blocks survive copy-paste, dependencies don't"). Reduces the agent's dependency footprint and clarifies what's "agent code" vs "legacy import pipeline".
**Where from.** LEARNINGS Step 3.
**Scope.** 2 hours. The split is mechanical; the only judgement call is whether to keep the Budget class importable from `classifier/__init__.py` for backward compatibility.

---

## C. New capabilities

### C1 — Real-data ingestion pipeline
**What.** Wire the preserved `Budget` class into a CLI command so the user can ingest fresh bank exports without leaving the agent container: `docker compose run --rm agent python -m db.migrate --raw <date>`. Combines the existing `combine_and_rename_files` → `import_raw_data` → `export_preprocessed_data` → `migrate.py` chain.
**Why.** Today, real-data ingestion requires the user's separate `..\banking` repo (not part of this project). For the agent to actually be useful long-term, importing fresh exports needs to live here.
**Where from.** [SPEC §3.6](SPEC_AGENT.md#36--demo-mode) ("Real data access: local network only") + Step 2 plan ("only synthetic for now, defer real" was the deferred option).
**Scope.** Day. Most code already exists (in `bank_statement_parser.py`'s `Budget` class); the work is exposing it as a CLI, adapting the file paths to the Docker-mounted `data/real/` location, and end-to-end testing with a real bank export.
**Depends on.** Easier if B3 is done first (Budget class lives in its own module).

### ~~C2 — Batch API for bulk Missing classification~~ ✓ Done (2026-06-01)
Shipped: two new tools in [agent/tools/classification.py](../agent/tools/classification.py) — `bulk_classify_async(memos)` submits an Anthropic Batch API request and persists state in a new `pending_batches` table; `check_batch_results(batch_id)` polls once, parses results back into the standard 6-field suggestion shape, and caches the completed batch locally. 50% discount on input + output. The async UX threads through `agent.agent.build_system_prompt` — pending in-progress batches are announced in the dynamic block of the next session so the agent can mention them. Batch counters surfaced in `/admin/stats` via a small `agent.tools._stats_sink` indirection (decoupled from FastAPI; CLI runs are a no-op). 8 new agent-side tests + 1 admin-side, full suite stays under 60s. See [LEARNINGS — C2](LEARNINGS.md#c2--batch-api-for-bulk-missing-classification).

### C3 — Conversation history summarisation into agent_state
**What.** Once a session's messages array exceeds N turns, summarise the older turns into a short `agent_state` entry and drop them from the messages array. The agent gets the summary, not the full transcript, for context on long sessions.
**Why.** The Step 5 LEARNINGS shows per-turn cost grows linearly with conversation length (system+tools cached flat; messages array uncached). Long sessions are the realistic use case for a personal finance assistant.
**Where from.** [LEARNINGS — Step 5, agent loop in production](LEARNINGS.md#step-5--agent-loop) ("For very long sessions, periodic summarisation of older turns into agent_state entries (rather than keeping every turn in the messages array forever) is the natural next optimisation").
**Scope.** Day. Trigger heuristic (turn count? token count?), summarisation prompt, agent_state schema for session-summary entries, and the next-turn assembly that injects the summary into context.

### ~~C4 — Web UI (React + FastAPI)~~ ✓ Done (2026-05-31)
Shipped: FastAPI + React + Vite + Tailwind, single multi-stage Docker image. Per-session DB isolation via `SESSION_DB_PATH` ContextVar. Per-session cost cap ($0.50) + per-IP rate limit (3/day) baked in before API spend. SSE streaming via `WebSseRenderer`. See [LEARNINGS — C4](LEARNINGS.md#c4--web-ui). Run locally: `docker compose -f docker-compose.yml -f docker-compose.web.yml up web`.

**Residual / natural follow-ups:**
- **D2 (transcript replay)** — extends the existing SSE event protocol; would let recruiters watch a sample conversation without burning their budget.
- **B1 (code gate)** — more important now that the agent is exposed publicly; protects `apply_classification_rule` + `apply_taxonomy_extension` against prompt-injection.
- **/admin/stats endpoint** — today's session count + spend, surfaceable to operator only.

---

## D. Polish

### D1 — Currency display unification
**What.** Decide whether the CLI footer shows $ (API billing reality) or £ (project currency) — pick one and apply consistently, or render both ("$0.0058 / £0.0046"). Flag in `claude_helpers.py`.
**Why.** Mixing currencies in the same view is a small but real mental tax noted in the Step 5 LEARNINGS open items.
**Scope.** 30 minutes. Mostly a decision; the code change is one line in `agent/cli.py`.

### ~~D2 — Transcript replay tool~~ ✓ Done (2026-06-01)
Shipped: [agent/replay.py](../agent/replay.py) reads a `logs/<ts>.jsonl` and re-emits each event through the Renderer protocol. CLI entry `python -m agent.replay <path>` with `--delay-seconds` (pacing for live demos), `--no-log-header`, and `--silent` (scripting-friendly one-line summary). Required a one-method extension to the Renderer protocol — `show_user_text` — implemented on all three renderers (RichRenderer, SilentRenderer, WebSseRenderer) so a future web-replay endpoint can stack cleanly. 11 deterministic tests + a real-transcript smoke check. See [LEARNINGS — D2 — transcript replay](LEARNINGS.md#d2--transcript-replay).

### ~~D2 follow-up — Web replay toggle~~ ✓ Done (2026-06-01)
Shipped: a Live/Replay segmented toggle in the web UI header that flips to a paced playback of a curated transcript bundled into the image at [web/replays/demo_3turn.jsonl](../web/replays/demo_3turn.jsonl). Backend reuses `agent.replay.replay()` + `WebSseRenderer` — no new streaming code, just two thin routes (`GET /api/replays`, `GET /api/replays/{id}/stream?delay=N`) running the replay in `asyncio.to_thread`. Replay bypasses the cost cap, session DB, and per-IP rate limit by design (it's a public read of bundled content). 5 new web tests. See [LEARNINGS — D2 follow-up](LEARNINGS.md#d2-follow-up--web-replay-toggle).

**Residual / natural follow-ups:**
- **Multi-demo picker** — the catalogue is a Python dict keyed by id; adding more entries is a content change. UI picker would slot in next to the toggle.
- **HTML export** (`--html out.html`) — single self-contained file for email/share. Separate path from the web toggle.

### D3 — "Resume conversation" UX
**What.** New `--resume <session_id>` flag on `python -m agent` that loads the last messages array from a transcript and continues the session. SPEC §3.1 ("session memory dies with the session") was a deliberate Phase 1 choice — Phase 2 can revisit if it actually feels limiting in use.
**Why.** Only worth doing if you find yourself wanting it after using the agent more. Premature otherwise.
**Scope.** Half-day. Replay transcript into a `Session` object, then drop into the REPL loop.

---

### ~~/admin/stats — operator monitoring~~ ✓ Done (2026-06-01)
Shipped: [GET /admin/stats](../web/backend/app.py) returns a JSON snapshot of session counts, demo spend, replay streams, rate-limit rejections, and per-replay-id breakdown. Auth via `X-Admin-Token` header matched against the `ADMIN_TOKEN` env var; unset → 503 ("admin disabled"). In-memory counters live on `app.state.stats` (restart wipes, matching the rest of the demo). 8 new tests; web suite now 21 tests in ~24s. See [LEARNINGS — Admin stats](LEARNINGS.md#admin-stats).

---

## Suggested ordering (updated post-C2)

A1, A2, A3, B1, B2, C2, C4, D2 (CLI), D2's web-replay toggle, and /admin/stats are done. Of what's left:

**If picking the daily-driver angle:** B3 → C1. Slim down `bank_statement_parser.py` first (mechanical), then wire the real-data ingestion pipeline. Turns this from "live demo" into "tool you actually use weekly". Pairs naturally with C2 since real-data ingests produce the fat Missing backlogs that justify async batching.

**If picking polish:** D1 (currency display) + the B2 CI residual. Each ~1 hour.

**If picking nothing big:** C3 (history summarisation) is *not* recommended next — the $0.50 web cap already bounds context growth, so the value is minimal until you raise the cap or add D3 (resume conversation).

---

## Out of scope (until explicitly chosen)

- Multi-user support, auth, anything cloud-hosted — same reasoning as SPEC §10.
- Replacing the raw-API approach with a framework (LangGraph etc.) — SPEC §3.2 explicitly defers this to a later project.
- Rewriting in another language. Don't.
