"""replay.py — render a recorded transcript through the Renderer protocol.

Reads a `logs/<ISO8601>.jsonl` file produced by `agent.transcript.Transcript`
and re-emits each event to a Renderer so the conversation appears (in the
terminal, via RichRenderer) as if it were happening now. Useful for:

  - Sharing past sessions without burning API budget.
  - Debugging surprising behaviour by stepping through the events in order.

Read-only. Never touches the DB. Never makes API calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.agent import Renderer, SilentRenderer  # noqa: E402


@dataclass
class ReplayStats:
    """Aggregates recomputed from the transcript as it's replayed."""
    turn_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    unknown_record_types: list[str] = field(default_factory=list)


def load_transcript(path: Path) -> Iterator[dict]:
    """Yield each JSONL record in order. Blank lines are skipped; malformed
    lines raise a ValueError that names the path and 1-based line number."""
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path}: malformed JSON at line {lineno}: {e.msg}"
                ) from e


def replay(
    path: Path,
    renderer: Renderer,
    *,
    delay_seconds: float = 0.0,
    show_header: bool = True,
) -> ReplayStats:
    """Walk the transcript and dispatch each record to the renderer.

    Returns ReplayStats with the recomputed totals. Unknown record types
    are logged to stderr and skipped (don't crash the replay).
    """
    stats = ReplayStats()
    header_printed = False

    for record in load_transcript(path):
        rtype = record.get("type")

        if rtype == "session_start":
            if show_header and not header_printed:
                sid = record.get("session_id", "?")
                started = _format_ts(record.get("ts"))
                _print_header(renderer, f"session {sid} — started {started}")
                header_printed = True
            continue

        if rtype == "user":
            renderer.show_user_text(record.get("content", ""))
            continue

        if rtype == "assistant":
            for block in record.get("content", []) or []:
                btype = block.get("type")
                if btype == "tool_use":
                    renderer.show_tool_call(block.get("name", "?"), block.get("input", {}) or {})
                elif btype == "text":
                    renderer.show_assistant_text(block.get("text", ""))
                # other block types (e.g. future "thinking") — skipped silently
            continue

        if rtype == "tool_result":
            renderer.show_tool_result(
                record.get("name", "?"),
                record.get("output"),
                is_error=bool(record.get("is_error", False)),
            )
            continue

        if rtype == "usage":
            tokens = record.get("tokens", {}) or {}
            input_tokens = int(tokens.get("in", 0))
            output_tokens = int(tokens.get("out", 0))
            cache_read = int(tokens.get("cache_read", 0))
            cache_creation = int(tokens.get("cache_creation", 0))
            cost_usd = float(record.get("cost_usd", 0.0))
            turn = int(record.get("turn", 0))

            renderer.show_usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read=cache_read,
                cache_creation=cache_creation,
                cost_usd=cost_usd,
                turn=turn,
            )

            stats.turn_count = max(stats.turn_count, turn)
            stats.tokens_in += input_tokens
            stats.tokens_out += output_tokens
            stats.cache_read += cache_read
            stats.cache_creation += cache_creation
            stats.cost_usd += cost_usd

            if delay_seconds > 0:
                time.sleep(delay_seconds)
            continue

        if rtype == "error":
            renderer.show_error(record.get("where", "?"), record.get("detail", ""))
            stats.errors += 1
            continue

        if rtype == "session_end":
            if show_header:
                reason = record.get("reason", "?")
                _print_header(
                    renderer,
                    f"session_end ({reason}) — turns={stats.turn_count} "
                    f"in={stats.tokens_in:,} out={stats.tokens_out:,} "
                    f"cache_read={stats.cache_read:,} cost=${stats.cost_usd:.4f}",
                )
            continue

        # Unknown — warn and skip.
        if rtype not in stats.unknown_record_types:
            stats.unknown_record_types.append(rtype)
        print(
            f"Warning: unknown record type {rtype!r} in {path} — skipped",
            file=sys.stderr,
        )

    return stats


# ---------------------------------------------------------------------------
# Header formatting
# ---------------------------------------------------------------------------

def _print_header(renderer: Renderer, text: str) -> None:
    """Print a session header/footer line. Uses the renderer's console if it
    has one (RichRenderer), otherwise falls back to plain stdout (SilentRenderer
    in tests). Either way it never crashes the replay loop."""
    console = getattr(renderer, "console", None)
    if console is not None:
        try:
            console.rule(text, style="dim")
            return
        except Exception:
            pass
    print(text)


def _format_ts(iso: str | None) -> str:
    """Return a short human-readable form of an ISO-8601 string, or '?' if
    unparseable. Keeps the header tidy."""
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return iso


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agent.replay",
        description="Re-render a recorded agent transcript (logs/<id>.jsonl).",
    )
    p.add_argument("path", help="Path to the .jsonl transcript to replay.")
    p.add_argument(
        "--delay-seconds", type=float, default=0.0,
        help="Pause this long after each turn's usage record. Default 0 (instant).",
    )
    p.add_argument(
        "--no-log-header", action="store_true",
        help="Suppress the session-start / session-end header lines.",
    )
    p.add_argument(
        "--silent", action="store_true",
        help="Use SilentRenderer (no output). Useful for sanity-checking a transcript parses.",
    )
    args = p.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"replay: file not found: {path}", file=sys.stderr)
        return 2

    if args.silent:
        renderer: Renderer = SilentRenderer()
        show_header = False  # silent = no chatter except the final one-line summary
    else:
        from agent.cli import RichRenderer  # local import — Rich is heavy
        renderer = RichRenderer()
        show_header = not args.no_log_header

    try:
        stats = replay(
            path, renderer,
            delay_seconds=args.delay_seconds,
            show_header=show_header,
        )
    except ValueError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 2

    if args.silent:
        # In silent mode the header lines are suppressed too, so emit a tiny
        # ASCII summary to stdout for scripting use cases.
        print(
            f"turns={stats.turn_count} "
            f"in={stats.tokens_in} out={stats.tokens_out} "
            f"cache_read={stats.cache_read} cost_usd={stats.cost_usd:.4f} "
            f"errors={stats.errors}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
