"""scenarios.py — forward-looking financial-reasoning tools (SPEC §5.2).

Four tools:
  - get_spending_summary(months, category_main=None)
  - get_income_summary(months)
  - classify_fixed_vs_discretionary(months)
  - model_scenario(scenario, parameters)

Design notes
------------
* All windows are anchored on the most recent transaction date in the data
  (not `date('now')`), so the same code works against the historical
  synthetic dataset and against live real data.
* Credit-card payoff rows (`Shopping/CreditCard`) are excluded from spend
  totals — they're balanced by the underlying CC purchases that are already
  counted on the CC accounts. This is the double-counting contract
  established in Step 1's gen_cc_payments docstring.
* `classify_fixed_vs_discretionary` uses a category-based mapping (the user
  chose this over a data-driven variance test). The FIXED_CATEGORIES set
  is the single source of truth; revisit in Phase 2 if the spend mix shifts.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Allow `python -m agent.tools.scenarios` to find the sibling db package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.database import get_data_source, open_db  # noqa: E402


# ---------------------------------------------------------------------------
# Category policy: what counts as a fixed cost
# ---------------------------------------------------------------------------

# (category_main, category_sub) tuples that are treated as fixed. Anything
# else that's a spend (negative amount) and isn't an excluded class is
# discretionary.
FIXED_CATEGORIES: set[tuple[str, str | None]] = {
    ("House", "Mortgage"),
    ("Bills", "utilities"),     # all utilities sub2s
    ("Bills", "loan"),
    ("Bills", "Household"),     # cleaner etc.
    ("Bills", "Charity"),       # regular giving
    ("Bills", "Bank Fees"),
    ("Savings", "Transfer"),    # forced savings is a fixed line
    ("Leisure", "subscription"),  # gym/streaming/news are recurring
    ("Leisure", "sport"),
}

# Always excluded from spend totals (not "spending" in the everyday sense).
SPENDING_EXCLUSIONS_SQL = (
    "category_main NOT IN ('Income', 'Savings', 'Withdrawal') "
    "AND NOT (category_main = 'Shopping' AND category_sub = 'CreditCard')"
)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _ref_date(conn, source: str) -> date:
    """Most recent transaction date in the data — the reference for 'now'.

    Note: SQLite returns MAX(date) as a string (PARSE_DECLTYPES converters
    only apply to declared columns, not expressions), so we parse it back.
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM transactions WHERE data_source = ?",
        (source,),
    ).fetchone()
    if row["d"] is None:
        raise RuntimeError(f"No transactions found for data_source={source!r}")
    return row["d"] if isinstance(row["d"], date) else date.fromisoformat(row["d"])


def _month_start_n_back(d: date, months: int) -> date:
    """First day of the month that, inclusively, starts an N-month window
    ending in d's month.

    Example: d=2025-12-15, months=6 -> 2025-07-01 (Jul..Dec = 6 months).
    """
    total = d.year * 12 + (d.month - 1) - (months - 1)
    y, m = divmod(total, 12)
    return date(y, m + 1, 1)


def _format_period(start: date, end: date) -> str:
    return f"{start.strftime('%Y-%m')} to {end.strftime('%Y-%m')}"


# ---------------------------------------------------------------------------
# get_spending_summary
# ---------------------------------------------------------------------------

def get_spending_summary(
    months: int,
    category_main: str | None = None,
    source: str | None = None,
) -> dict:
    """Spend totals + monthly averages over the last N months.

    Excludes Income, Savings, Withdrawal, Shopping/CreditCard.
    Optionally filter to a single top-level category.
    """
    src = source or get_data_source()
    with open_db() as conn:
        ref = _ref_date(conn, src)
        start = _month_start_n_back(ref, months)

        params: list = [src, start, ref]
        sql = f"""
            SELECT
                category_main,
                COALESCE(category_sub, '') AS category_sub,
                SUM(-amount) AS total
            FROM transactions
            WHERE data_source = ?
              AND date >= ? AND date <= ?
              AND amount < 0
              AND {SPENDING_EXCLUSIONS_SQL}
        """
        if category_main is not None:
            sql += " AND category_main = ?"
            params.append(category_main)
        sql += " GROUP BY category_main, category_sub ORDER BY total DESC"

        rows = conn.execute(sql, params).fetchall()

    by_category: dict[str, dict[str, float]] = {}
    grand_total = 0.0
    for r in rows:
        key = f"{r['category_main']}/{r['category_sub']}" if r["category_sub"] else r["category_main"]
        total = round(r["total"], 2)
        by_category[key] = {
            "total": total,
            "monthly_avg": round(total / months, 2),
        }
        grand_total += total

    return {
        "period": _format_period(start, ref),
        "months": months,
        "by_category": by_category,
        "grand_total": round(grand_total, 2),
        "monthly_avg_total": round(grand_total / months, 2),
    }


