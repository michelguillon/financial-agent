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
