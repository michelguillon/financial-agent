# Learnings — Personal Finance Agent

A running log of methodology and surprises from building the agent.
One entry per build step (see SPEC_AGENT.md §8).

The goal of this doc is the methodology — not the answers. If a future
project hits a similar problem, what here would help?

---

## Cross-cutting decisions

### Testing strategy (so far): verify-by-running

No pytest, no fixtures, no CI. Each step's validator is baked into the
tool itself — the synthetic generator's summary, the throwaway round-trip
verifier in Step 1, `migrate.py`'s validation epilogue. At this scale,
"run the script, eyeball the output" has been faster than writing test
infrastructure.

The trigger for adopting pytest is Step 5 — the agent loop is where the
regression surface (tools × scenarios × state transitions) gets large
enough for unit tests to pay back. Stating this out loud so it reads as a
policy, not an oversight.

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
([SPEC §3.6](SPEC_AGENT.md#36--demo-mode)) actually pays off — the dev
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
conversation flow naturally has an approval gate built in (the user sees
the preview before saying yes). The alternative — one tool that mutates
and returns a count — would push the safety question into prompt-
engineering. The split makes it a property of the API.

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

### Reusable patterns

- **AskUserQuestion before plan, ExitPlanMode after.** Four design
  questions in plan mode beats four questions per implementation phase.
- **Co-locate schema with function**; assemble the registry from the
  per-module exports. Single source of truth, drift-impossible.
- **Two-step destructive operations** (preview → apply) where the
  agent is in the loop. The shape of the API enforces the safety,
  not just the prompt.
- **Tool-use with forced `tool_choice`** as a way to get guaranteed
  structured output from a model call — more robust than JSON-mode
  prompting because the schema is part of the request.
- **Mount the directory, not the file**, for Docker bind mounts. Path
  existence is no longer a fresh-checkout footgun.
- **Lazy module-level connection**, with a `reset_*` helper for tests.
  The cost of opening the SQLite DB on every call would add up across
  a long agent session; the lazy global pays the open cost once.
