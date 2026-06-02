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

### ~~B3 — Slim down `classifier/bank_statement_parser.py`~~ ✓ Done (2026-06-02)
Shipped: file renamed to [`classifier/budget_importer.py`](../classifier/budget_importer.py) (the file had zero importers across the repo, so no compat shim was needed). Top docstring rewritten to reflect the file's actual role — legacy ingestion + Excel-writer pipeline preserved for C1, not a "categorisation engine" (that was deleted in A1). Active doc references updated (README/CLAUDE.md/SPEC §7 + §9, generator and migrate comments, .env.example). Historical references in LEARNINGS / SPEC §3.4 kept verbatim — they describe past state. No code/runtime change; agent suite stays at 109 tests in ~57s. See [LEARNINGS — B3](LEARNINGS.md#b3--slim-down-bank_statement_parserpy).

---

## C. New capabilities

### ~~C1 — Real-data ingestion pipeline~~ ✓ Done (2026-06-02)
Shipped: `python -m db.migrate --raw YYYY_MM_DD` runs the Budget pipeline end-to-end inside the container. New `data/real/raw/` + `data/real/preprocessed/` layout (replacing the legacy `BUDGET_DATA_DIR/tmp_data/`); `BUDGET_DATA_DIR` defaults to `./data/real` and is overridable per-invocation with `--budget-root`. Three live bugs fixed in `classifier/budget_importer.py` (missing `categories` import, `self.data_append` typo, four `_append` calls broken in pandas 2.x). `SESSION_DB_PATH` reused inside `db/migrate.py:main()` so `rule_lookup` opens against `--db` not the module default. 7 new deterministic tests; suite now 116 tests in ~64s. See [LEARNINGS — C1](LEARNINGS.md#c1--real-data-ingestion-cli).

**Residual / natural follow-ups:**
- **`--with-excel` toggle.** `update_excel_budget()` is dormant inside `budget_importer.py`. Adding a flag that calls it (with `openpyxl` in `requirements.txt`) is mechanical now that the SQLite path is wired.
- **Barclaycard / Sainsbury fixture coverage.** Tests cover current_account + amex paths; the other two importers were exercised end-to-end manually but lack pytest fixtures.

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

## Suggested ordering (updated post-C1)

A1, A2, A3, B1, B2, B3, C1, C2, C4, D2 (CLI), D2's web-replay toggle, and /admin/stats are done. Of what's left:

**If picking polish:** D1 (currency display) + the B2 CI residual. Each ~1 hour.

**If picking the daily-driver follow-up:** the `--with-excel` toggle on `python -m db.migrate --raw` (calls the dormant `update_excel_budget()` after SQLite ingest; needs `openpyxl` in `requirements.txt`). ~Half a day. Only matters if you still maintain `budget.xlsx` separately from the agent.

**If picking nothing big:** C3 (history summarisation) is *not* recommended next — the $0.50 web cap already bounds context growth, so the value is minimal until you raise the cap or add D3 (resume conversation).

---

## Out of scope (until explicitly chosen)

- Multi-user support, auth, anything cloud-hosted — same reasoning as SPEC §10.
- Replacing the raw-API approach with a framework (LangGraph etc.) — SPEC §3.2 explicitly defers this to a later project.
- Rewriting in another language. Don't.
