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

### B1 — Code gate for `apply_classification_rule`
**What.** The `tool_registry.dispatch()` function inspects the recent conversation history before allowing `apply_classification_rule` to run. If no approval pattern is detected ("yes", "ok", "go ahead", "looks right", etc. in the user message after the preview), it raises an error that gets injected as `is_error: True` tool_result.
**Why.** Phase 1 enforces this contract via prompt instruction only. A code gate hardens against prompt injection, model regressions, or future tool-use patterns that bypass the convention.
**Where from.** [SPEC §6](SPEC_AGENT.md#6-agent-loop) ("A code gate ... is a Phase 2 hardening option") and explicitly noted in the [Preview-before-apply cross-cutting decision](LEARNINGS.md#preview-before-apply-for-destructive-agent-tools).
**Scope.** 2–3 hours. The tricky part is defining "approval pattern" generously enough to avoid false negatives. Probably need a small LLM call ("did this user message express approval?") rather than a regex.

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

### C2 — Batch API for bulk Missing classification
**What.** When the agent processes >10 Missing rows in one go, route through Anthropic's Batch API instead of sequential calls. Async pattern: submit batch → return `batch_id` → user comes back later → `check_batch_results` retrieves and applies. 50% cost discount.
**Why.** Documented in SPEC §3.3 with `BATCH_THRESHOLD = 10` as a constant in `claude_helpers.py`. Deferred from Step 5 because it's fundamentally an async UX that didn't fit the synchronous conversational loop.
**Where from.** [SPEC §3.3 — cost levers](SPEC_AGENT.md#33--model-routing-sonnet-46-for-the-agent-loop-haiku-45-for-classification) + Step 5 plan decision ("Skip Batch API in Step 5").
**Scope.** 1–2 days. Two new tools (`bulk_classify_async(memos)` + `check_batch_results(batch_id)`), persistence of pending batch jobs in a new table, and the cross-session UX ("you have N suggestions from yesterday — want to review them?").

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

### D2 — Transcript replay tool
**What.** `python -m agent.replay logs/<timestamp>.jsonl` — reads a transcript and re-renders it through the RichRenderer as if the conversation were happening now. Useful for sharing/reviewing past sessions without re-running them.
**Why.** Demo asset (record once, replay deterministically) + debugging tool. The transcript already contains everything needed; just need a reader.
**Scope.** 2 hours. New `agent/replay.py` module + an entry in `__main__.py` or a separate one.

### D3 — "Resume conversation" UX
**What.** New `--resume <session_id>` flag on `python -m agent` that loads the last messages array from a transcript and continues the session. SPEC §3.1 ("session memory dies with the session") was a deliberate Phase 1 choice — Phase 2 can revisit if it actually feels limiting in use.
**Why.** Only worth doing if you find yourself wanting it after using the agent more. Premature otherwise.
**Scope.** Half-day. Replay transcript into a `Session` object, then drop into the REPL loop.

---

## Suggested ordering (updated post-C4)

A1, A2, A3, B2, and C4 are done. Of what's left:

**If picking the demo-hardening angle (most relevant now that the agent is publicly exposed):** B1 (code gate for `apply_*` tools) + D2 (transcript replay) + a tiny `/admin/stats` endpoint. ~half-day total. B1 makes prompt-injection materially harder; D2 lets recruiters watch a canned conversation without burning their $0.50 budget; stats give you a private monitoring view.

**If picking the daily-driver angle:** B3 → C1. Slim down `bank_statement_parser.py` first (mechanical), then wire the real-data ingestion pipeline. Turns this from "live demo" into "tool you actually use weekly".

**If picking the cost-efficiency angle:** C2 (Batch API for bulk Missing) — 50% Anthropic discount on bulk classification runs. Useful once a real-data ingestion produces a fat Missing backlog (so naturally pairs with B3+C1).

**If picking polish:** D1 (currency display) + the B2 CI residual. Each ~1 hour.

**If picking nothing big:** C3 (history summarisation) is *not* recommended next — the $0.50 web cap already bounds context growth, so the value is minimal until you raise the cap or add D3 (resume conversation).

---

## Out of scope (until explicitly chosen)

- Multi-user support, auth, anything cloud-hosted — same reasoning as SPEC §10.
- Replacing the raw-API approach with a framework (LangGraph etc.) — SPEC §3.2 explicitly defers this to a later project.
- Rewriting in another language. Don't.