# ---------------------------------------------------------------------------
# get_income_summary
# ---------------------------------------------------------------------------

def get_income_summary(months: int, source: str | None = None) -> dict:
    """Aggregate Income/* rows over N months.

    Detects distinct sources via the first whitespace-delimited token of the
    memo (e.g. 'COMPANY_C SALARY' -> source 'COMPANY_C'). Classifies stability
    by month-to-month variance.
    """
    src = source or get_data_source()
    with open_db() as conn:
        ref = _ref_date(conn, src)
        start = _month_start_n_back(ref, months)

        rows = conn.execute(
            """
            SELECT date, amount, memo, category_sub
            FROM transactions
            WHERE data_source = ?
              AND date >= ? AND date <= ?
              AND category_main = 'Income'
              AND amount > 0
            ORDER BY date
            """,
            (src, start, ref),
        ).fetchall()

    if not rows:
        return {
            "monthly_avg": 0.0, "sources": [],
            "stability": "no_income_detected",
            "months_analysed": months,
            "period": _format_period(start, ref),
        }

    # Per-source aggregation
    by_source: dict[str, dict] = {}
    monthly_totals: dict[tuple[int, int], float] = {}
    for r in rows:
        token = (r["memo"] or "").split()[0] if r["memo"] else "UNKNOWN"
        sub = r["category_sub"] or ""
        amt = r["amount"]
        bucket = by_source.setdefault(token, {"type": sub, "total": 0.0, "n": 0})
        bucket["total"] += amt
        bucket["n"] += 1
        ym = (r["date"].year, r["date"].month)
        monthly_totals[ym] = monthly_totals.get(ym, 0.0) + amt

    sources = [
        {
            "name": name,
            "type": info["type"],
            "monthly_avg": round(info["total"] / months, 2),
            "occurrences": info["n"],
        }
        for name, info in sorted(by_source.items(), key=lambda kv: -kv[1]["total"])
    ]

    monthly_avg_total = round(sum(info["total"] for info in by_source.values()) / months, 2)

    # Stability: coefficient of variation across months that had income
    values = list(monthly_totals.values())
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    cv = (var ** 0.5) / mean if mean else 0
    if cv < 0.05:
        stability = "stable"
    elif cv < 0.20:
        stability = "mostly_stable"
    else:
        stability = "variable"

    return {
        "monthly_avg": monthly_avg_total,
        "sources": sources,
        "stability": stability,
        "months_analysed": months,
        "period": _format_period(start, ref),
    }


# ---------------------------------------------------------------------------
# classify_fixed_vs_discretionary
# ---------------------------------------------------------------------------

def _is_fixed(category_main: str, category_sub: str | None) -> bool:
    return (category_main, category_sub) in FIXED_CATEGORIES


def classify_fixed_vs_discretionary(
    months: int, source: str | None = None
) -> dict:
    """Split spend into fixed vs discretionary using FIXED_CATEGORIES.

    Same exclusions as get_spending_summary (no Income/Savings/Withdrawal/CC).
    """
    src = source or get_data_source()
    with open_db() as conn:
        ref = _ref_date(conn, src)
        start = _month_start_n_back(ref, months)
        rows = conn.execute(
            f"""
            SELECT category_main, category_sub, SUM(-amount) AS total
            FROM transactions
            WHERE data_source = ?
              AND date >= ? AND date <= ?
              AND amount < 0
              AND {SPENDING_EXCLUSIONS_SQL}
            GROUP BY category_main, category_sub
            """,
            (src, start, ref),
        ).fetchall()

    fixed = {"by_category": {}, "total": 0.0}
    disc = {"by_category": {}, "total": 0.0}
    for r in rows:
        key = (
            f"{r['category_main']}/{r['category_sub']}"
            if r["category_sub"]
            else r["category_main"]
        )
        total = round(r["total"], 2)
        bucket = fixed if _is_fixed(r["category_main"], r["category_sub"]) else disc
        bucket["by_category"][key] = {
            "total": total,
            "monthly_avg": round(total / months, 2),
        }
        bucket["total"] += total

    for b in (fixed, disc):
        b["total"] = round(b["total"], 2)
        b["monthly_total"] = round(b["total"] / months, 2)

    return {
        "period": _format_period(start, ref),
        "months": months,
        "fixed": fixed,
        "discretionary": disc,
    }


