# Phase 2 Backlog

**Project status: complete.** Phase 1 (Steps 1–5) and all planned Phase 2 tickets (A1–A3, B1–B3, C1–C2, C4, D1, D2 + follow-ups, /admin/stats, B2 CI residual) are shipped. The items below are nice-to-have and are not planned unless a specific need arises.

---

## Shipped

| Ticket | Done | What |
|--------|------|------|
| A1 | 2026-05-31 | Rules into `classification_rules` table; hardcoded chain deleted |
| A2 | 2026-05-31 | Taxonomy expansion: Travel, rail, video |
| A3 | 2026-05-31 | `extend_taxonomy` preview/apply tool pair |
| B2 | 2026-05-31 | pytest suite (now 116 deterministic + 3 LLM-gated) |
| B3 | 2026-06-02 | `bank_statement_parser.py` → `budget_importer.py` rename |
| C1 | 2026-06-02 | Real-data ingestion CLI (`--raw YYYY_MM_DD`) |
| C2 | 2026-06-01 | Batch API for bulk Missing classification (50% discount) |
| C4 | 2026-05-31 | React + FastAPI web UI, SSE streaming, per-session DB, $0.50 cap |
| B1 | 2026-06-01 | Dispatch-layer code gate on `apply_*` tools |
| D1 | 2026-06-02 | CLI footer renders both `$0.0058 / £0.0046` (USD_TO_GBP constant) |
| D2 | 2026-06-01 | Transcript replay CLI + web Live/Replay toggle |
| /admin/stats | 2026-06-01 | Operator monitoring endpoint (`X-Admin-Token` auth) |
| B2 CI | 2026-06-02 | `.github/workflows/test.yml` — deterministic pytest on push/PR |

See [LEARNINGS.md](LEARNINGS.md) for notes on each. See [SPEC_AGENT.md §8](SPEC_AGENT.md#8-build-history) for the full build history.

---

## Nice-to-have (not planned)

### C3 — Conversation history summarisation
**What.** Once session exceeds N turns, summarise older turns into `agent_state` and drop them from the messages array.  
**Why not now.** The $0.50 web cap already bounds context growth. Only worth doing if the cap is raised or D3 (resume) is added.  
**Scope.** Day.

### D3 — Resume conversation
**What.** `--resume <session_id>` flag that loads the last messages array from a transcript and continues.  
**Why not now.** Only worth doing if you find yourself wanting it after more use.  
**Scope.** Half-day.

### C1 residuals
- `--with-excel` toggle: calls dormant `update_excel_budget()` + adds `openpyxl` to requirements. Half-day. Only useful if you still maintain `budget.xlsx`.
- Barclaycard / Sainsbury fixture coverage: worth adding when those paths surface a real bug.

### D2 residuals
- Multi-demo picker: adding entries to the catalogue in `web/backend/replays.py` is a content change, not a code change.
- HTML export (`--html out.html`): single self-contained file for sharing. A separate path from the web toggle.

---

## Permanently out of scope

- Multi-user support, auth, cloud-hosting — same reasoning as SPEC §10.
- Replacing raw-API approach with LangGraph etc. — SPEC §3.2 explicitly defers to a future project.
- Rewriting in another language. Don't.
