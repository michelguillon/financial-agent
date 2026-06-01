"""cli.py — Rich-based display layer for the agent loop.

Implements the Renderer protocol from agent.agent. Decisions:

- Tool calls render as one-liners in dim text: `> get_spending_summary(months=12)`
- Tool results render as a small Markdown-rendered block, capped in width
  so the terminal doesn't get spammed by 200-line dumps. The full result
  still goes to the transcript.
- Assistant text uses rich's Markdown renderer — tables, bullets, code
  fences all look right.
- Per-turn footer shows token counts + USD cost in dim grey.

The currency mix is intentional: tool results show £ (transaction
currency), the cost footer shows $ (API billing currency).
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text


# A few colour choices, kept in one place.
STYLE_USER_PROMPT = "bold cyan"
STYLE_TOOL_CALL = "dim"
STYLE_TOOL_RESULT_BORDER = "dim cyan"
STYLE_ASSISTANT_BORDER = "green"
STYLE_FOOTER = "dim"
STYLE_ERROR = "bold red"


class RichRenderer:
    """Rich-backed implementation of the Renderer protocol."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    # ---- protocol methods ------------------------------------------------

    def show_tool_call(self, name: str, input: dict) -> None:
        arg_str = self._format_args(input)
        self.console.print(f"› {name}({arg_str})", style=STYLE_TOOL_CALL)

    def show_tool_result(self, name: str, result: Any, is_error: bool = False) -> None:
        if is_error:
            self.console.print(
                Panel(
                    Text(str(result), style=STYLE_ERROR),
                    title=f"{name} — ERROR",
                    border_style=STYLE_ERROR,
                )
            )
            return

        summary = self._summarise_result(name, result)
        self.console.print(
            Panel(
                summary,
                title=name,
                border_style=STYLE_TOOL_RESULT_BORDER,
                padding=(0, 1),
            )
        )

    def show_assistant_text(self, text: str) -> None:
        self.console.print(
            Panel(
                Markdown(text),
                border_style=STYLE_ASSISTANT_BORDER,
                padding=(0, 1),
            )
        )

    def show_user_text(self, text: str) -> None:
        # Used only by replay (the live REPL gets the user message from
        # `prompt()` and never needs to render it back).
        self.console.print(f"You> {text}", style=STYLE_USER_PROMPT)

    def show_usage(self, *, input_tokens: int, output_tokens: int,
                   cache_read: int, cache_creation: int,
                   cost_usd: float, turn: int) -> None:
        parts = [
            f"in {input_tokens:,}",
            f"out {output_tokens:,}",
            f"cache_read {cache_read:,}",
        ]
        if cache_creation:
            parts.append(f"cache_create {cache_creation:,}")
        parts.append(f"${cost_usd:.4f}")
        parts.append(f"turn {turn}")
        self.console.print(f"[{' · '.join(parts)}]", style=STYLE_FOOTER)
        self.console.print()  # blank line between turns

    def show_error(self, where: str, detail: str) -> None:
        # Build a Text node so [tool:foo]-style prefixes aren't parsed as markup.
        self.console.print(Text(f"[{where}] {detail}", style=STYLE_ERROR))

    def prompt(self, label: str) -> str:
        # rich's Prompt handles history/editing if readline is available.
        return Prompt.ask(f"[{STYLE_USER_PROMPT}]{label}[/]", console=self.console)

    # ---- summarisers -----------------------------------------------------

    def _format_args(self, input: dict) -> str:
        """Compact, human-friendly arg rendering — `months=12, category_main=None`."""
        if not input:
            return ""
        parts = []
        for k, v in input.items():
            if isinstance(v, str):
                # Truncate long string args for the call line; the full
                # value is in the transcript.
                if len(v) > 40:
                    parts.append(f"{k}={v[:37]!r}...")
                else:
                    parts.append(f"{k}={v!r}")
            else:
                parts.append(f"{k}={v!r}")
        return ", ".join(parts)

    def _summarise_result(self, name: str, result: Any) -> Any:
        """Per-tool summary that fits in one panel — avoids dumping full JSON.

        Falls back to truncated JSON for tools we haven't formatted.
        """
        if result is None:
            return Text("None")
        if isinstance(result, dict) and "error" in result:
            return Text(f"{result.get('error')}: {result.get('message', '')}",
                        style=STYLE_ERROR)

        # Per-tool formatters
        if name == "get_spending_summary":
            return self._fmt_spending_summary(result)
        if name == "get_income_summary":
            return self._fmt_income_summary(result)
        if name == "classify_fixed_vs_discretionary":
            return self._fmt_fixed_vs_disc(result)
        if name == "model_scenario":
            return self._fmt_model_scenario(result)
        if name == "get_unclassified_transactions":
            return self._fmt_unclassified(result)
        if name == "preview_rule_application":
            return self._fmt_preview(result)
        if name == "apply_classification_rule":
            return self._fmt_apply(result)
        if name == "list_categories":
            return Text(f"{len(result)} main categories: {', '.join(sorted(result))}")
        if name == "suggest_classification":
            return self._fmt_suggest(result)
        if name == "get_agent_state":
            return Text(self._compact_json(result))
        if name == "set_agent_state":
            return Text(f"success={result.get('success')}")

        # Fallback: truncated JSON
        return Text(self._compact_json(result, max_len=600))

    # ---- per-tool formatters ---------------------------------------------

    @staticmethod
    def _compact_json(obj: Any, max_len: int = 300) -> str:
        s = json.dumps(obj, default=str, ensure_ascii=False, indent=2)
        if len(s) > max_len:
            s = s[: max_len - 20] + f"... [+{len(s) - max_len + 20} chars]"
        return s

    def _fmt_spending_summary(self, r: dict) -> Text:
        lines = [
            f"period: {r['period']}  ({r['months']} months)",
            f"grand_total: £{r['grand_total']:,.2f}   monthly_avg: £{r['monthly_avg_total']:,.2f}",
            "top 5:",
        ]
        for k, v in list(r["by_category"].items())[:5]:
            lines.append(f"  {k:<30} £{v['total']:>10,.2f}  (£{v['monthly_avg']:>7,.2f}/mo)")
        return Text("\n".join(lines))

    def _fmt_income_summary(self, r: dict) -> Text:
        lines = [
            f"period: {r['period']}  monthly_avg: £{r['monthly_avg']:,.2f}  stability: {r['stability']}",
            "sources:",
        ]
        for s in r["sources"]:
            lines.append(f"  {s['name']:<14} {s['type']:<8} £{s['monthly_avg']:>8,.2f}/mo  ({s['occurrences']} txns)")
        return Text("\n".join(lines))

    def _fmt_fixed_vs_disc(self, r: dict) -> Text:
        f, d = r["fixed"], r["discretionary"]
        return Text(
            f"period: {r['period']}\n"
            f"fixed:         £{f['total']:>10,.2f}  (£{f['monthly_total']:>7,.2f}/mo, {len(f['by_category'])} categories)\n"
            f"discretionary: £{d['total']:>10,.2f}  (£{d['monthly_total']:>7,.2f}/mo, {len(d['by_category'])} categories)"
        )

    def _fmt_model_scenario(self, r: dict) -> Text:
        lines = [
            f"scenario: {r['scenario']}",
            f"current_surplus: £{r['current_monthly_surplus']:>8,.2f}/mo",
            f"new_surplus:     £{r['new_monthly_surplus']:>8,.2f}/mo",
            f"gap:             £{r['gap']:>8,.2f}/mo",
        ]
        if r.get("monthly_payment_delta") is not None:
            lines.append(f"monthly_payment_delta: £{r['monthly_payment_delta']:,.2f}")
        if r.get("recommendations"):
            lines.append(f"top recommendations ({len(r['recommendations'])} total):")
            for rec in r["recommendations"][:3]:
                lines.append(f"  {rec['category']:<30} £{rec['current_monthly']:>7,.2f} → £{rec['suggested_monthly']:>7,.2f}")
        return Text("\n".join(lines))

    def _fmt_unclassified(self, rows: list) -> Text:
        if not rows:
            return Text("(none)")
        lines = [f"{len(rows)} rows:"]
        for r in rows[:10]:
            lines.append(f"  [{r['id']}] {r['date']} {r['account_name']:<12} £{r['amount']:>8,.2f}  {r['memo']}")
        if len(rows) > 10:
            lines.append(f"  ... and {len(rows) - 10} more")
        return Text("\n".join(lines))

    def _fmt_preview(self, r: dict) -> Text:
        prop = r["proposed_classification"]
        cls = "/".join(filter(None, [prop["category_main"], prop["category_sub"], prop.get("category_sub2")]))
        lines = [
            f"would_match: {r['would_match']}",
            f"proposed: {cls}",
            "samples:",
        ]
        for s in r["sample_matches"][:5]:
            lines.append(f"  [{s['id']}] {s['date']} £{s['amount']:>7,.2f}  {s['memo']}")
        return Text("\n".join(lines))

    def _fmt_apply(self, r: dict) -> Text:
        return Text(
            f"rule_id={r['rule_id']}  rules_added={r['rules_added']}  "
            f"transactions_reclassified={r['transactions_reclassified']}"
        )

    def _fmt_suggest(self, r: dict) -> Text:
        cls = "/".join(filter(None, [r["category_main"], r["category_sub"], r.get("category_sub2")]))
        return Text(
            f"category: {cls}\n"
            f"pattern:  {r['suggested_pattern']!r}\n"
            f"rationale: {r['rationale']}"
        )