# ---------------------------------------------------------------------------
# model_scenario
# ---------------------------------------------------------------------------

def _build_recommendations(fixed_disc: dict, target_savings_pct: float = 0.5) -> list[dict]:
    """Rank discretionary categories by monthly spend, propose `pct` cut to each."""
    items = sorted(
        fixed_disc["discretionary"]["by_category"].items(),
        key=lambda kv: -kv[1]["monthly_avg"],
    )
    recs = []
    for cat, info in items:
        cur = info["monthly_avg"]
        suggested = round(cur * (1 - target_savings_pct), 2)
        saving = round(cur - suggested, 2)
        recs.append({
            "category": cat,
            "current_monthly": cur,
            "suggested_monthly": suggested,
            "potential_saving": saving,
            "type": "discretionary",
        })
    return recs


def model_scenario(
    scenario: str,
    parameters: dict,
    source: str | None = None,
) -> dict:
    """Forward-looking scenario modeller.

    Supported scenarios:
      - job_loss:     {"income_reduction_pct": 100} or {"new_monthly_income": X}
      - rate_change:  {"current_rate", "new_rate", "mortgage_balance",
                       optional "effective_date"}
      - expense_change: {"category": "Shopping/Groceries", "monthly_delta": +/-X}

    Returns surplus before/after, the gap, and a ranked list of discretionary
    cuts to close it. The tool is intentionally deterministic — the agent
    layers narrative judgement on top of these numbers.
    """
    src = source or get_data_source()
    lookback = 6  # months — recent enough to reflect current life, long enough to smooth

    income = get_income_summary(months=lookback, source=src)
    fd = classify_fixed_vs_discretionary(months=lookback, source=src)
    current_income = income["monthly_avg"]
    fixed_monthly = fd["fixed"]["monthly_total"]
    disc_monthly = fd["discretionary"]["monthly_total"]
    current_surplus = round(current_income - fixed_monthly - disc_monthly, 2)

    if scenario == "job_loss":
        if "new_monthly_income" in parameters:
            new_income = float(parameters["new_monthly_income"])
        elif "income_reduction_pct" in parameters:
            pct = float(parameters["income_reduction_pct"])
            new_income = round(current_income * (1 - pct / 100), 2)
        else:
            raise ValueError(
                "job_loss needs 'new_monthly_income' or 'income_reduction_pct'"
            )
        new_surplus = round(new_income - fixed_monthly - disc_monthly, 2)
        gap = round(current_surplus - new_surplus, 2)
        return {
            "scenario": scenario,
            "lookback_months": lookback,
            "current_monthly_income": current_income,
            "new_monthly_income": new_income,
            "current_monthly_surplus": current_surplus,
            "new_monthly_surplus": new_surplus,
            "gap": gap,
            "recommendations": _build_recommendations(fd),
            "fixed_costs_unchanged": list(fd["fixed"]["by_category"].keys()),
        }

    elif scenario == "rate_change":
        required = ("current_rate", "new_rate", "mortgage_balance")
        for k in required:
            if k not in parameters:
                raise ValueError(f"rate_change requires '{k}'")
        cur_rate = float(parameters["current_rate"])
        new_rate = float(parameters["new_rate"])
        balance = float(parameters["mortgage_balance"])

        # Simple interest-delta approximation: annual cost = balance * (new - cur).
        # Captures the right monthly magnitude for budgeting without needing
        # the remaining term. A true amortisation recalc could be a Phase 2
        # refinement.
        monthly_delta = round(balance * (new_rate - cur_rate) / 12, 2)
        new_fixed_monthly = round(fixed_monthly + monthly_delta, 2)
        new_surplus = round(current_income - new_fixed_monthly - disc_monthly, 2)
        gap = round(current_surplus - new_surplus, 2)
        return {
            "scenario": scenario,
            "lookback_months": lookback,
            "rate_delta": round(new_rate - cur_rate, 4),
            "mortgage_balance": balance,
            "monthly_payment_delta": monthly_delta,
            "effective_date": parameters.get("effective_date"),
            "current_monthly_surplus": current_surplus,
            "new_monthly_surplus": new_surplus,
            "gap": gap,
            "recommendations": _build_recommendations(fd),
            "fixed_costs_unchanged": list(fd["fixed"]["by_category"].keys()),
            "calculation_note": (
                "Uses simple interest delta (balance * rate change / 12). "
                "Full amortisation requires remaining term — not provided."
            ),
        }

    elif scenario == "expense_change":
        if "category" not in parameters or "monthly_delta" not in parameters:
            raise ValueError(
                "expense_change requires 'category' and 'monthly_delta'"
            )
        delta = float(parameters["monthly_delta"])
        new_surplus = round(current_surplus - delta, 2)
        gap = round(current_surplus - new_surplus, 2)
        return {
            "scenario": scenario,
            "lookback_months": lookback,
            "category": parameters["category"],
            "monthly_delta": delta,
            "current_monthly_surplus": current_surplus,
            "new_monthly_surplus": new_surplus,
            "gap": gap,
            "recommendations": _build_recommendations(fd) if gap > 0 else [],
            "fixed_costs_unchanged": list(fd["fixed"]["by_category"].keys()),
        }

    else:
        raise ValueError(
            f"Unknown scenario {scenario!r}. "
            "Expected one of: 'job_loss', 'rate_change', 'expense_change'."
        )


