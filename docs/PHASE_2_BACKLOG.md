# Phase 2 Backlog

Phase 1 (the Week-2 portfolio build, SPEC §8 Steps 1–5) is complete. This file consolidates every deferred item from across the SPEC and LEARNINGS so a future session has a single place to pick from.

Each item has: **what** • **why** • **where it came from** • **rough scope**.

Pick one or more to work on next session. Most are independent; dependencies are flagged in the "depends on" lines.

---

## A. Classification engine evolution

### A1 — Migrate hardcoded chain into `classification_rules` table
**What.** Phase 2 of SPEC §3.4 proper. The 60+ regex rules in `classifier/bank_statement_parser.py:categories()` get migrated into rows of the `classification_rules` SQLite table. The Python function becomes a thin wrapper around the table lookup; the hardcoded if/elif chain is retired.
**Why.** All rules become inspectable, editable, exportable. The agent can show, evolve, and version-control the user's whole classification logic via tool calls. End state described in SPEC §3.4.
**Where from.** [SPEC §3.4](SPEC_AGENT.md#34--classification-engine-migration-two-phase).
**Scope.** Half-day. A migration script that walks `categories()` and emits ~60 INSERT rows; the wrapper in `classifier/rule_lookup.py` already supports table-first lookup, so retiring the chain is mostly deleting it. Round-trip verifier (Steps 1+3) still works — should report 100% agreement against the synthetic data, just routed via the table.

### A2 — Taxonomy expansion (Travel main, rail sub, video sub)
**What.** Extend SPEC §4 taxonomy: add `Travel` as a new main (covers AIRBNB, hotels, flights), `Transport/rail` sub (TRAINLINE), `Leisure/subscription/video` sub (NETFLIX, DISNEY+). Update the synthetic generator to use these where appropriate; update the classifier's rules.
**Why.** Live LLM validation showed Haiku correctly pigeonholing into "least-wrong" categories because there's no good fit (NETFLIX → music, TRAINLINE → taxi, AIRBNB → entertainment). The agent handles it honestly but the data is still misclassified.
**Where from.** [LEARNINGS — Step 4 surprises (taxonomy gaps)](LEARNINGS.md#step-4--tool-implementations--docker).
**Scope.** 1–2 hours. Spec edit + 5–10 new regexes in the classifier + ~20 line update to the synthetic generator + re-run migration. Probably best done together with A3.

### A3 — `extend_taxonomy(main, sub, sub2)` tool
**What.** New agent tool that adds a new taxonomy entry with human approval, then optionally re-runs a list of Missing rows through `suggest_classification` to surface ones that would now fit.
**Why.** Makes the taxonomy evolvable through the agent itself rather than requiring source edits. Closes the loop the LEARNINGS Step 4 entry pointed at.
**Where from.** Plan-mode discussion before Step 4 ("How to handle the taxonomy gaps" → "Add a tool" was the third option).
**Scope.** Half-day. New tool + schema + preview/apply pair (per the project-wide preview-before-apply default). Needs to write to a `taxonomy_extensions` table (new) or hold the list elsewhere — SPEC update needed.
**Depends on.** Naturally pairs with A1 (once rules are in the table, the taxonomy is just the distinct categories used by rules).

---

## B. Safety & hardening

### B1 — Code gate for `apply_classification_rule`
**What.** The `tool_registry.dispatch()` function inspects the recent conversation history before allowing `apply_classification_rule` to run. If no approval pattern is detected ("yes", "ok", "go ahead", "looks right", etc. in the user message after the preview), it raises an error that gets injected as `is_error: True` tool_result.
**Why.** Phase 1 enforces this contract via prompt instruction only. A code gate hardens against prompt injection, model regressions, or future tool-use patterns that bypass the convention.
**Where from.** [SPEC §6](SPEC_AGENT.md#6-agent-loop) ("A code gate ... is a Phase 2 hardening option") and explicitly noted in the [Preview-before-apply cross-cutting decision](LEARNINGS.md#preview-before-apply-for-destructive-agent-tools).
**Scope.** 2–3 hours. The tricky part is defining "approval pattern" generously enough to avoid false negatives. Probably need a small LLM call ("did this user message express approval?") rather than a regex.

### B2 — Adopt pytest
**What.** Convert the inline `if __name__ == "__main__":` smoke tests into pytest test files. Add a `tests/` directory, a `pytest.ini`, and CI (GitHub Actions). Real assertions, fixtures for the synthetic DB, gated LLM tests via a pytest marker (`@pytest.mark.llm`).
**Why.** The LEARNINGS testing-strategy decision pinned the trigger to "Step 5 — when the regression surface gets too big to eyeball". We shipped Step 5 with verify-by-running and it worked, but adding any of A/B/C below increases that surface meaningfully.
**Where from.** [LEARNINGS — Cross-cutting decisions → Testing strategy](LEARNINGS.md#testing-strategy-so-far-verify-by-running).
**Scope.** Day. Mostly mechanical conversion. The interesting design questions are: how to share the synthetic DB across tests (fixture vs in-memory), and whether to add an end-to-end "scripted conversation" pytest fixture for catching prompt regressions.

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

### C4 — Web UI (React + FastAPI)
**What.** Browser front-end for the agent, replacing the CLI. FastAPI endpoint wraps `run_turn`; React renders the tool calls/results/responses the same way `agent/cli.py` does today.
**Why.** Demo-quality UI for the portfolio (CLI is fine for engineers; recruiters benefit from a clickable demo). SPEC §8 Step 6 — explicitly optional, post-Week-2.
**Where from.** [SPEC §8 Step 6](SPEC_AGENT.md#8-build-sequence).
**Scope.** Week+. The agent loop is already clean enough to drop into FastAPI (Renderer protocol from Step 5 means the rendering and the loop are decoupled). The real work is the React UI and the deployment story (the M720q home server can host both).

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

## Suggested ordering

**If picking one thing:** A1 (Phase 2 §3.4 proper). It's the canonical Phase 2 item, ships clean value (inspectable rule table), and unblocks A3.

**If picking a small bundle:** A1 + A2 together — the taxonomy gaps are easier to fix once rules live in the table, and the synthetic generator + classifier updates are one consistent edit.

**If picking the safety angle:** B1 + B2 — hardens the agent against future drift and gives you a regression net for everything else.

**If picking the long-term-utility angle:** C1 (real-data pipeline) — turns this from a demo into a tool you'd actually use weekly.

**If picking the portfolio angle:** C4 (web UI). Slow to build but the demo video lands harder.

---

## Out of scope (until explicitly chosen)

- Multi-user support, auth, anything cloud-hosted — same reasoning as SPEC §10.
- Replacing the raw-API approach with a framework (LangGraph etc.) — SPEC §3.2 explicitly defers this to a later project.
- Rewriting in another language. Don't.
