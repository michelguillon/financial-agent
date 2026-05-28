# Learnings — Personal Finance Agent

A running log of methodology and surprises from building the agent.
One entry per build step (see SPEC_AGENT.md §8).

The goal of this doc is the methodology — not the answers. If a future
project hits a similar problem, what here would help?

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