# ---------------------------------------------------------------------------
# JSON Schemas for the Anthropic tool registry
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "get_spending_summary",
        "description": (
            "Spend totals + monthly averages over the last N months, grouped "
            "by category/sub. Excludes Income, Savings, Withdrawal, and "
            "Shopping/CreditCard (the latter to avoid double-counting CC "
            "payoffs against the underlying purchases). Optionally filter to "
            "a single main category."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "minimum": 1, "maximum": 60},
                "category_main": {
                    "type": ["string", "null"],
                    "description": "Limit to one main category (Income, House, Shopping, etc.).",
                },
            },
            "required": ["months"],
        },
    },
    {
        "name": "get_income_summary",
        "description": (
            "Average monthly income, detected sources (by memo-prefix), and "
            "stability (stable/mostly_stable/variable) over the last N months."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"months": {"type": "integer", "minimum": 1, "maximum": 60}},
            "required": ["months"],
        },
    },
    {
        "name": "classify_fixed_vs_discretionary",
        "description": (
            "Split spend over the last N months into fixed costs (mortgage, "
            "utilities, loans, cleaner, charity, savings transfers, "
            "subscriptions, gym) vs discretionary (everything else). Returns "
            "totals and per-category breakdowns for each bucket."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"months": {"type": "integer", "minimum": 1, "maximum": 60}},
            "required": ["months"],
        },
    },
    {
        "name": "model_scenario",
        "description": (
            "Forward-looking scenario modeller. job_loss simulates an income "
            "drop; rate_change simulates a mortgage rate move (simple "
            "interest delta); expense_change simulates a recurring expense "
            "added/removed. Returns surplus before/after, the gap, and a "
            "ranked list of discretionary cuts that could close it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "enum": ["job_loss", "rate_change", "expense_change"],
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Scenario-specific params. job_loss: "
                        "{income_reduction_pct} OR {new_monthly_income}. "
                        "rate_change: {current_rate, new_rate, mortgage_balance, "
                        "effective_date?}. "
                        "expense_change: {category, monthly_delta}."
                    ),
                },
            },
            "required": ["scenario", "parameters"],
        },
    },
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== get_spending_summary(months=12) ===")
    s = get_spending_summary(months=12)
    print(f"  period: {s['period']}")
    print(f"  grand_total: £{s['grand_total']:,.2f}  (monthly avg: £{s['monthly_avg_total']:,.2f})")
    # Spot check: expect roughly £45-90k/year in spend (excluding CC + savings).
    assert 30_000 < s["grand_total"] < 100_000, \
        f"spend total looks off: {s['grand_total']}"
    print("  Top 5 categories:")
    for k, v in list(s["by_category"].items())[:5]:
        print(f"    {k:<30} £{v['total']:>10,.2f}  (avg £{v['monthly_avg']:>7,.2f}/mo)")

    print("\n=== get_income_summary(months=12) ===")
    inc = get_income_summary(months=12)
    print(f"  monthly_avg: £{inc['monthly_avg']:,.2f}  ({inc['stability']})")
    print(f"  sources:")
    for src in inc["sources"]:
        print(f"    {src['name']:<12} type={src['type']:<8} £{src['monthly_avg']:>8,.2f}/mo  ({src['occurrences']} txns)")
    # Last 12 months of synthetic data end 2025-12-31 so income should reflect
    # the COMPANY_C era.
    assert any(s["name"] == "COMPANY_C" for s in inc["sources"]), \
        f"COMPANY_C missing from recent income sources: {inc['sources']}"

    print("\n=== classify_fixed_vs_discretionary(months=12) ===")
    fd = classify_fixed_vs_discretionary(months=12)
    print(f"  fixed:         £{fd['fixed']['total']:>10,.2f}  ({fd['fixed']['monthly_total']:>7,.2f}/mo)")
    print(f"  discretionary: £{fd['discretionary']['total']:>10,.2f}  ({fd['discretionary']['monthly_total']:>7,.2f}/mo)")
    # Fixed should be a large chunk: mortgage + utilities + cleaner + savings + subs.
    assert fd["fixed"]["total"] > 15_000, \
        f"fixed-cost total looks low: {fd['fixed']['total']}"
    print(f"  fixed categories: {list(fd['fixed']['by_category'].keys())}")

    print("\n=== model_scenario(job_loss, 100% reduction) ===")
    js = model_scenario("job_loss", {"income_reduction_pct": 100})
    print(f"  current surplus: £{js['current_monthly_surplus']:>8,.2f}/mo")
    print(f"  new surplus:     £{js['new_monthly_surplus']:>8,.2f}/mo")
    print(f"  gap:             £{js['gap']:>8,.2f}/mo")
    print(f"  top 3 recommended cuts:")
    for r in js["recommendations"][:3]:
        print(f"    {r['category']:<30} £{r['current_monthly']:>7,.2f} -> £{r['suggested_monthly']:>7,.2f}  save £{r['potential_saving']:>6,.2f}")
    assert js["new_monthly_surplus"] < 0, "100% income loss should yield negative surplus"
    assert len(js["recommendations"]) > 0

    print("\n=== model_scenario(rate_change 2% -> 4% on £185k) ===")
    rs = model_scenario("rate_change", {
        "current_rate": 0.02, "new_rate": 0.04,
        "mortgage_balance": 185_000, "effective_date": "2027-03-01",
    })
    print(f"  monthly payment delta: £{rs['monthly_payment_delta']:,.2f}")
    print(f"  current surplus: £{rs['current_monthly_surplus']:,.2f} -> new: £{rs['new_monthly_surplus']:,.2f}")
    # 2% extra on £185k = £3,700/year = £308.33/mo
    assert 250 < rs["monthly_payment_delta"] < 400, \
        f"rate delta calc looks off: {rs['monthly_payment_delta']}"

    print("\n=== model_scenario(expense_change +£200/mo on Leisure) ===")
    es = model_scenario("expense_change", {
        "category": "Leisure/food/drinks", "monthly_delta": 200,
    })
    print(f"  current surplus: £{es['current_monthly_surplus']:,.2f}")
    print(f"  new surplus:     £{es['new_monthly_surplus']:,.2f}  (delta £{es['monthly_delta']:,.2f})")
    assert abs(es["new_monthly_surplus"] - (es["current_monthly_surplus"] - 200)) < 0.01

    print("\nAll scenarios.py smoke tests passed.")
