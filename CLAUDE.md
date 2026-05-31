# CLAUDE.md

Conventions and gotchas for this project. Read [docs/SPEC_AGENT.md](docs/SPEC_AGENT.md) for the architecture and [docs/LEARNINGS.md](docs/LEARNINGS.md) for the methodology log behind each decision.

---

## Project shape

Personal finance agent: 11 tools (state, classification, scenarios) wrapped in a conversational loop using Anthropic Sonnet 4.6 + Haiku 4.5. Real-data classifier (`classifier/bank_statement_parser.py`) is a redacted copy of a private repo; synthetic-data generator produces 18,780 transactions matching the same taxonomy so the system is demoable without real data.

Build status: Steps 1–5 of [SPEC §8](docs/SPEC_AGENT.md#8-build-sequence) complete. Step 6 (web UI) is optional and post-Week-2.

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

### Taxonomy is fixed
The category taxonomy (SPEC §4) reflects the user's real-world historical spending. It has gaps — no `video` sub for streaming, no `rail` sub for trains, no `Travel` main. When the model classifies into an imperfect fit, the agent must say so honestly (this is in the system prompt). Don't invent new categories silently. Phase 2 will evolve the taxonomy via the agent itself.

### Currency convention
Transaction data: **£** (UK).
API costs: **$** (Anthropic billing).
Both appear in the CLI footer. Don't convert; be explicit which is which.

### Redaction discipline at the data-generation layer
The redacted classifier and the synthetic data generator share the same placeholders (`ACCOUNT_CURRENT`, `COMPANY_A`, `CLEANER_A`, `CARDHOLDER_NAME_CARDNUMBER`, `LENDER_NAME_REFERENCE`). When introducing new redactable values, update both. See [SPEC §9](docs/SPEC_AGENT.md#9-privacy-and-access-pattern).

---

## Verify-by-running (no pytest yet)

Each module's `if __name__ == "__main__":` block is its smoke test. The pytest cutover trigger is "regression surface gets too big to eyeball"; we're not there yet. See [LEARNINGS → Testing strategy](docs/LEARNINGS.md#testing-strategy-so-far-verify-by-running).

```powershell
# Deterministic — no API key needed
docker compose run --rm agent python data/synthetic/generate_synthetic.py
docker compose run --rm agent python db/migrate.py --replace
docker compose run --rm agent python -m agent.tools.state
docker compose run --rm agent python -m agent.tools.classification
docker compose run --rm agent python -m agent.tools.scenarios
docker compose run --rm agent python -m agent.tool_registry
docker compose run --rm agent python -m agent.transcript
docker compose run --rm agent python -m agent.agent           # mocks the API
docker compose run --rm agent python -m agent.cli             # eyeball rendering
```

LLM-touching tests are gated by `RUN_LLM_TESTS=1` to avoid burning credits. Set it in `.env` alongside `ANTHROPIC_API_KEY` to enable:

```powershell
# Now suggest_classification and the agent.agent end-to-end test will actually call the API.
docker compose run --rm agent python -m agent.tools.classification
docker compose run --rm agent python -m agent.agent
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