# ---------------------------------------------------------------------------
# Smoke test — render every tool result type so the layout can be eyeballed
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    r = RichRenderer()
    r.show_user_text("What did I spend on groceries last month?")
    r.show_assistant_text("# Hello\n\nThis is **rich** markdown.\n\n- bullet 1\n- bullet 2")

    r.show_tool_call("get_spending_summary", {"months": 12})
    r.show_tool_result("get_spending_summary", {
        "period": "2025-01 to 2025-12", "months": 12,
        "by_category": {
            "House/Mortgage": {"total": 17040.00, "monthly_avg": 1420.00},
            "Shopping/Groceries": {"total": 13676.46, "monthly_avg": 1139.70},
        },
        "grand_total": 64287.16, "monthly_avg_total": 5357.26,
    })

    r.show_tool_call("preview_rule_application", {
        "pattern": "NETFLIX", "category_main": "Leisure",
        "category_sub": "subscription", "category_sub2": "music",
    })
    r.show_tool_result("preview_rule_application", {
        "would_match": 24,
        "sample_matches": [
            {"id": 1234, "date": "2025-12-01", "amount": -14.14, "memo": "NETFLIX.COM 9192"},
            {"id": 1235, "date": "2025-11-01", "amount": -14.14, "memo": "NETFLIX.COM 5893"},
        ],
        "proposed_classification": {
            "category_main": "Leisure", "category_sub": "subscription",
            "category_sub2": "music", "details": None,
        },
    })

    r.show_tool_call("model_scenario", {"scenario": "rate_change", "parameters": {"...": "..."}})
    r.show_tool_result("model_scenario", {
        "scenario": "rate_change",
        "current_monthly_surplus": 265.79,
        "new_monthly_surplus": -42.54,
        "gap": 308.33,
        "monthly_payment_delta": 308.33,
        "recommendations": [
            {"category": "Shopping/Groceries", "current_monthly": 1139.70, "suggested_monthly": 569.85, "potential_saving": 569.85, "type": "discretionary"},
        ],
    })

    r.show_tool_result("apply_classification_rule", {
        "rules_added": 1, "rule_id": 7, "transactions_reclassified": 24,
    })

    r.show_error("tool:bad_thing", "Something went wrong")

    r.show_usage(
        input_tokens=1248, output_tokens=187,
        cache_read=12943, cache_creation=0,
        cost_usd=0.0058, turn=2,
    )

    print("Eyeball the output above — if it looks right, the smoke test passed.")
