"""Entry point: `python -m agent`.

Interactive REPL — reads user input, runs one turn, repeats until /exit
or Ctrl+D/Ctrl+C. Writes a transcript at logs/<timestamp>.jsonl unless
--no-log is passed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.agent import Session, run_turn  # noqa: E402
from agent.cli import RichRenderer  # noqa: E402
from agent.transcript import Transcript  # noqa: E402
from db.database import get_data_source  # noqa: E402


HELP = """
Commands:
  /help     show this message
  /exit     end the session (Ctrl+D and Ctrl+C also work)
  /tokens   show running token totals + cost so far

Anything else is a message to the agent.
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent", description=__doc__.splitlines()[0])
    p.add_argument(
        "--source", choices=["synthetic", "real", "auto"], default="auto",
        help="Which data set to query (default: auto — uses data/real/ if present).",
    )
    p.add_argument(
        "--no-log", action="store_true",
        help="Don't write a transcript file.",
    )
    args = p.parse_args(argv)

    source = get_data_source() if args.source == "auto" else args.source

    renderer = RichRenderer()
    session = Session(data_source=source)

    renderer.console.rule(f"Personal finance agent — data source: {source}")
    renderer.console.print(
        "Type your question. /help for commands. /exit to quit.",
        style="dim",
    )
    renderer.console.print()

    transcript_cm = (
        Transcript() if not args.no_log else _NullTranscript()
    )

    with transcript_cm as t:
        while True:
            try:
                user = renderer.prompt("You")
            except (KeyboardInterrupt, EOFError):
                renderer.console.print()
                break

            user = user.strip()
            if not user:
                continue
            if user in ("/exit", "/quit"):
                break
            if user == "/help":
                renderer.console.print(HELP, style="dim")
                continue
            if user == "/tokens":
                renderer.console.print(
                    f"turns: {session.turn_count}  "
                    f"in: {session.tokens_in:,}  out: {session.tokens_out:,}  "
                    f"cache_read: {session.cache_read:,}  "
                    f"cost: ${session.cost_usd:.4f}",
                    style="dim",
                )
                continue

            try:
                run_turn(session, user, renderer, t)
            except Exception as e:
                renderer.show_error("agent", f"{type(e).__name__}: {e}")
                if t is not None and hasattr(t, "record"):
                    try:
                        t.record("error", where="agent_loop", detail=f"{type(e).__name__}: {e}")
                    except Exception:
                        pass

    # Final summary
    renderer.console.rule("Session summary")
    renderer.console.print(
        f"turns: {session.turn_count}  "
        f"in: {session.tokens_in:,}  out: {session.tokens_out:,}  "
        f"cache_read: {session.cache_read:,}  "
        f"cost: ${session.cost_usd:.4f}",
        style="dim",
    )
    if not args.no_log:
        renderer.console.print(f"transcript: {transcript_cm.path}", style="dim")
    return 0


class _NullTranscript:
    """No-op stand-in when --no-log is passed."""
    path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def record(self, *a, **kw):
        pass


if __name__ == "__main__":
    sys.exit(main())
