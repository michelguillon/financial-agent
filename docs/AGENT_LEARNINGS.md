# Learnings — Personal Finance Agent

A running log of methodology and surprises from building the agent.

**Phase 1 (Steps 1–5)** covers the original portfolio build — synthetic data, migration, classifier, tools, and the agent loop. **Phase 2 (A\*/B\*/C\*/D\* tickets)** covers the hardening and extension work that followed: rules into the DB, pytest, web UI, taxonomy expansion, code gate, Batch API, real-data ingestion, and transcript replay.

The goal of this doc is the methodology — not the answers. If a future project hits a similar problem, what here would help?

---

## Cross-cutting decisions

### Testing strategy: verify-by-running through Phase 1, pytest from B2 onward

**Phase 1 (Steps 1–5):** no pytest, no fixtures, no CI. Each step's validator
baked into the tool itself — the synthetic generator's summary, the
throwaway round-trip verifier in Step 1, `migrate.py`'s validation
epilogue, per-module `if __name__ == "__main__":` smoke blocks. At that
scale, "run the script, eyeball the output" was faster than writing test
infrastructure.

**Trigger that flipped it:** the start of Phase 2. A1/A2 (rules-into-table
migration + taxonomy expansion) re-shapes the classifier path and the
synthetic generator together, so the round-trip verifier and the
preview/apply contract need a real regression net. The original Step 5
trigger turned out to be too early — the agent loop alone was eyeballable;
what wasn't was *editing the classifier mid-flight without breaking the
flow*. See [B2 — pytest adoption](#b2--pytest-adoption) below for the
mechanics.

### Preview-before-apply for destructive agent tools

**Default pattern:** any agent tool that performs an irreversible write
gets a paired read-only preview tool that reports the scope of the
intended change. The agent calls the preview first, the human sees the
blast radius, the apply tool only runs after explicit approval.

In this project that shape is `preview_rule_application` →
`apply_classification_rule` (SPEC §5.1). The same shape applies to any
future tool that deletes, bulk-overwrites, sends external messages, or
calls a paid API on the user's behalf.

**Why this beats the alternatives:**
- **Prompt-only safety** ("the model is told to ask first") relies on
  model behaviour every turn — fragile, breaks under unusual phrasing,
  and a single forgotten instruction is a footgun.
- **Tool-internal confirmation** (the apply tool returns "are you sure?"
  and waits) breaks the agent loop's straight-line tool execution and
  requires synchronous user input mid-call.
- **Reversibility everywhere** (every write is undoable) sometimes works
  but adds permanent complexity for what's usually a one-time approval
  moment.

The split-tool pattern makes the safety property *part of the API
surface*. The agent literally cannot mutate without first having read
the preview output into the conversation, which means the user has a
text-form record of what they're approving.

**When to skip it:** trivial overwrites the user can undo by re-issuing
the request (e.g. `set_agent_state` overwriting one key); reads that
happen to have side effects too small to surface (e.g. updating a
`last_seen_at` timestamp); operations that are themselves *the user's
intention stated literally* with no scope ambiguity ("delete this one
row" with a primary key already in hand).

**The test:** if asked after-the-fact "did the agent change something
you didn't expect?" could a reasonable user say yes — add a preview.

---

## Step 1 — Synthetic data generator

### Goal

Generate 15 years of realistic UK personal-finance transactions as a safety
net for the real-data path. The agent code should be developable and
demoable against synthetic data without ever needing the private dataset.

### Methodology that worked

**1. Read the existing classifier before generating anything.**

The existing classifier has 60+ regex rules accumulated over years of
manual rule-writing against real bank exports. Every memo the generator
makes up needs to flow through those rules and land in the same bucket the
generator pre-assigned — otherwise the synthetic dataset diverges from the
real one and the "safety net" property is lost. So the merchant pools in
the generator (`RESTAURANTS`, `PUBS`, `CAFES`, …) were lifted directly
from the existing regexes, not invented.

This is the inverse of how a synthetic-data project might naturally start
("I'll generate plausible data and then write a classifier for it"). Doing
it in the other order — classifier first, data shaped to fit — is what
makes the dataset a drop-in stand-in for real exports.

**2. Round-trip verification with a throwaway script.**

After generating, every row was fed through the real `categories()`
function and the output compared to the pre-assigned categories. Anything
that didn't match was either a generator bug or an expected redaction gap.

The throwaway verifier was ~50 lines, ran in seconds, and was deleted
after producing its report. It earned its keep by finding a real bug (see
below) that no amount of eyeballing the CSV would have caught.

**3. Categorise the mismatches before reading the percentage.**

Raw verification said "93.6% agreement." That number is useless on its
own. Bucketing the mismatches by their root cause turned it into:

- ~780 cleaner-name redactions (placeholder vs real names in regex)
- ~180 employer-name redactions (same)
- ~170 cardholder-name redactions (same)
- ~60 loan-reference redactions (same)
- 1 actual bug

Conclusion: 100% agreement on every category not blocked by a
not-yet-applied redaction. The redaction gap is by design and resolves
itself when the classifier is copied into the project repo with redaction
patterns applied.

### Surprises

**The spec was out of date.**
The real classifier had a `Health` main category (Dentist, Eyecare,
General/Medicine, GP) that wasn't in SPEC §4's taxonomy table. Surfacing
this in Step 1 — before writing the scenario tools that consume the
taxonomy — meant a 1-line spec edit instead of a debugging session three
steps later. Spec docs drift; living code is authoritative.

**Float-boundary bug caught by round-trip.**
Some merchants are classified by amount (Pret: `<£5 = café`, `>=£5 =
restaurant`). The generator computed `is_cafe` against the unrounded
float, then wrote `round(amount, 2)` to CSV. An amount of `4.995` was
labelled café but became `5.00` in CSV → restaurant on re-read. One row
in 18,732. Eyeballing the CSV would never have found it; the round-trip
check made it stand out as the single non-redaction mismatch.

**Credit-card double-counting is a tooling contract, not a data fix.**
A £50 dinner paid on a CC shows up twice in raw bank data — once as the
dinner, once as the payoff from the current account. The right answer
isn't to "fix" the synthetic data (real exports look exactly like this)
but to tag both legs of the payoff consistently (`Shopping/CreditCard`)
so downstream tools have a clean exclusion rule. The exclusion contract
lives in the generator's `gen_cc_payments` docstring; the scenario tools
inherit it.

### Reusable patterns

- **Generator → classifier → diff** as a verification pattern works
  whenever you have rules and synthetic data and want to know whether
  the data conforms to the rules without writing a separate validator.
- **Stepwise life events** make synthetic data feel real without
  distributions: a `[(date, value), …]` list and a `latest_at(events, d)`
  lookup is enough to model salary raises, provider switches, mortgage
  refinances, etc.
- **Built-in noise for the agent demo.** ~5% of variable spend uses
  memos the classifier doesn't recognise (real merchants that simply
  aren't in the rules yet). These land in `Missing` and become the
  agent's classification backlog. The dataset ships with built-in work
  for the agent to do, so the demo has something to show on first run.
- **Redaction discipline at the data-generation layer, not later.**
  Personally identifying values (employer names, cleaner names, card
  numbers, loan references, account numbers) are replaced with
  placeholders at the point the synthetic generator emits them. Nothing
  to forget at commit time.

---

## Step 2 — SQLite migration

### Goal

Get categorised transactions into a queryable store. Tools and the agent
loop (Steps 4–5) need to issue SQL against the data; flat CSVs aren't
enough. Schema = SPEC §4 verbatim.

### Methodology that worked

**1. Decouple the classifier from the migration.**

The spec said "migrate.py loads existing CSV exports and runs the
existing categories() function." That phrasing implies coupling — the
migration tool depending on the classifier module. The cleaner design
is to draw the boundary at *already-categorised CSV*: the classifier
emits a CSV, the migration ingests a CSV. They never import each other.

Concrete payoff: the real classifier doesn't have to be copied into the
repo until Step 3 (where it actually needs to be — that's where the
SQLite-first lookup wraps it). Step 2 ships independently against the
synthetic dataset.

**2. Auto-detect format from the CSV header.**

Two formats exist in this project's lifetime: the synthetic schema
(lowercase snake_case, has `data_source` col) and the existing
preprocessed schema (Title Case with spaces, no `data_source`). Asking
the user to pass `--format` would be a foot-gun every time. Inspecting
the header row and choosing a row builder is ~10 lines and removes a
whole class of "I passed the wrong flag" errors.

**3. Validation lives inside the migration tool.**

`migrate.py` ends every run with a validation block: row count, date
range, count by category_main, count of `Missing`, and 5 random
sample Missing rows for spot-checking. The user never has to remember
to "also run the validator" — it's just the last 20 lines of output.

**4. Idempotency scoped to data_source.**

`--replace` deletes only rows matching the `data_source` being inserted.
Real and synthetic data can coexist in one DB during development; replacing
one doesn't touch the other. This is how the demo-mode switch
([SPEC §3.6](AGENT_SPEC.md#36--demo-mode)) actually pays off — the dev
workflow doesn't need separate DB files.

### Surprises

**Python 3.12 silently broke the default sqlite3 date adapter.**
The migration ran fine but emitted `DeprecationWarning: The default date
adapter is deprecated as of Python 3.12`. The fix is one-time module-load
registration of explicit ISO-format adapters/converters for `date` and
`datetime`:

```python
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat(sep=" "))
sqlite3.register_converter("date", lambda b: date.fromisoformat(b.decode()))
sqlite3.register_converter("datetime", lambda b: datetime.fromisoformat(b.decode()))
```

Warnings on stdlib defaults are easy to ignore in the moment and a
pain to debug years later when the default actually gets removed. Better
to fix on first sight.

### Reusable patterns

- **Header-driven format detection** is a cheap alternative to `--format`
  flags whenever multiple CSV layouts have to coexist. The header is
  always there, always free to read.
- **Validation block as the migration's epilogue.** Tools that produce
  data should also report on the data they produced. Splitting it across
  two scripts trains users to skip the second one.
- **Scope destructive flags by partition key.** `--replace` on a flag
  alone is dangerous; `--replace` scoped to `data_source` is safe — you
  can't accidentally wipe real data by re-running the synthetic ingest.

---

## Step 3 — Rule-lookup wrapper

### Goal

Add the SQLite-first lookup layer that lets the agent grow the classifier
over time. Approved rules from the agent's classification flow live in
the `classification_rules` table; the unchanged hardcoded chain in
`bank_statement_parser.py` is the fallback. This is the foundation the
classification tools (Step 4) build on.

### Methodology that worked

**1. Sign off the redaction mapping before touching the file.**

Copying a private file into a public repo is a one-shot operation that's
much harder to undo than to plan. I extracted every personally
identifying token from the source (account numbers, employer names,
cleaner names, cardholder + card number, loan reference, file paths)
and presented a flat 15-row mapping table for explicit approval before
writing a single line. The mapping was the contract; the editing was
mechanical.

The grep-after-write — searching the redacted file for *every* original
token to confirm zero occurrences remained — is the safety belt that
catches anything the mapping missed.

**2. Preserve the original, change only what the spec mandates.**

SPEC §7 says "original script, preserved." The temptation was to also:
fix a pre-existing typo (`self.data_append` instead of `self.data._append`
in `import_barclaycard`), modernise the deprecated `df._append` calls,
strip the unused Excel-import code path, slim the dependency footprint.
None of that is redaction. Doing it all in one pass mixes "make it
publishable" with "improve it" and makes the diff impossible to review
for safety. The redaction commit does redaction only; cleanups can be
their own PRs later.

**3. SQLite REGEXP via a registered Python function.**

SQLite has no built-in REGEXP operator — the syntax `WHERE memo REGEXP
pattern` parses but raises at runtime unless you register a function for
it. The fix is one line at connection open:

```python
conn.create_function("REGEXP", 2, _regexp)
```

The Python function does the actual matching. Errors in user-supplied
patterns return `False` instead of raising, so a single malformed rule
can't crash the whole lookup. The same `re.IGNORECASE` flag the
hardcoded chain uses is applied here for consistency.

**4. The end-to-end round-trip is the contract test.**

The Step 1 round-trip verifier reported 1,193 mismatches against the
*unredacted* classifier, all of them in the four known redaction
buckets (cleaner, salary, cardholder, loan). After applying the
redaction mapping to a copy of the classifier and re-running the same
verification against this repo's copy: **100.00% agreement, 0
mismatches across 18,780 rows.** That number is the contract holding.
If a future redaction edit ever breaks the mapping, the same script
catches it immediately.

### Surprises

**The hardcoded chain is more nuanced than it looks.**
Re-reading the source for redaction surfaced details I'd missed in
Step 1: a price-based Pret rule (`<£5 = café, ≥£5 = restaurant`), a
joint memo pattern `MORRISONS PETROL` that lands as Supermarket because
`MORRISONS.*` matches before any petrol rule, a `BARCLAYS PRTNR FIN`
branch that maps to `kitchen/bathroom` with details `wren repayment`.
None of this is documented anywhere except in the rule order itself.
Step 4 (LLM-powered `suggest_classification`) will need to read this
file as context so it doesn't suggest rules that collide with the
hardcoded chain in surprising ways.

**`if __name__ == "__main__":` blocks survive copy-paste, dependencies don't.**
The redacted file still has the full Budget-class import pipeline and
the argparse main block — both pull in `dotenv`, `openpyxl`, and the
`pandas` Excel writer. For the agent's purposes only `categories()`
and the `set_up_*` helpers are needed. Preserving the rest was a
deliberate fidelity choice (per SPEC §7), but it does mean the public
repo now depends on libraries that don't pull their weight for the
agent. Worth resolving when `requirements.txt` lands — likely
extracting the Budget class into a separate file would let the
classifier sit on `pandas` alone.

### Reusable patterns

- **Mapping-table-as-contract** for any private→public migration.
  Approving the mapping is approving the diff in advance; the writing
  step becomes mechanical and the review surface is much smaller.
- **Post-edit token grep** as the redaction safety belt. Cheaper than
  reviewing the whole diff, and catches the case where one of N
  occurrences was missed.
- **Pre-existing test that proves the new layer didn't regress.** The
  Step 1 round-trip verifier was originally tooling for the synthetic
  generator. In Step 3 it doubles as the contract test for the
  redaction mapping — same script, different question, same answer
  format. A regression-watching tool you wrote once and keep
  recycling tends to pay back faster than tools that prove one thing.

---

## Step 4 — Tool implementations + Docker

### Goal

Build the 11 tools the agent will use (state, classification, scenarios),
plus the infrastructure they sit on: tool registry, Anthropic API helpers,
and the Docker container that everything runs in. Each tool needs to be
independently verifiable so the Step 5 agent loop is a thin orchestrator
rather than a place where bugs hide.

### Methodology that worked

**1. Plan before building, and force four design decisions early.**

The plan up-front (model routing, rule-application flow, fixed/discretionary
heuristic, scope of scenario tools) was four AskUserQuestion items that
took 60 seconds to ask and saved an indeterminate amount of rework. The
user's answer to "two-step rule application" in particular changed the
API surface — `add_classification_rule` became two tools
(`preview_rule_application` + `apply_classification_rule`). Catching that
in planning rather than mid-implementation meant the SPEC, the
tool_registry, the inline tests, and the system-prompt guidance all
landed coherent.

**2. Mirror the working pattern from a sibling project.**

`claude_helpers.py` is a direct adaptation of `mistral_helpers.py` from
the rag-pipeline project — same `call_with_retry(func, *args, max_retries,
base_delay, **kwargs)` signature, same exponential backoff with
`Retry-After` honouring, just different exception types. Copying a known-
good pattern means the retry semantics are battle-tested before the agent
ever runs. The five-line decision: "what we did before, but for this SDK."

**3. Co-locate JSON schemas with the functions they describe.**

Each tool module exports `SCHEMAS` next to the function bodies; the
registry imports them and asserts every schema name has a matching
callable. Drift between "what the API thinks the tool does" and "what the
function actually does" becomes impossible at import time — change the
function name and forget the schema, and `tool_registry.py` refuses to
load.

**4. Two-step destructive operations.**

`preview_rule_application` answers "how many?" without writing;
`apply_classification_rule` writes. Splitting them means the agent's
conversation flow naturally has an approval gate built in. This is the
first concrete instance of a pattern that's now a project-wide default —
see the
[Preview-before-apply](#preview-before-apply-for-destructive-agent-tools)
cross-cutting decision.

**5. Inline smoke tests that mutate, then clean up.**

The classification.py test inserts a NETFLIX rule, applies it (mutating
24 rows), verifies the count matches the preview, then restores the rows
and deletes the rule. Snapshotting the affected IDs *before* the mutation
and using them to drive the restore means the test is idempotent — it
leaves the DB exactly as it found it, runnable any number of times back-
to-back without an external reset.

### Surprises

**SQLite expressions don't get PARSE_DECLTYPES converters.**
`SELECT MAX(date) FROM transactions` returns a string, not a date,
because converters apply only to declared columns. The fix is
`date.fromisoformat(row["d"])`. Caught by the first scenarios.py smoke
test — and worth flagging because every other date-column read in the
project does come back as a date, so the inconsistency is easy to miss
until something explodes.

**Docker file-mounts on a missing host path silently create a directory.**
The first `docker-compose.yml` had `./finance.db:/app/finance.db`. It
worked because the file existed from Step 2 — but a fresh checkout would
have had Docker create a *directory* called `finance.db`, breaking SQLite
silently. Fix: move `finance.db` inside `data/` and mount the directory.
This is the kind of bug that doesn't surface until someone else clones
the repo three months later. Catching it before commit was a function of
spending two extra minutes wondering "what happens on a fresh checkout?"

**The image bakes the code at build time, so source edits need a rebuild.**
First migration after the path move kept writing to `/app/finance.db`
inside the container — because the container was running the pre-edit
code from `COPY . .` at build time. Rebuilding (`docker compose build`)
picks up the new code. The alternative — bind-mounting source for live
dev — re-introduces host/container Python-version drift, which is exactly
what Docker is here to prevent. Decision: live with the rebuild cost,
since builds are ~10s after deps layer is cached.

**Embedded LLM call as a tool result is its own architectural shape.**
`suggest_classification` is a tool the *outer* agent loop calls, but
internally it makes its own API call to Haiku 4.5. The agent loop never
knows another model was involved — it just gets a structured dict back.
This is a clean pattern for cost-routing (the SPEC §3.3 case) but it
also generalises: any tool that's really "ask a cheaper model for a
draft" can hide that fact from the orchestrator.

**Live LLM validation surfaced real taxonomy gaps, not bugs.**
Running `suggest_classification` against 5 distinct memo patterns showed
Haiku staying strictly within the taxonomy (no hallucinated categories)
but picking obviously-wrong sub2s for merchants the existing taxonomy
doesn't cover: NETFLIX, DISNEY+, APPLE.COM all landed in
`Leisure/subscription/music` because there's no `video` or `streaming`
sub2. TRAINLINE.COM landed in `Transport/taxi` because there's no
`rail`. AIRBNB landed in `Leisure/entertainment` because there's no
`Travel` main. The behaviour is correct — "stay in the taxonomy" is the
guardrail we want — but it reveals that the inherited taxonomy reflects
this user's historical real spending, not a forward-looking superset.
Phase 2 (when the hardcoded chain migrates into the rules table) is the
natural time to evolve sub-categories with the agent's help.

### Reusable patterns

- **AskUserQuestion before plan, ExitPlanMode after.** Four design
  questions in plan mode beats four questions per implementation phase.
- **Co-locate schema with function**; assemble the registry from the
  per-module exports. Single source of truth, drift-impossible.
- **Two-step destructive operations** (preview → apply) where the
  agent is in the loop — promoted to a [project-wide
  default](#preview-before-apply-for-destructive-agent-tools).
- **Tool-use with forced `tool_choice`** as a way to get guaranteed
  structured output from a model call — more robust than JSON-mode
  prompting because the schema is part of the request.
- **Mount the directory, not the file**, for Docker bind mounts. Path
  existence is no longer a fresh-checkout footgun.
- **Lazy module-level connection**, with a `reset_*` helper for tests.
  The cost of opening the SQLite DB on every call would add up across
  a long agent session; the lazy global pays the open cost once.

---

## Step 5 — Agent loop

### Goal

Wire the 11 tools, Sonnet 4.6, prompt caching, and a CLI into a working
conversational agent. SPEC §6 had the shape; the work was turning it
into ~600 lines of code that actually run inside the Docker container
and feel coherent across multi-turn conversations.

### Methodology that worked

**1. Three thin modules, separated by I/O concern.**

`agent/agent.py` is pure logic (Session, run_turn, build_system_prompt) —
no printing, no file I/O, no SDK objects leaking out. `agent/cli.py` is
the RichRenderer (display only). `agent/transcript.py` is the JSONL
writer (persistence only). They share a tiny Renderer protocol so the
core loop can be tested with a `SilentRenderer` mock that returns and
records nothing. The total cost of the abstraction is ~5 methods on a
Protocol class. The benefit is that all deterministic tests live inside
`agent.agent` and never touch the terminal or the filesystem.

**2. The system prompt is a product. Treat it that way.**

The static block of the system prompt (~2,700 chars) carries four
load-bearing things: the role, the preview-before-apply contract, the
state-store boundary rule, and the taxonomy-honesty instruction. Each
of those four came from a specific architectural decision documented
elsewhere in this repo (SPEC §3.1, §3.4, §6, [Preview-before-apply
cross-cutting decision](#preview-before-apply-for-destructive-agent-tools)).
Writing the prompt was an exercise in *projection* — squeezing each
decision into 1–3 sentences that the model can actually follow. The
empirical validation: a 3-turn conversation followed all four
constraints without further nudging.

**3. The agent caught a bug I'd missed in scenario.model_scenario.**

First end-to-end test, turn 2: the model output `monthly_payment_delta:
£30,833/month` for a 2%→4% rate change on £185k. The agent's response
opened with "⚠️ The model has returned a payment delta of £30,833/month
which is clearly wrong — that's what simple interest on £185k at 2%
would be annually, not monthly." Root cause: the tool expected rates as
decimals (0.02) but the model passed percentages (2). Two takeaways:

  - The "surface uncertainty" prompt instruction had real teeth — the
    agent didn't just render the wrong number.
  - The tool was too brittle. Fix: accept both forms via `if rate >= 1:
    rate /= 100`. Now both decimal and percentage inputs work.

The deeper lesson: **end-to-end LLM testing isn't optional, even for
deterministically-tested tools**. The bug was in correct-looking
Python that passed every unit-style test in Step 4. Only a live model
actually using the tool surfaced the API mismatch.

**4. Prompt caching works exactly as the SPEC promised — and hits on turn 1.**

The first test session showed turn 1 cache_read: 0, which suggested
caching only kicked in from turn 2 onwards. The real production session
(2026-05-31, see worked example below) showed something better:
`cache_read: 3,015` on turn 1 itself. The system prompt and tool schemas
are cached on the very first API call of a session — by turn 2 those
same 3,015 tokens are served at $0.30/MTok instead of $3.00/MTok. On a
3-turn mortgage scenario conversation the cache saved roughly $0.025.
The cost of the abstraction: ~5 lines of `cacheable_text_block(...)`
wrapping in `build_system_prompt`. Worth it at any session length.

### Surprises

**Module patching gets confused when `__name__ == "__main__"`.**
First version of the deterministic dispatch-error test patched
`agent.agent.call_with_retry` — but when invoked as `python -m
agent.agent`, the running module's `__name__` is `__main__`, not
`agent.agent`. There were two module objects in `sys.modules` (the
original from `python -m`, and a second one created by `import
agent.agent as _aa`), and my patch hit the wrong one. The `run_turn`
function actually being called resolved `call_with_retry` from the
`__main__` namespace, which wasn't patched. Fix: patch
`sys.modules[__name__]` instead. Tedious to debug, obvious in
retrospect.

**rich interprets `[label]`-prefixed strings as markup.**
`console.print(f"[tool:bad_thing] failed")` lost the prefix entirely
because rich parsed it as a (malformed) markup tag. Wrap in `Text(...)`
to opt out. Generic principle: any user-supplied or computed string
going through rich's `print()` should either be escaped or wrapped in
`Text()` to avoid silent loss.

**Multi-tool turns require *one* user message with *all* tool_results.**
The Anthropic API is strict about this: emit several `tool_use` blocks
in one assistant response, and the next user message must contain a
`tool_result` block for each one. A common bug shape would be
dispatching each tool and immediately appending a tool_result message,
then calling the API again — that breaks the contract and the API will
422 you. The loop in `run_turn` collects all tool_results from one
iteration into a single user message before the next API call.

### Reusable patterns

- **Renderer protocol + SilentRenderer** for any agent loop. Makes the
  loop testable without `mock.patch('sys.stdout')` hacks.
- **System prompt as projection of architectural decisions.** Every
  load-bearing instruction in the prompt should have a SPEC reference;
  every SPEC contract that affects runtime behaviour should appear in
  the prompt. The two stay in sync because they're written together.
- **End-to-end live tests catch a different class of bug** than
  deterministic unit tests. A few cents per test run is cheap insurance
  against API-shape mismatches that the type checker can't see.
- **Patch `sys.modules[__name__]`** when monkey-patching for tests in a
  module that may run as either `__main__` or its dotted name.
- **Cap tool_result size** before sending back to the model. A scenario
  query that returns 100 categories shouldn't blow next-turn context.
  Trimming for the model's view; full result still in the transcript.



---

## Phase 1 — What shipped

*Five steps, roughly three weeks of build time.*

| Component | What it is |
|-----------|------------|
| `data/synthetic/generate_synthetic.py` | 15 years of realistic UK transactions, classifier-shaped |
| `db/migrate.py` | CSV ingestion into SQLite, format auto-detection, idempotent |
| `classifier/rule_lookup.py` | SQLite-first lookup (hardcoded chain as Phase 1 fallback; removed A1) |
| `agent/tools/` | 11 tools: 6 classification, 4 scenario, 2 state (grew to 13 post-A3 + 15 post-C2) |
| `agent/agent.py` + `cli.py` + `transcript.py` | Renderer-protocol loop, Rich display, JSONL logging |
| `Dockerfile` + `docker-compose.yml` | python:3.13-slim, non-root, data/ + logs/ bind mounts |

**What worked from the original spec:** model routing (Sonnet + Haiku), prompt caching, hybrid session/cross-session memory, preview-before-apply pattern, JSONL transcript format, `data_source` column for demo/real switch.

**What changed vs the original spec:** the hardcoded fallback chain was always meant to be temporary (A1 removed it). The Renderer protocol wasn't in the original spec — it emerged during Step 5 and became the load-bearing abstraction for every subsequent front-end.

---

## Retrospective — cross-cutting observations from Phase 1

*These insights emerged from the first live production session, after Step 5 was complete. They require the agent loop to already exist to make sense.*

**The observation:** reading a transcript entry like this one —

```json
{"type": "assistant", "content": [
  {"type": "tool_use", "name": "get_spending_summary", "input": {"months": 60, "category_main": "Transport"}},
  {"type": "tool_use", "name": "get_spending_summary", "input": {"months": 60, "category_main": "House"}}
]}
```

— it looks like Claude spontaneously knew what tools existed and chose to
call two of them. There's no visible context explaining how. This is a
log format issue, not a mystery.

**What actually gets sent on every API call:**

```
client.messages.create(
    system = [system_prompt, agent_state_snapshot],   ← NOT in the log
    tools  = [schema_1, schema_2, ..., schema_11],    ← NOT in the log
    messages = [...conversation turns...]              ← THIS is the log
)
```

The transcript records only the `messages` array — the evolving
conversation. The `system` parameter (system prompt + agent state
snapshot) and the `tools` parameter (full JSON schema of all 13+
tools) are sent on every single API call but are never written to the
log because they are structural inputs to the API call, not
conversation events. The system prompt is static per session; the
agent_state snapshot can change between turns (any `set_agent_state`
call updates it for the next turn). Neither belongs in the per-event
log — the right place to inspect them is the codebase
(`build_system_prompt`, `agent_state` table), not the transcript.

**The `cache_read` tokens are the evidence they were sent:**
In the `usage` line for turn 1 of the mortgage session:

```json
{"type": "usage", "tokens": {"in": 358, "cache_read": 3015, "cache_creation": 423}}
```

`cache_read: 3015` means 3,015 tokens were served from cache. Those
tokens *are* the system prompt and tool registry — they were sent, read
by the model, and happened to hit the cache. The log doesn't record
their content; it records the cost proof that they arrived.

**Implication for debugging:** if the agent calls a tool unexpectedly,
or fails to call one it should, the root cause will never be visible in
the transcript alone. Look at: (1) the tool schema description — how the
model was told the tool works; (2) the system prompt — whether the
relevant constraint was stated; (3) the agent_state snapshot — whether
missing context changed the reasoning. The transcript shows the
*what*; the static config explains the *why*.

**Interview framing:**
"The transcript is a record of the conversation, not a record of the
API call. Every call also sends the system prompt and the full tool
registry — the model only knows about tools because we include their
JSON schemas on every request. If you read a transcript and wonder how
Claude knew what tools existed, look for the `cache_read` token count
in the usage line — those cached tokens are the tool schemas doing
their job invisibly."

---

### The agent loop in production — reading a real session

**Reference:** `AGENT_ARCHITECTURE.html` — four diagrams
showing the loop, classification HITL flow, scenario use case, and
data architecture. Open in any browser; supports dark mode.

The mortgage rate session (2026-05-31, logged in
`logs/session_20260531T124548Z.jsonl`) is a clean worked example of
all three architecture decisions working together in one conversation.

**Turn 1 — Multi-tool batching and honest limitation surfacing**

User asked about car spend over 5 years. The model responded with
*two* `tool_use` blocks and zero text — it batched `Transport` and
`House` queries in the same response because the question "was
maintenance the most expensive?" implied both categories might be
relevant. The 5-second gap between user message and tool_use response
is Sonnet reasoning time. The under-1-second gap between tool_use
and tool_results is Python running SQLite queries.

The final response then did something architecturally important: it
correctly identified that `get_spending_summary` returns totals across
the window, not year-by-year breakdowns, and told the user this
directly rather than fabricating a trend. This was the "surface
uncertainty" prompt instruction having real effect.

```
turn 1 cost: $0.0117  (cache_read: 3,015 — system prompt + tools cached)
```

**Turn 2 — Three simultaneous tool calls, null state, then a write**

The mortgage rate question triggered three tool calls in one response:
`get_income_summary`, `classify_fixed_vs_discretionary`, and
`get_agent_state("mortgage_balance")`. The state call returned `null`
— the balance had never been stored. The model inferred ~£852k from
the current £1,420/month payment at 2% and ran the scenario anyway.

After the scenario completed, the model called `set_agent_state` three
times — storing the rate change date, current rate, and new rate. It
learned from the conversation and persisted what it learned without
being told to. This is the cross-session knowledge store operating
correctly: the agent identified durable facts and stored them with
rationale.

```
turn 2 cost: $0.0168  (cache_read: 4,446 — growing context, still cached)
```

**Turn 3 — Correction accepted, re-run, cross-session write**

User corrected the balance to £400k. The model immediately re-ran
`model_scenario` with the corrected figure and then called
`set_agent_state("mortgage_balance", 400000)` — writing the correct
value for future sessions. The gap was now £667/month instead of
£1,420/month, and the response correctly identified that a 15% trim
on groceries and eating out would close it entirely.

```
turn 3 cost: $0.0268  (cache_creation: 4,224 — session history now staging for cache)
```

**The cost trend is architectural, not accidental:**
$0.0117 → $0.0168 → $0.0268. The messages array grows each turn. Even
with caching, the non-cached portion (new tool results, new messages)
compounds. A session that runs to 15–20 turns will see this trend
continue. The prompt caching holds the system prompt cost flat; the
conversation history cost grows linearly. For very long sessions,
periodic summarisation of older turns into agent_state entries (rather
than keeping every turn in the messages array forever) is the natural
next optimisation.

**Total session cost: $0.055** — about 4p for a three-turn mortgage
scenario conversation with five tool calls, two model hops (Sonnet for
the loop, implicit), and three state writes. That's the cost of running
this as a real personal finance tool.

---

## Phase 2 — Hardening and extension

*Ten tickets shipped: B2, A1, A2, A3, C4, B1, D2, D2-followup, /admin/stats, C2, B3, C1.*

**Phase 2 goal:** make Phase 1 production-quality and extend its capabilities across three tracks:
- **A\*** — migrate rules from Python to SQLite, expand the taxonomy, give the agent a tool to grow it further
- **B\*/D\*** — pytest suite, dispatch-layer code gate, transcript replay and web toggle
- **C\*** — web UI, operator stats, Batch API cost lever, real-data ingestion

### Phase 2 naming convention

Letters encode the track: **A\*** classifier · **B\*** hardening/testing · **C\*** production capabilities · **D\*** developer experience. Tickets shipped in dependency order, not letter order (C4 before B1 because the public URL made B1 urgent).

---

## B2 — pytest adoption

### Goal

Convert the inline `__main__` smoke blocks in 6 modules into a real pytest
suite *before* A1/A2 begin to mutate the classifier path. Preserve the
deterministic-vs-LLM split (the `RUN_LLM_TESTS=1` env-var convention) and
the docker-first invocation discipline. CI was deferred — landing tests
first, treating CI as its own follow-up.

### Methodology that worked

**1. Canary one test before porting the rest.**

`test_state.py` went first, alone, because it exercises the full fixture
chain — `seed_db` builds a synthetic DB once per session, `tmp_db` copies
it per-function and monkey-patches `db.database.DB_PATH`. If the
monkeypatch didn't reach the tools that import `open_db` directly, every
later test would have failed for the same reason. Running one file in
isolation proved the chain before scaling. The cost of being wrong was a
single test file, not nine.

**2. Two-tier fixture: session-scoped seed + per-function copy.**

The synthetic CSV is 18,780 rows; `migrate.ingest()` takes ~2s. Building
it per-test was wasteful (paying ~2s × N), building it once per session
was risky (writes leak between tests). The shape that worked:

```python
@pytest.fixture(scope="session")
def seed_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seed") / "seed.db"
    with database.open_db(path) as conn:
        ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=False)
    return path

@pytest.fixture
def tmp_db(seed_db, tmp_path, monkeypatch) -> Path:
    db_copy = tmp_path / "finance.db"
    shutil.copy(seed_db, db_copy)
    monkeypatch.setattr("db.database.DB_PATH", db_copy)
    return db_copy
```

`shutil.copy` of an 18k-row SQLite file is ~30ms. The whole suite (47
tests, no LLM) runs in ~8s. The plan's `<10s` sanity gate held.

**3. Monkeypatch over the `_this = sys.modules[__name__]` dance.**

`agent/agent.py`'s old `__main__` block patched the running module by
walking `sys.modules` (because `__main__` blocks have no fixture system).
The pytest version replaces that with one line per binding:

```python
monkeypatch.setattr("agent.agent.call_with_retry", lambda func, *a, **kw: fake_create(**kw))
monkeypatch.setattr("agent.agent.get_client", lambda: _FakeClient())
```

Same surgery, but with automatic teardown restore. The `_this` indirection
was scar tissue from not having pytest available.

**4. LLM-gated tests as a marker + auto-skip hook.**

The previous convention used `os.environ.get("RUN_LLM_TESTS") == "1"` as
an inline gate. Pytest's idiom is a marker. Preserved the same env-var so
nothing in CLAUDE.md or docker-compose.yml had to change:

```python
# pytest.ini
markers =
    llm: hits the Anthropic API; skipped unless RUN_LLM_TESTS=1

# tests/conftest.py
def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LLM_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="LLM test; set RUN_LLM_TESTS=1 to run")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
```

One env-var entrypoint, no `--run-llm` CLI flag — fewer ways to forget.

**5. Crash-only smoke for the CLI renderer.**

`agent/cli.py` had no assertions in its old `__main__` (eyeball-only).
Snapshot tests against `rich.console.Console` are brittle across terminal
widths and colour modes. The middle ground: instantiate `RichRenderer`,
call each public method with mock blocks, assert no exception. Catches
broken constructors and missing methods; ignores prettiness. `python -m
agent.cli` stays as the manual visual check.

### Surprises

**Docker layer cache bit me twice.** The image `COPY . .` step caches by
file content at build time. My first build kicked off *before* I'd written
`tests/`, so the image had no test files and pytest reported "file or
directory not found". Easy fix (rebuild) but a reminder: don't kick off a
long Docker build in parallel with adding files the image needs to see.

**An orphan `print()` survived a multi-line `Edit` because the original
`__main__` block was one line longer than I'd matched in `old_string`.**
The Edit tool's exact-string requirement saved me from corrupting more
files, but the failure mode (one-line orphan, `IndentationError` deferred
to import time) was diagnosable only from the next test run. Lesson: when
deleting a big trailing block, prefer `Read` the last 10 lines first to
confirm the exact tail.

### What was deferred (intentionally)

- **CI (GitHub Actions).** Backlog flagged this as part of B2. Pulled out
  to a separate follow-up because (a) there's no `.github/` dir yet, (b)
  the secrets story for `ANTHROPIC_API_KEY` in CI is a separate decision,
  (c) landing tests locally first lets CI be one focused PR.
- **Scripted-conversation pytest fixture.** Today the LLM end-to-end test
  is a single linear script. A reusable fixture that records and replays
  multi-turn conversations against mock responses (for prompt-regression
  catching without LLM cost) is Phase 3 territory.

### Hand-off to A1

The next chosen item adds `classification_rules` table reads as the
primary lookup path with the hardcoded chain as fallback. The pytest
shape it needs:

- `test_rule_lookup.py` — exists today only in the migration target. New
  cases: table-first hit, table-miss-with-chain-fallback, table-and-chain
  agreement on the synthetic dataset (the round-trip verifier).
- Existing `test_classification.py` should keep passing untouched: the
  preview/apply contract is independent of where rules live.

### Cost

Final pytest suite: **47 deterministic tests in ~8s**, 2 LLM tests
(`@pytest.mark.llm`) opt-in via `RUN_LLM_TESTS=1` for ~$0.10/run.

---

## A1 + A2 — rules into table + taxonomy expansion

### Goal

Migrate the hardcoded `if/elif` chain in `bank_statement_parser.py:categories()`
(~40 regex rules) into rows of `classification_rules`, and extend the
taxonomy with `Travel`, `Transport/rail`, `Leisure/subscription/video` so
the agent has somewhere correct to put NETFLIX / TRAINLINE / AIRBNB /
DISNEY+ when it processes the Missing backlog.

### Methodology that worked

**1. Round-trip verifier as the contract.**

The whole migration was unsafe-by-default — losing one rule or
mis-ordering them would silently mis-classify thousands of rows. The fix
was to write the verifier FIRST (before deleting any old code), get it to
100% agreement on the unchanged synthetic CSV, and only then delete the
hardcoded chain. The test exists at
[tests/test_round_trip.py](../tests/test_round_trip.py) — read every
synthetic row, classify via the new table-driven path, assert match
against the CSV's pre-assigned categories. First green run = "the
migration didn't lose anything"; future red run = "you just broke a
rule, fix it before merging".

This is a different shape from B2's pytest suite — B2 was testing
*behaviour*, this is testing *equivalence to a known-good baseline*.
Both have a role.

**2. Schema-extends-for-conditional-rules over special-case code.**

Three of the ported rules condition on more than memo (cash-from-current,
mortgage-from-current, PRET under £5). Two options were on the table:
keep a tiny 3-rule hardcoded chain alongside the table for those, or
extend the schema with optional columns and put EVERYTHING in the table.
We picked the schema extension — four nullable columns
(`account_match`, `type_match`, `amount_min`, `amount_max`) cover the
existing cases and any future agent-added conditions. "Table is the
source of truth" became a real invariant, not a 95%-true approximation.

Convention chosen: `amount_min` inclusive (`>=`), `amount_max` exclusive
(`<`). Mirrors Python's `range(start, stop)`. The original chain used
`abs(amount) < 5` so a £5.00 PRET went to restaurant; our seed encodes
this as `amount_max: 5` (NOT 4.99) and the SQL matches exactly. Worth
documenting because the off-by-one is otherwise the kind of bug that
takes an afternoon to find.

### Surprises

**`re.match` vs `re.search` semantics.** The first round-trip run after
seeding failed because SQLite's REGEXP (using `re.search`) matched THE
ECONOMIST against the gas/electric pattern `E.ON|EDF ENERGY` — `E.ON`
matches "E" + any char + "ON", which appears mid-string in
"THE ECONOMIST". The original hardcoded chain used `re.match`
(start-anchored), and the existing rule patterns relied on that
implicitly — patterns without `.*` prefix were start-anchored, patterns
with `.*` opted into "anywhere". Fix: change `db/database.py:_regexp`
from `re.search` to `re.match`. This preserved the original semantics
across all patterns without rewriting any of them. Documented in the
`_regexp` docstring so future agent-added rules know the convention.

Lesson: when adopting an existing regex chain into a new evaluation
engine, run the round-trip verifier as the very first step after the
seed lands — bugs at the regex-engine level are invisible to
spot-checks but jump out the moment you compare every row.

**Migration order matters more than I expected.** PRET café (amount<5)
must come BEFORE the restaurant rule in `RULES_SEED`, otherwise the
restaurant rule's broader memo pattern fires first. Same for ^MTG
mortgage before any generic memo match that could swallow it. The
ordering is brittle but inspectable — a future test could assert
"every rule's pattern doesn't match an earlier rule's example memo"
to catch reorderings, but the round-trip verifier already does this
implicitly.

### The A2 / "Missing backlog" tension

The Missing backlog is a deliberate demo feature — the agent's
classification loop needs unclassified rows to chew on. A2 adds the
right categories (Travel, rail, video) but also threatens to empty the
backlog if it adds matching seed rules for NETFLIX/AIRBNB/TRAINLINE
themselves.

Resolution: split "what's in the taxonomy" from "what gets
auto-classified by seed rules". Generator emits baseline pre-classified
rows (BOOKING.COM → Travel, AVANTI WEST COAST → Transport/rail, NOW TV
→ Leisure/subscription/video) so `list_categories()` reports the new
mains/subs. NETFLIX/AIRBNB/TRAINLINE/DISNEY+ stay in `NOISE_MEMOS` with
no matching seed rules, so they keep landing as Missing. The agent
demos by classifying them — *into the new taxonomy that now exists*.

This shape generalises: when adding a category, decide whether existing
"unknown" merchants should automatically slot into it or remain Missing
for the agent loop. The demo angle and the cleanup angle pull in
opposite directions; the right answer is context-dependent and worth a
conscious choice.

### Hand-off to A3

A3 (`extend_taxonomy` tool) is now a small follow-up. The shape:
- Agent tool that takes `(main, sub, sub2)` + an optional list of
  example merchant patterns to seed as rules.
- Preview/apply pair (per the project default): preview shows "this
  would add a new category and create N rules"; apply inserts a new
  row in a `taxonomy_extensions` audit log + writes the new rules with
  `added_by='agent'`.
- Round-trip verifier still passes because `added_by='seed'` rows are
  unchanged; new rows are agent-added.

### Cost

A1 + A2 combined edit: ~50 minutes of model time, $0 in API costs
(round-trip verifier is deterministic). The final LLM-included test
run cost ~$0.10. Total: ~$0.10 for the whole bundle.

---

## A3 — extend_taxonomy tool

### Goal

Make taxonomy growth a first-class agent capability. A1+A2 left the
agent able to mutate the rules table (via `apply_classification_rule`)
without any explicit signal that a new `(main, sub, sub2)` tuple was
being introduced. A3 adds a paired preview/apply tool that validates
the tuple is genuinely new and rejects 0-match patterns, so the new
category is guaranteed to land on actual data.

### Methodology that worked

**1. Thin wrappers over the existing preview/apply functions.**

The temptation was to reimplement everything. Instead `preview_taxonomy_extension`
just calls `preview_rule_application` after a validation check, and
`apply_taxonomy_extension` just calls `apply_classification_rule`. Two
extra functions, ~30 lines of net new code, zero schema change. The
SCHEMAS entries make them addressable from the agent loop; everything
else is reuse.

The pattern: when adding a more-specific variant of an existing
capability, the new tool is a *validator-wrapper*, not a fork. Same SQL
path, same write semantics, different validation boundary at the front.

**2. Reject-at-preview as the load-bearing invariant.**

The fork in the plan was what to do when a new tuple's pattern matches
0 Missing rows. Three options were on the table: reject, allow with
phantom category, allow + extend `list_categories()` to union with
the rules table. Picked reject — it keeps the invariant "the live
taxonomy reflects actual data" intact. The cost is a slightly chatty
agent flow (if the user asks to add a category for a merchant that
isn't yet in Missing, the agent has to say "no matches; come back
when one arrives or use a broader pattern"), but the gain is no
phantom entries to clean up later.

This is a tradeoff worth being explicit about: invariant-preserving
strictness often produces slightly worse UX in unusual cases and
materially better behaviour everywhere else. Same reasoning that drove
the preview-before-apply default.

**3. Test the rejection paths explicitly.**

Easy to write the happy-path test and forget the rejection paths.
`tests/test_classification.py` got three tests for the new flow's
guards: rejects-existing-tuple, rejects-zero-matches, apply-also-rejects-existing.
Each one asserts the specific error message substring the agent will
see — if a future refactor changes the message, the test catches it and
prompts an explicit update to the agent's expected error vocabulary.

### Surprises

**`re.search` → `re.match` was a one-line classifier prompt fix too.**
A1 changed SQLite's REGEXP backing function from `re.search` to `re.match`
to preserve the original chain's start-anchored semantics, but the
`suggest_classification` system prompt at
[agent/tools/classification.py:_SYSTEM_PROMPT](../agent/tools/classification.py)
still told Haiku "Python's re.search applied case-insensitively". The
fix is one paragraph in the prompt — but if A3 hadn't been the next
pass, Haiku would have kept producing patterns under the wrong mental
model indefinitely, silently failing on memos that don't start with the
merchant name. Lesson: when changing a regex engine's semantics, grep
for every place the old semantics were documented to callers (model
prompts included).

**Synthetic data picked the test merchant.** The synthetic generator's
`NOISE_MEMOS` pool includes APPLE.COM/BILL, which doesn't match any
seed rule, so it reliably lands in Missing across the synthetic CSV.
That made `("Shopping", "digital", "apps")` + `.*APPLE\\.COM` the
perfect test fixture for the new-taxonomy-entry flow — no SQL fixture
setup needed, just point the test at what's already there. Worth
noticing when designing synthetic data: pre-seeded "unmatched" merchants
double as test fixtures for any future tool that touches Missing.

### Hand-off

A3 is the natural endpoint of the A* track for now. Remaining backlog:
B1 (code gate for apply_classification_rule — would also protect
apply_taxonomy_extension once added), C1 (real-data ingestion), C2
(Batch API for Missing classification), C3 (session history
summarisation), B3 (slim down bank_statement_parser), CI residual,
D1–D3 polish.

If the agent starts producing patterns that under-match (i.e., the prompt
fix didn't fully land), the next finding goes here.

### Cost

A3 edit: ~30 minutes of model time, $0 deterministic test cost, ~$0.10
for the LLM-included run. Total: ~$0.10.

---

## C4 — web UI

### Goal

Recruiter-clickable demo hosted on the M720Q. Public URL, synthetic
data only, ephemeral sessions, no real-data risk. Shows the agent
reasoning through tool calls in real time — that visible reasoning IS
the demo.

### Methodology that worked

**1. The Renderer protocol from Step 5 paid off here.**

The agent loop talks to a `Renderer` for every visible event
(`show_tool_call`, `show_tool_result`, `show_assistant_text`,
`show_usage`, `show_error`). The CLI implementation prints via Rich;
the web implementation pushes events into an asyncio.Queue. Same loop,
zero changes to `run_turn` or the tools. The Step 5 design decision
"render via a protocol, not via prints" turned out to be the load-bearing
abstraction for the entire UI. If `run_turn` had been print-coupled,
this whole project would have needed a refactor pass before C4 could
begin.

Lesson: when you write code that emits to a user, an early "Renderer
takes structured events" interface costs nothing extra in the first
implementation and unlocks every alternative front-end forever.

**2. ContextVar for per-session state.**

The agent's tools call `open_db()` with no arg, reading a module-level
`DB_PATH`. For multi-user web hosting, every visitor needs their own DB,
but threading an explicit `db_path` through `run_turn` and every tool
function would be a refactor of dozens of call sites.

Fix: a `SESSION_DB_PATH` ContextVar that `get_connection()` reads as
fallback (after explicit arg, before module default). FastAPI handler
sets it before invoking the agent. ContextVars propagate across
`asyncio.to_thread` boundaries automatically in Python 3.10+, so the
synchronous `run_turn` running in a worker thread still sees the
per-request value. Verified explicitly with
`tests/test_database.py::test_session_db_path_propagates_across_to_thread`
— the kind of behaviour that's easy to assume and expensive to be
wrong about.

Same pattern would work for any per-request state we add later (current
user, request ID for tracing, etc.).

**3. Thread→async bridge via `loop.call_soon_threadsafe`.**

`run_turn` is synchronous (must stay that way so the CLI still works).
The web turn handler runs it in `asyncio.to_thread`. The renderer's
callbacks fire from that worker thread but need to put events on an
`asyncio.Queue` that the FastAPI route reads. `asyncio.Queue` is not
thread-safe.

The stdlib-clean bridge: `loop.call_soon_threadsafe(queue.put_nowait,
event)`. Each callback schedules the put on the event loop. The async
generator drains the queue and yields SSE strings. A `_SENTINEL` object
gets pushed when `run_turn` returns (from its `finally` block, so it
fires even on exception) to terminate the generator.

Subtle race: the `finally` block runs BEFORE the function actually
returns to `to_thread`, so the sentinel arrives at the consumer while
the task is technically still running. Calling `.result()` on the task
in that window raises `InvalidStateError`. Fix: `await run_turn_task`
before reading the result, not `.result()`. Caught only because of the
test — would have shipped silently broken otherwise.

**4. Cost cap as a property of the API surface, not just a budget alert.**

The cap (`$0.50/session`) is checked BEFORE every API call: if
`cost_usd + estimated_next_turn_cost > BUDGET_USD`, emit
`budget.exceeded` and don't call Anthropic. Estimated-next-turn is a
generous constant (`$0.06`) so the cap trips before the expensive turn
starts; worst-case overshoot from one in-flight turn is ~$0.10. This
matches the preview-before-apply pattern from the classification flow:
make safety a property of the *surface*, not a hope about behaviour.

### Surprises

**`except E as e: ... yield e.foo` in a generator closure is a bug.**
Python's `except E as e:` rebinding deletes `e` after the except block
exits, so a closure created inside the except can't reference `e` when
it runs later (in an async generator, "later" is when the consumer
iterates). Capture the values into locals before defining the closure.
Caught by the budget-exceeded test on first run. Standard Python
footgun but easy to miss.

**Multi-stage Docker build is the right call for this app.**
Node + Python in one image would have been ~1.3GB and dragged 200+ npm
packages into the runtime. Multi-stage (Node 22-slim → Python 3.13-slim
with just the built `dist/` copied across) lands at ~400MB and keeps
the runtime image minimal. The build-time cost is one extra stage; the
runtime cost is zero.

**Tailwind v4's `@import "tailwindcss"` is one line of CSS.**
v4's Vite plugin auto-detects content paths from imports. No
`tailwind.config.js`, no `content: [...]` glob. ~30 lines of config
eliminated relative to v3. Worth noting because the v3 tutorials still
dominate search results; the v4 way is materially simpler.

**react-markdown ships CommonMark only — tables need `remark-gfm`.**
Caught after first real conversation: the agent often returns markdown
tables (income breakdown, scenario comparison) and they rendered as
ASCII slop. Two fixes, both small: add `remark-gfm` and pass it via
`remarkPlugins={[remarkGfm]}`; install `@tailwindcss/typography` and
load it with `@plugin "@tailwindcss/typography"` in the CSS. The
`prose` classes had silently been doing nothing before this — they
needed the plugin to take effect. Worth knowing because (a) the React
markdown ecosystem assumes you know to add remark-gfm and (b)
Tailwind v4's `@plugin` directive is new and not yet ubiquitous in
tutorials.

### What got cut to ship

- **Auth, sessions across browser restarts, multi-region.** Out of
  scope by design — demo is ephemeral.
- **D2 (transcript replay).** Would let recruiters watch a sample
  conversation without burning their budget. Natural follow-up; PHASE_2
  backlog notes this.
- **True token streaming.** Today the SSE granularity is block-level
  (one event per `Renderer` callback). Token-by-token would need
  `client.messages.stream()` + agent-loop changes. The block-level
  cadence already feels responsive (~1s per tool call, ~1s per text
  block) because Sonnet returns fast.
- **A `/admin/stats` endpoint** showing today's session count + spend.
  Useful for monitoring abuse; not blocking the demo. Add if the live
  URL starts attracting bot traffic.
- **C3 (history summarisation).** Per-turn token cost is bounded by the
  $0.50 cap (10 turns max), so accumulated message size doesn't matter
  in practice. Skip unless we raise the cap.

### Hand-off

The web UI is the natural arrival point for the agent's portfolio
chapter. From here:
- D2 (transcript replay) would extend the existing SSE event protocol
  — record a session's events to disk, then replay them through the
  same frontend at typing speed. Cheap, demo-quality win.
- B1 (code gate for `apply_classification_rule` /
  `apply_taxonomy_extension`) is more important now: a public URL means
  prompt-injection attempts will happen. The current "prompt-instructed
  approval" contract holds for a well-behaved agent but is exactly the
  kind of thing a hostile user would probe.
- A monitoring story (basic /admin/stats or just structured logging to
  the M720Q's existing log aggregation) before broad sharing.

### Cost

C4 edit: ~3 hours of model time, $0 in API costs (mocked `run_turn` in
tests means no Anthropic calls during dev). Deployment costs are zero
since the M720Q is already running and the tunnel is already set up.
At the demo's per-session cap of $0.50 and per-IP limit of 3/day,
worst-case monthly spend from a single abusive IP = $45; realistic
expected spend (a few real recruiters/month, each doing ~5 turns) ≈
$1-3.

---

## B1 — code gate for apply_* tools

### Goal

Defense-in-depth on the preview-before-apply contract. Phase 1 enforced
it via prompt instruction only; C4 then exposed the agent publicly
through the M720Q tunnel, making prompt-injection ("ignore previous
instructions, apply rule X without confirmation") a realistic vector.
B1 adds a dispatch-layer gate so the contract holds even if the model
is steered off-script.

### What shipped

`agent/tool_registry.py` now declares `GATED_TOOLS` —
`apply_classification_rule` → `preview_rule_application` and
`apply_taxonomy_extension` → `preview_taxonomy_extension`. `dispatch()`
grew an optional `messages` kwarg; when a gated tool is invoked with a
messages array, `check_approval(tool_name, messages)` runs first:

1. **Locate the most recent matching preview** by walking `messages`
   backwards for an assistant `tool_use` block with the required name.
2. **Pull the first plain-string user reply after it** (skipping the
   tool_result list-form user message). If there is none — preview and
   apply emitted in the same assistant turn, for instance — raise.
3. **Classify the reply.** Regex fast-path over a curated approve/deny
   list. On `approve`, return cleanly. On `deny`, raise immediately.
   On `ambiguous` (none, both, contradictory), fall through to a
   forced-tool-use Haiku 4.5 call that returns one of `{approve, deny,
   ambiguous}` — same forced-tool-use pattern as
   `suggest_classification`.

Failures raise `ApprovalRequiredError`. The agent loop's existing
`try/except Exception` at `agent/agent.py:313` converts that into an
`is_error=True` tool_result, so the model sees a clear message
("apply_classification_rule blocked: …") and can re-show the preview
or ask the user. No special-case handling needed in the loop.

The threading from the agent loop is one line: the `dispatch_fn` call
at `agent/agent.py:310` now passes `messages=session.messages`. Tests
that exercise `dispatch()` directly (without a session) keep working
because the gate is skipped when `messages is None`.

### Methodology that worked

**Surface the fork in plain English before any code.** Three real
design decisions had to be made before the implementation became
obvious — approval-detection mechanism (regex vs LLM vs hybrid),
rejection semantics (is_error tool_result vs hard-stop), and history
scope (last user message vs last N turns vs whole session). All three
collapsed once stated — the hybrid + is_error + SPEC §6-baseline
combination is what 80% of well-designed gates would converge on — but
surfacing them as a 3-question AskUserQuestion still mattered, because
each had a "no, we want the other one" answer in 20% of plausible
worlds, and rebuilding the gate to a different shape later would be
half the effort over again.

**Test the pure functions first.** `_regex_classify` and
`_find_latest_approval_message` are pure-function lookups over text
or a list of dicts. Table-driven tests for the regex (14 phrasings,
covering clear-yes, clear-no, ambiguous, mixed, empty) and shaped
fixture tests for the history walk (most-recent-preview-wins,
same-turn-emission-rejected, no-preview-rejected) covered the whole
deterministic surface before any wiring. Bugs surfaced at the unit
layer instead of the integration layer.

**The end-to-end test is what proves the wiring.** Two layers of unit
test plus a `_FakeClient` integration test in `tests/test_agent.py`
that feeds the agent loop an `apply_classification_rule` call with no
preview history and asserts the resulting tool_result has
`is_error=True` and mentions `preview_rule_application`. That's the
test that breaks if anyone forgets to thread `messages=` through
`dispatch_fn` in a future refactor.

**Approval-phrase lists are deliberately conservative.** The regex
both-list-hit case ("yes but actually no") returns `ambiguous` rather
than approve, so contradictory replies escalate to the LLM. Empty and
whitespace-only replies are also `ambiguous`. The LLM system prompt
explicitly tells Haiku "when in doubt, choose ambiguous or deny —
never approve". Layered conservatism: each layer biases away from
false-positive approval.

### Surprises

**The same-turn emission case caught a real edge.** Walking through
how `agent.py:run_turn` assembles `session.messages`, it became clear
that the model could emit `preview_*` and `apply_*` tool_use blocks
in the SAME assistant response — which would create a preview tool_use
in history but no user-reply-after-it, because the dispatch loop runs
both tool_uses before appending the next user message. My
`_find_latest_approval_message` algorithm rejects this case (no
plain-string user reply between preview and apply ⇒ return None ⇒
gate raises). Without that test fixture
(`test_find_latest_approval_message_returns_none_when_no_user_reply_after_preview`),
the gate would have silently approved an injection where the attacker
got the model to emit both calls in one shot.

**Backwards compatibility was free.** Making `messages` a
keyword-only optional kwarg meant zero changes to the existing two
dispatch call-sites in `tests/test_tool_registry.py`. The gate only
fires when the agent loop opts in — which is exactly the right
boundary, because direct calls to `dispatch()` (in tests or future
admin tooling) usually don't have a conversation to inspect.

**Cache invalidation isn't an issue here.** The Sonnet 4.6 system
prompt cache (SPEC §3.3) doesn't see `messages` content, so threading
`session.messages` into the gate doesn't perturb the existing 90%
cache-read rate on turn 2+. The gate's own Haiku call sits outside
the agent loop's caching entirely.

### Decisions I'd revisit

**The approve-phrase list is English-only.** A multilingual deployment
would need either a per-locale phrase list or a higher reliance on the
LLM fallback. Acceptable for this project (UK user, English-only by
construction), but worth flagging if anyone ports this gate pattern
elsewhere.

**No audit log.** Each approval check is in-memory only. For a
multi-user or compliance-driven deployment, persisting `(timestamp,
tool_name, verdict, source: regex|llm, user_text_hash)` rows would
turn the gate into a queryable audit trail. Out of scope for B1; lives
in the Phase 3 ideas pile.

**The LLM fallback runs synchronously.** Worst-case adds ~1s of
latency to an apply_* call when the user's reply is ambiguous. For a
chat UI that's fine (the user is waiting anyway); for a future batch
pipeline that might cycle through many apply_* calls, the
synchronous-per-call shape would dominate. Not a problem now.

### Cost

B1 edit: ~1 hour of model time. Per-call cost when the LLM fallback
fires: ~$0.001 (Haiku 4.5, ~500 input tokens + ~30 output). Realistic
workload — most apply_* approvals are clean "yes" replies caught by
regex — so fallback fires <10% of the time. End-to-end test suite
adds one `@pytest.mark.llm` test at ~$0.001/run, well under the
project's existing $0.10/run budget for the LLM suite.

---

## D2 — transcript replay

### Goal

Make recorded sessions watchable. The agent already logs one JSONL file
per session (`logs/<ISO>.jsonl`) with every event needed to reconstruct
the conversation. What was missing was a reader. Two use cases drove
the work: a debug asset (step through any past session that surprised
us) and a future demo asset (let recruiters watch a canned
conversation without spending their $0.50 budget).

### What shipped

`agent/replay.py` walks the JSONL and dispatches each record to a
Renderer via the existing protocol — no new I/O paths, no new
formatting code. Running `python -m agent.replay logs/<ts>.jsonl`
re-prints the recorded conversation through RichRenderer; the output
is visually equivalent to the live REPL, which is exactly what we
want.

Three CLI knobs: `--delay-seconds N` sleeps N seconds after each
turn's usage record so the replay feels like a live session (handy
for demos); `--no-log-header` strips the session-start/end banner
lines; `--silent` swaps in `SilentRenderer` and emits one ASCII line
of summary stats for scripting use.

The replay loop is intentionally tolerant of schema drift. Unknown
record types log a warning to stderr and continue. Missing
content blocks (no `text`/`tool_use`) are skipped silently. The real
transcripts under `logs/` contain pre-A3 records with slightly
different shapes, and the test suite includes a smoke check that
loads the most recent real log through SilentRenderer end-to-end so
we don't quietly break old transcripts when the schema evolves.

### Methodology that worked

**Reuse the Renderer protocol instead of inventing one.** The agent
loop already abstracts display behind a Protocol with five methods
(`show_tool_call`, `show_tool_result`, `show_assistant_text`,
`show_usage`, `show_error`). The web SSE renderer and the test
SilentRenderer both implement it. Replay only needed one more verb —
`show_user_text` — because the live REPL gets user input from
`renderer.prompt()` and never has to render it back. Adding the
method to all three implementations (Rich, Silent, WebSse) was four
lines per renderer plus one Protocol entry. Everything else flowed
through the existing channel.

**Stats are recomputed, not stored.** The transcript records each
turn's usage individually; there's no aggregate row. The replay loop
keeps a small dataclass and sums as it walks. Means a partial
transcript (Ctrl+C mid-session) still produces accurate totals up to
the cutoff, which would not be true if we relied on a final
summary line.

**Tests use a recording fake renderer.** `RecordingRenderer` captures
every call as a `(method_name, args, kwargs)` tuple. Assertions then
check the call sequence and arguments against a hand-built
transcript fixture. Cleaner than monkey-patching the real renderer
and avoids any dependency on Rich's terminal output.

### Surprises

**`--silent` had a leak.** First implementation passed
`show_header=not args.no_log_header` to `replay()` regardless of
`--silent`. The `_print_header` fallback (for renderers without a
`console` attribute) used plain `print()`, so the session-start /
session-end banner lines still made it to stdout in silent mode.
Caught by re-running the eyeball check after wiring up `--silent`
(test as written passed because it only asserted "summary line is in
output", not "summary line is the only line"). Fixed by hard-coding
`show_header = False` when `--silent` is set, and tightening the
test to assert exactly one non-blank output line.

**Lesson:** assertions that say "X appears in the output" are weaker
than they look. For CLI tools where stdout cleanliness matters,
prefer "output equals X" or "exactly N lines of output".

**Old transcripts work fine through the new renderer.** Pre-A3
sessions don't have `apply_taxonomy_extension` records, and pre-A2
sessions don't have the `category_sub2` field. The replay loop fell
through to the JSON-fallback formatter in `RichRenderer._summarise_result`
gracefully — no test rewrites needed, no special-casing on schema
version.

### Decisions I'd revisit

**The web-replay endpoint is the next obvious step.** The
WebSseRenderer already has a `user_text` event hook ready. A
`/api/replay/<id>` SSE stream that drains a transcript through
WebSseRenderer would let recruiters watch a canned conversation in
the browser — the original motivation for D2 from the C4 follow-ups
list. Deferred from this scope because plumbing the React side adds
another half-day; the CLI tool is enough by itself for the debug
use case and for live demos via screen share.

**No pause-on-user-input mode.** A real demo might want the replay
to pause when it encounters a `user` record and wait for a keypress
(or for the user to type the next message themselves). That's
closer to "interactive transcript walkthrough" than "replay" — fine
to defer until someone asks for it.

### Cost

D2 edit: ~1.5 hours of model time, $0 in API costs (no LLM calls
involved). 11 deterministic tests added, full pytest suite now sits
at 100 tests in ~63s.

---

## D2 follow-up — web replay toggle

### Goal

Surface D2's replay capability through the existing web UI so recruiters
landing on the demo URL can watch a canned conversation without burning
their $0.50 budget. The user explicitly chose a Live/Replay toggle
inline in the header rather than a separate URL ("might not be easily
found") — a UX call that turned out to dovetail nicely with the
implementation: one app, one tab, the existing chat surface re-used.

### What shipped

Two backend routes, a curated transcript bundled into the image, and a
segmented toggle in the React header. Both forks were resolved by the
user upfront: one curated demo (not a picker), paced ~0.8s/turn by
default (not instant).

The backend is ~60 lines added to `web/backend/app.py` plus a tiny
`web/backend/replays.py` catalogue dict. Both routes — `GET /api/replays`
and `GET /api/replays/{id}/stream?delay=N` — bypass session creation,
the per-IP rate limit, and the cost cap. They run `agent.replay.replay()`
(from D2) in `asyncio.to_thread`, feeding events through the existing
`WebSseRenderer`. The renderer's `show_user_text` hook, added in D2's
protocol extension, gets exercised for the first time on this path.

The frontend is mostly state plumbing: a `mode: 'live' | 'replay'`
discriminator, a `streamReplay()` sibling for the existing `streamTurn()`
SSE consumer, and three new event types in the discriminated union
(`replay.info`, `user_text`, `replay.completed`). The Live/Replay
segmented control lives in a small extracted `Header.tsx` so `App.tsx`
stays state-focused.

### Methodology that worked

**The protocol-first decision in D2 paid off here.** D2 added
`show_user_text` to the Renderer Protocol "to keep parity, even though
no current renderer calls it." That hook is exactly what the web
replay needed for the user prompts to render. Zero refactoring of D2
code was needed — the web path is just `replay(path, WebSseRenderer(...))`.
Designing for the next consumer one task in advance turned a half-day
of plumbing into ~3h of writing routes + UI.

**Bypass the safety machinery deliberately and document why.** Replay
is a public read of bundled content with no API cost. Routing it
through the live-session machinery (creating a DB copy, decrementing
the rate limit, checking budget) would be honest-but-pointless overhead.
The two replay routes intentionally skip all of that and the test
`test_replay_bypasses_rate_limit_and_session` codifies the choice so
a future refactor can't silently reintroduce gating.

**A Python dict beats a manifest.json for one entry.** The catalogue
sits in `web/backend/replays.py` as a `dict[str, ReplayMeta]`. Adding
more entries is a code change going through the same review as
everything else. When it grows past ~3 entries, swap to JSON; for now,
co-located metadata + path + validation is the right shape.

### Surprises

**Existing `stream_turn` was 95% generic.** The only thing tying it
to the live path was a hard-coded `"where": "run_turn"` label on its
error event. Adding a keyword-only `error_where` parameter took one
line and let the replay route reuse the function as-is. Always read
the existing utility before writing a sibling.

**The agent image's `replay()` couldn't find the bundled jsonl on
first try.** `Dockerfile.web` had `COPY web/backend/` but not
`COPY web/replays/`. The CLI sanity check (`python -m agent.replay
web/replays/demo_3turn.jsonl`) caught it before the web build, because
the agent image goes through its own Dockerfile that also needed the
file. Lesson restated: when bundling new content, audit every
Dockerfile that touches the path.

**TestClient's SSE handling reads to completion synchronously.**
`client.get("/api/replays/.../stream?delay=0")` returns once the entire
stream is done — including the `replay.completed` event — and exposes
the full body via `.text`. That made the SSE-parser test (`_read_sse_events`)
a one-shot string split, no async generator needed. Pleasant.

### Decisions I'd revisit

**No client-visible speed control.** The server enforces `?delay=N`
with a hard cap of 5s, but the user can't change it from the UI.
Adding 1x/2x/instant buttons next to the toggle is ~30 minutes of work
when someone asks; today, the default 0.8s is fine.

**The cost numbers from the original recording show up in the
replay's `usage` events.** Recruiters watching the replay see "$0.011"
flash by per turn. That's informative — the agent IS this cheap to
run — but if it ever becomes off-putting, the SSE runner can drop
`usage` events on the replay path. Not flagged as today's problem.

**No usage stats on replays.** Replays bypass the rate limiter and
don't increment a counter; we have no idea how many recruiters
actually click the toggle. The `/admin/stats` endpoint from the
demo-hardening backlog would close this gap.

### Cost

D2-followup edit: ~1.5 hours of model time. Zero API costs. 5 new web
tests added; the full web suite is now 13 tests in ~21s, the
agent-side full suite is unchanged at 100 in ~63s.

---

## Admin stats

### Goal

C4 ships behind a public tunnel. B1 + D2 + the web-replay toggle added
more behaviour. Today the operator has zero visibility — no way to
answer "is anyone using the demo today, how much spend, has anyone hit
the rate limit, are replays getting clicked." `/admin/stats` closes
that gap with a JSON snapshot behind a shared-secret header.

### What shipped

`web/backend/stats.py` holds a single mutable `Stats` dataclass pinned
to `app.state.stats` (single-process, single-worker — already
constrained in `web/backend/main.py`, so no locking is needed). The
three existing routes each gained one or two lines that call
`stats.record_*(...)` methods. A new `GET /admin/stats` route gates on
`hmac.compare_digest` of the `X-Admin-Token` header against the
`ADMIN_TOKEN` env var; the env unset → 503 by design so the endpoint
is invisible in dev.

The endpoint mixes lifetime counters (sessions created total, spend
USD total, replays by id) with live read-throughs for fields where
some other component is authoritative — `sessions.active` reads from
`SessionManager.active_count()`, `unique_ips_seen_today` and
`events_today` read from a new `RateLimiter.snapshot()`. Two sources
of truth would have been a counter-divergence bug waiting to happen.

### Methodology that worked

**One struct, explicit increment methods.** Compared to scattered
counter variables, `Stats` with named methods (`record_session_created`,
`record_turn(kind=...)`, `record_replay_started(id)`) gives one place
to read the per-action observability story and one place to assert
against in tests. The dataclass has 11 fields; with scattered globals
this would have been 11 module-level integers and a lot of
"where does this get set?" archeology.

**Auth via env var, default off.** `ADMIN_TOKEN` unset → 503. No
accidental exposure in dev or first-deploy. The 503 vs 401 distinction
is deliberate — 503 says "this endpoint isn't currently enabled,"
which is honest about the config state without revealing whether the
deployer is intentionally locking it.

**Stats live on `app.state`, fresh per TestClient lifespan.** The
existing `client` fixture in `tests/test_web.py` already builds a
fresh TestClient per test, which triggers the FastAPI lifespan and
constructs a new `Stats` instance each time. Tests can assert
absolute counts (`== 1`) rather than deltas. Free correctness from
the existing fixture shape.

### Surprises

**`replay_stream` is async + long-lived.** I had to choose where to
increment the replay counter: at the top of the handler (counts every
start, even aborts) or after the SSE generator completes (counts only
fully-watched replays). Picked "at the top, after the 404 check"
because aborts and disconnects shouldn't undercount; "stream started"
is the honest semantic. Documented in the route.

**Cost-delta computation is one line.** `cost_delta = max(0,
ws.agent_session.cost_usd - cost_before)` after `run_turn` returns.
Works for happy paths AND mid-turn failures (which can have partial
spend) because `cost_usd` is incremented by the agent loop on each
API call before any exception. The `max(0, ...)` guards against
weird negatives from instrumentation bugs.

**hmac.compare_digest vs ==**. Constant-time comparison matters even
for headers, because timing attacks on token comparison are a real
class of bug. Free with the stdlib; no excuse not to use it.

### Decisions I'd revisit

**No per-IP detail in the response.** That's PII-adjacent and only
valuable mid-incident. A separate `/admin/ips` could expose it later;
this endpoint stays clean.

**No persistence across restart.** Matches everything else in the web
app. The day someone wants weekly aggregates is the day this should
land in SQLite under `/tmp/agent-sessions/`. Today, restart = reset is
fine and the `started_at` field in the response makes the reboot
visible.

**JSON only.** No HTML view, no Prometheus exporter. The operator can
pipe through `jq` or `curl` from any client. If a real dashboard
becomes useful, the JSON is the natural input.

### Cost

Admin-stats edit: ~1 hour of model time. Zero API costs. 8 new web
tests added; the full web suite is now 21 tests in ~24s.

---

## C2 — Batch API for bulk Missing classification

### Goal

`suggest_classification` (Haiku 4.5) costs ~$0.001/call and runs once
per Missing row. A real-data ingest typically lands tens-to-hundreds of
Missing rows in one go. Switching the bulk path to Anthropic's Batch
API gets a flat 50% discount across input + output — the documented
headline cost lever from SPEC §3.3. Deferred from Step 5 because the
sync agent loop didn't fit the "submit → come back later" UX; Phase 2's
cross-session SQLite state machinery made it easy.

### What shipped

Two new tools in `agent/tools/classification.py`:

- `bulk_classify_async(memos)` builds one Anthropic Batch API request
  per memo (same forced-tool-use `submit_classification` shape as the
  sync path, just wrapped in the batch envelope), submits, and persists
  a row in a new `pending_batches` table with `status='in_progress'`.
  Returns `{batch_id, status, memos_count, eta_hint}` — never blocks.
- `check_batch_results(batch_id)` reads the row; if already completed
  serves cached suggestions; otherwise calls `messages.batches.retrieve`
  to check `processing_status`. On `ended`, streams the results, parses
  each into the 6-field suggestion dict, computes realised cost via
  `HAIKU_PRICE_* × BATCH_DISCOUNT` (new constants in `claude_helpers.py`),
  and persists everything back to the row.

Neither tool mutates transactions — the agent still goes through
`preview_rule_application` → user approval → `apply_classification_rule`,
which means B1's gate stays where it should and these tools sit
outside `GATED_TOOLS`.

Cross-session UX: `agent.agent._pending_batches_summary()` reads
`pending_batches WHERE status='in_progress'` and adds a one-liner to
the dynamic block of the system prompt. A future session sees
"Pending classification batches (1): batch_xyz (42 memos, submitted
…). Call check_batch_results(batch_id) to retrieve suggestions." The
agent can mention this to the user verbatim.

Stats: three new counters on `Stats` (`batches_submitted_total`,
`batches_completed_total`, `batch_spend_usd_total`). Tools call into a
tiny `agent.tools._stats_sink` module that no-ops by default and gets
wired to the live Stats instance by the web app's lifespan. CLI runs
leave the sink unset — perfect decoupling, no FastAPI imports leak
into the agent tool layer.

### Methodology that worked

**Two tools, not one.** The original "auto-route at threshold N" idea
in SPEC §3.3 would have buried the async semantics inside a single
`suggest_classification` call. That breaks the mental model badly:
sometimes the same tool name takes 2 seconds and returns suggestions,
sometimes it returns a batch_id and you have to poll. Separating into
`bulk_classify_async` + `check_batch_results` makes the async shape
visible at the tool boundary, where the agent can reason about it.

**Model picks the threshold, not code.** The prompt nudge says "if
more than ~10 rows… prefer bulk_classify_async." `BATCH_THRESHOLD = 10`
is a documented constant, not a code-enforced switch. Reasoning: the
model has fuller context than any heuristic — it sees what the user is
actually trying to do, whether the immediate answer matters, how
patient the user feels. A hard threshold would fire batch_classify
even for a casual "what's that one weird transaction" query.

**`_stats_sink` indirection.** Counter wiring tempted me to import
Stats into the tools module, which would have created a layering
violation (agent depends on web). The sink module — Protocol-typed,
default-None, `register(stats)` from the web lifespan — gets the
counters without the dependency. Tests can patch the sink directly
when they want to assert on it.

**Mocked the batches API surgically.** `_FakeBatchesAPI` records
`create_calls`, `retrieve_calls`, `results_calls`. The seven new tests
cover the happy path (3-memo submit → in-progress poll → ended →
parsed suggestions cached → second check serves from cache), the
unhappy paths (unknown id, expired-only batch, empty memo list), and
the on-disk side effects (pending row inserted, then updated to
completed/failed with the right cost). No LLM cost.

### Surprises

**The Anthropic SDK takes plain dicts inside `batches.create`.**
Initially I thought I'd need to import `Request` and
`MessageCreateParamsNonStreaming` typed shapes. In fact the call
accepts a list of dicts with `custom_id` + `params` — much cleaner
than wrapping every request through helper constructors. Saved ~30
lines.

**Per-result token usage is on the inner `message.usage`.** The batch
result wrapper goes envelope → message → usage. My cost computation
walks that chain and treats missing layers as zero contribution, so a
partial-success batch (some succeeded, some errored) costs only what
actually succeeded. The expired-only test exercises the all-errored
branch, where cost should be ~0 — and is.

**The 109-test suite still runs in 57s.** I added 8 classification
tests + 1 agent prompt test, expected ~70s; it came in at the same
pacing because the new tests are pure unit (mocked SDK, single DB row
each). The test budget held.

### Decisions I'd revisit

**No background sweeper.** If a user submits a batch and never returns,
the row sits as `in_progress` forever. Eventually Anthropic times it
out (24h) but `check_batch_results` would only flip the DB row on the
next explicit poll. For a personal-use tool this is fine; for a hosted
multi-user demo it'd be worth a periodic sweeper that polls all
`in_progress` rows older than 24h and marks them `expired`.

**Per-session DB scopes the cross-session UX to the same browser.**
The web demo's per-session DB means a batch submitted in session A is
invisible to session B (different `/tmp/agent-sessions/<id>/finance.db`).
The "next session announces" UX only works in the CLI / single-DB
mode. For the web demo, batches submitted during a session expire with
that session — the $0.50 cap already bounds the realistic batch size,
so this is acceptable.

**Cost accounting reads token counts from the SDK objects.** If the
SDK's response shape ever shifts (e.g. nested `usage.input_tokens`
becomes `usage.input.tokens`), the cost calculation silently returns
0 instead of the real number. A more defensive shape would assert on
the structure. Today, the test fixtures freeze a known-good shape, so
a real-API drift would be caught at the next test run on a new SDK
version.

### Cost

C2 edit: ~2 hours of model time. Zero API costs in testing (mocked).
Realised cost on the actual API: 50% off Haiku — so a 100-memo bulk
classify costs about $0.05 instead of $0.10. 8 new agent-side tests +
1 agent prompt test + 1 admin-stats shape extension. Full agent suite
is now 109 tests in ~57s; web suite stays at 21 tests in ~21s.

---

## B3 — slim down bank_statement_parser.py

### Goal

`classifier/bank_statement_parser.py` was the original 743-line script
copied from the private repo. A1 had already deleted its `categories()`
function; what remained was the `Budget` class — a raw-CSV combiner
plus an Excel-writer using `openpyxl`. The file name implied "parser"
but the active classifier path lives in `rule_lookup.py` post-A1. B3
makes the filename honest and the layout truthful, prerequisite to C1
(real-data ingestion CLI).

### What shipped

`git mv classifier/bank_statement_parser.py classifier/budget_importer.py`.
Rewrote the top docstring to describe what the file actually does today
(legacy ingest pipeline + Excel writer, openpyxl-dependent, dormant
until C1). Updated active doc references in README, CLAUDE.md, SPEC §7
+ §9, `.env.example`, and a handful of comment-level mentions in
sibling source files. Historical references in LEARNINGS and SPEC §3.4
kept verbatim — those describe past state, and rewriting the
methodology log to look retroactively consistent would defeat its
purpose. The full deterministic agent suite still ran clean (109/109);
no code imports the file, so the rename was zero-risk.

### Methodology that worked

**Grep first, design second.** The original B3 framing in the backlog
described "reducing the agent's dependency footprint" by separating the
`Budget` class out of the classifier path. Twenty seconds of `grep -r
bank_statement_parser` showed the file is imported precisely
**nowhere** in the repo. That collapsed the design space: there's no
import path to slim, no dependency to drop, no `__init__.py` re-export
to maintain. The task became "rename for accuracy + update docs," a
~40-min mechanical pass instead of a half-day refactor.

**Active vs historical doc references.** Mid-task I caught myself
about to find-and-replace `bank_statement_parser` across every
LEARNINGS entry. Resisted it. The rule that fell out:

- *Active* references (README layout, SPEC §7 tree, CLAUDE.md "Project
  shape") describe the *current* state. Always update.
- *Historical* references (LEARNINGS sections like "A1 migrated the
  chain from `bank_statement_parser.py`") describe what was true *at
  the time*. Keep verbatim.

A methodology log that gets retconned on every rename is no longer a
log. The one concession: a single explicit "renamed to
`budget_importer.py` in B3" footnote at the top of SPEC §9's redaction
section, which is the most likely landing page for a confused reader.

**`git mv`, not delete-and-create.** Standard hygiene but worth noting
for the next refactor — `git log --follow classifier/budget_importer.py`
still walks back through the original commits. If I'd just done a
`Remove + Write`, the file would have looked like a 743-line drop on
the diff and the history would have been disconnected.

### Surprises

**Zero importers.** I expected `bank_statement_parser` to be wired up
in at least a smoke test or an `__init__.py` re-export. It wasn't —
the file had been entirely dead since the A1 migration, just sitting
there as preserved private-repo code. The backlog's framing ("reduce
the agent's dependency footprint") was already obsolete; the real
value of B3 was discoverability and accuracy, not runtime.

**`openpyxl` isn't in `requirements.txt` and never was.** The original
file imports `openpyxl` inside the `update_excel_budget` method but the
project never declared it. The `Budget` class can't run today inside
the Docker image. That's been the silent truth since the initial
redaction commit. Documented it in the new docstring so the next
reader doesn't have to debug. C1 is the right place to add the
dependency alongside the rest of the ingestion plumbing.

**`__main__` block carries over for free.** Running `python -m
classifier.budget_importer --help` still works after the rename
(argparse parses, then ImportError on openpyxl). The legacy CLI is
preserved verbatim — anyone with the original incantation cached
gets a clean failure mode ("No module named
classifier.bank_statement_parser"), not a silent break.

### Decisions I'd revisit

**No `__init__.py` shim.** A one-line `from classifier.budget_importer
import *` at `classifier/bank_statement_parser.py` would preserve any
notebook or sibling-repo invocations. Skipped it because nothing
external is known to reference the file. If a stale notebook surfaces
months from now, add the shim then — the marginal benefit doesn't
justify the permanent indirection today.

**Helpers stayed at module level.** The four module-level helpers
(`process_amount_sainsbury` et al.) are called from inside `Budget`
methods via `df.apply()`. They could become static methods of `Budget`
for tighter cohesion. Decided against — they're tiny, they work, and
restructuring is C1 territory if C1 ends up rewriting the class
altogether.

### Cost

B3 edit: ~40 minutes of model time. Zero API costs. Zero new tests
(no code path to cover). Zero runtime changes — agent suite still 109
tests in ~57s, web suite still 21 in ~21s.

---

## C1 — real-data ingestion CLI

### What shipped

`docker compose run --rm agent python -m db.migrate --raw YYYY_MM_DD`
now ingests fresh bank exports into `data/finance.db` without leaving
the container. Layout:

```
$BUDGET_DATA_DIR/raw/<date>_amex.csv               (input)
$BUDGET_DATA_DIR/raw/<date>_accounts_download.csv  (input)
$BUDGET_DATA_DIR/raw/<date>_credit_card.csv        (input — barclaycard)
$BUDGET_DATA_DIR/raw/<date>_sainsbury.csv          (input)
$BUDGET_DATA_DIR/preprocessed/<date>_accounts_preprocessed.csv (output)
```

`BUDGET_DATA_DIR` defaults to `./data/real` (gitignored). Overridable
per-invocation with `--budget-root`. SQLite-only; `update_excel_budget()`
stays dormant for a future `--with-excel` follow-up that would also
add `openpyxl` to `requirements.txt`.

### What B3 missed (three latent bugs in budget_importer.py)

B3's framing was "zero importers, just rename." A grep confirmed nothing
in the repo called `Budget`, so we shipped the rename without exercising
the code. C1's first end-to-end run surfaced three bugs that had been
sitting there since the file landed:

1. **`categories` referenced but not imported.** Line 217's
   `self.data.apply(categories, axis=1)` would have raised
   `NameError` on the first call. A1 deleted the in-file `categories()`
   function and migrated rules into `classification_rules`, but the
   companion import (`from classifier.rule_lookup import categories`)
   was never added — the call site fell through the cracks because
   nothing imported `Budget` after A1 either.

2. **`self.data_append` typo.** `import_barclaycard` had
   `self.data = self.data_append(df[...])` — a missing dot. Would
   have raised `AttributeError`. The other three importers spelled
   `_append` correctly.

3. **`DataFrame._append` removed in pandas 2.x.** All four
   importers used the private `_append` API. pandas 2.0 removed it.
   Replaced with `pd.concat([self.data, df[...]], ignore_index=True)`
   in all four sites.

Bugs (1)+(2) were B3's blind spot — a rename without a run leaves dead
code looking healthy. Bug (3) was a pandas-major-version regression
that nobody caught because the file never ran in the project's Python
3.13 / pandas 2.x environment. Lesson reinforced: **a refactor that
doesn't exercise the new code path can't be "done" — at minimum, run
the standalone CLI once.** B3 hit zero importers so I skipped that
step; C1 paid the bill for both refactors.

### SESSION_DB_PATH reused from the web UI

`rule_lookup.categories()` uses a module-level lazy SQLite connection
opened against `db.database.DB_PATH` — the module default, not whatever
`db/migrate.py:main()` was operating on. With the default `--db`,
they match. The moment a test or user passed `--db ./somewhere/else`,
`rule_lookup` connected to the OLD `data/finance.db` and saw a
pre-A1 schema missing `account_match` — classification crashed with
`sqlite3.OperationalError: no such column: account_match`.

Fix: set `db.database.SESSION_DB_PATH` (the ContextVar introduced for
C4's per-session web isolation) at the top of `main()` to `args.db`.
`get_connection(None)` falls back to `SESSION_DB_PATH` before the
module default, so `rule_lookup` re-opens against the correct DB on
its next `_get_conn()` call. The web UI uses the same hook for the
same reason; CLI just happens to want it too.

Decoupled this from changing the public API of `rule_lookup`. The
alternative — exposing a `set_db_path(path)` function — would have
been a wider surface and a longer-lived dependency from CLI flow into
classifier internals.

### `pd.concat` and the empty-DataFrame edge case

After replacing `_append` with `pd.concat`, an empty raw directory
(no input files) produced a no-op `import_raw_data`. Then
`append_columns` tried
`self.data[['Cat - Main', ..., 'Details']] = self.data.apply(categories)`
on an empty DataFrame. pandas 2.x's `apply` returns no columns for an
empty input, and assigning a 0-column value to a 4-key column list
raises `ValueError: Columns must be same length as key`.

Solution: short-circuit `append_columns` when `self.data.empty` and
insert empty Series for the expected output columns. The preprocessed
CSV ends up header-only — clean no-op that downstream `db/migrate.py`
ingests as 0 rows. Without this, a user with `data/real/raw/` empty
would have hit a cryptic pandas error instead of seeing "Inserted 0
rows."

### Path layout — why not legacy tmp_data

The legacy script used `$BUDGET_DATA_DIR/tmp_data/` for both inputs
AND intermediate outputs. "tmp_data" was a misnomer — those inputs
are the user's actual bank exports, not temporary anything.

C1 split this into `raw/` (inputs the user drops in) and
`preprocessed/` (output the pipeline writes). Each directory's name
matches its job; cleanup after a failed run is obvious; the dormant
Excel-writer path keeps writing `<root>/budget.xlsx` unchanged for
the future follow-up.

This is the kind of structural decision that's effectively free at
C1 time and gets expensive to renegotiate later — if the next
contributor adds a `--with-excel` toggle, they don't have to
backtrack to fix path conventions.

### Test coverage

7 new deterministic tests (`tests/test_budget_importer.py` + one
`--raw` end-to-end case in `tests/test_migrate.py`). Coverage focuses
on the current_account + amex paths because their CSV schemas are
trivial to fixture-write. Barclaycard (Credit/Debit split + month
abbreviations needing regex replace) and Sainsbury (ISO-8859-1, no
header) were exercised end-to-end manually but lack pytest fixtures.
Worth adding when those exports surface a real bug.

The `budget_root` fixture sets `BUDGET_DATA_DIR` via `monkeypatch.setenv`
and calls `rule_lookup.reset_connection()` in setup + teardown to
prevent the module-level `_conn` from carrying stale state across
tests. Pattern lifted from how `tests/test_classification.py` already
manages this.

### Cost

C1 edit: ~1.5 hours of model time. Zero API costs. 7 new tests
(~3s extra). End-to-end smoke against a 4-row fixture: 0.5s in the
container. Agent suite now 116 tests in ~64s.

### Decisions I'd revisit

**`update_excel_budget` left dormant.** The Excel writer is real,
working code (modulo the `_append`/openpyxl-import dependencies).
Splitting it into a separate `--with-excel` follow-up is the smallest-
useful-thing call. The alternative — ripping the Excel path out
entirely now — would be premature; the user's existing workflow may
still want it.

**Conditional default `args.source = "real"` only when `--source`
wasn't passed.** When `--raw` is used, source defaults to `'real'`
unless overridden. Cleaner than re-asking the user every invocation,
and `--source synthetic` is still a (weird) escape hatch.

---

## Phase 2 — What shipped

| Ticket | What it is | Key learning |
|--------|------------|--------------|
| B2 | pytest suite — 47→116 deterministic tests | Session-scoped seed + per-test copy is the right SQLite fixture shape |
| A1 | Rules into `classification_rules`; hardcoded chain deleted | `re.match` not `re.search`; migration order matters; round-trip verifier as regression net |
| A2 | Taxonomy expansion (Travel, rail, video) | "what stays Missing" is a conscious choice, not a gap |
| A3 | `extend_taxonomy` preview/apply pair | Thin wrapper; reject-at-preview keeps taxonomy grounded in actual data |
| C4 | React + FastAPI web UI, SSE, per-session DB, cost cap | Renderer protocol from Step 5 was the load-bearing abstraction; ContextVar + `call_soon_threadsafe` for sync→async bridge |
| B1 | Dispatch-layer code gate on `apply_*` tools | Same-turn emission edge case; layered conservatism (regex → Haiku fallback); `is_error` tool_result for self-correction |
| D2 | Transcript replay CLI + web Live/Replay toggle | Reuse the Renderer protocol; replay bypasses cost cap by design |
| /admin/stats | Operator monitoring JSON endpoint | One struct + explicit increment methods; `Stats` on `app.state` = free test isolation |
| C2 | Batch API for bulk Missing classification | Two tools not one; model picks the threshold; `_stats_sink` indirection avoids layer violation |
| B3 | Rename `bank_statement_parser.py` → `budget_importer.py` | Grep first; active vs historical doc references; `git mv` preserves history |
| C1 | Real-data ingestion CLI | B3's "zero importers" framing hid three latent bugs; `SESSION_DB_PATH` reused from C4 |
| D1 | CLI footer renders `$0.0058 / £0.0046` | Show both, don't convert silently; web stays $-only (budget cap is in $) |
| B2 CI | `.github/workflows/test.yml` on push/PR | Docker-only CI mirrors dev workflow; `-T` flag for tty; web suite excluded until it's worth the build time |

**What Phase 2 validated about Phase 1 decisions:**
- The Renderer protocol (Step 5) made C4 essentially free — zero changes to `run_turn`.
- The preview-before-apply pattern (Step 4) made B1 a thin gate rather than a redesign.
- The JSONL transcript format (Step 5) made D2 a one-afternoon job.
- The `data_source` column (Step 2) made per-session DB isolation trivial for C4.
- `SESSION_DB_PATH` (C4) solved the multi-DB problem again in C1 with no new machinery.

**What Phase 2 changed about Phase 1 decisions:**
- Testing strategy flipped at Phase 2's boundary (not Step 5 as originally planned).
- Hardcoded chain gone (A1); `rule_lookup.py` is the only classification path.
- Tool count: 11 (Phase 1) → 13 (post-A3) → 15 (post-C2: `bulk_classify_async` + `check_batch_results`).
- "Prompt-instructed approval" (Phase 1 HITL) is now defence-in-depth layer 2; code gate (B1) is layer 1.
- `BATCH_THRESHOLD = 10` as a code constant became "model picks the threshold" — agent context beats heuristics.

**Remaining nice-to-have:** see [AGENT_SPEC.md §10](AGENT_SPEC.md#10-out-of-scope). The two short tail tickets — D1 (currency display) and the B2 CI residual — shipped 2026-06-02 (entry below).

---

## D1 + B2 CI residual — closing tail (2026-06-02)

Two short tickets handled together as the project wound down.

### D1 — CLI footer now renders both currencies

`agent/cli.py:show_usage` previously rendered `$0.0058`; CLAUDE.md
even codified the "don't convert; show $ for billing, £ for
transaction data" rule. Step 5 LEARNINGS flagged the mixed currencies
in a single view as a real mental tax. D1 trades one rule for a
clearer principle: **don't convert silently — show both.**

```
[in 1,248 · out 187 · cache_read 12,943 · $0.0058 / £0.0046 · turn 2]
```

The `USD_TO_GBP = 0.79` constant lives in
`agent/claude_helpers.py` next to the other pricing constants. It's
hardcoded and approximate — the demo stays offline and deterministic,
and the £ figure is for readability, not accounting. Refresh the
constant when the displayed £ drifts visibly from reality. **The web
UI stays $-only** — the budget cap is in $, the recruiter-facing
context is $-billing-reality, and changing both would have been scope
creep without a real reason.

This is one of those cases where the "two-currency mix is confusing"
finding sat in LEARNINGS for two weeks before being acted on. The
change is genuinely one line in `cli.py` + a constant. Worth doing,
worth doing late.

### B2 CI residual — GitHub Actions on push/PR

`.github/workflows/test.yml` runs
`docker compose run --rm -T agent pytest -m "not llm" --ignore=tests/test_web.py`
on every push and PR to `main`. ~2-3 min on Ubuntu runners.

Three choices worth surfacing:

1. **Docker-only — no separate "install deps + run pytest" path.**
   CI mirrors the developer workflow exactly. CLAUDE.md's "do not
   `pip install` on the host" rule means there's no second-toolchain
   to maintain. Trade-off: image rebuild on every push (no layer
   caching configured yet) costs ~30s vs a hypothetical 5s for
   `pip install`. Acceptable.

2. **`-T` flag.** docker-compose.yml has `tty: true` for interactive
   local use; CI runners have no tty and would crash with "the input
   device is not a TTY". `docker compose run -T` disables tty
   allocation, fixing this without touching the compose file.

3. **Agent suite only, web suite deferred.** test_web.py needs the
   multi-stage web image (node + python build), roughly doubling
   total CI time. The web backend hasn't been flaky enough to justify
   it. Re-enable here if that changes.

No `ANTHROPIC_API_KEY` secret needed in the repo — that was the whole
point of B2's `@pytest.mark.llm` gating. CI exercises the 116
deterministic tests; the 3 LLM-gated tests stay developer-local.

### Cost

Combined: ~1 hour of model time. Zero API costs. Zero new unit tests
(test_cli.py is crash-only; the footer-format change is verified by
eyeballing the demo block). One new file (`.github/workflows/test.yml`).
Agent suite stays at 116 tests in ~65s.
