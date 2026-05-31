"""transcript.py — JSONL session logger for the agent loop.

One file per session under logs/<ISO8601-timestamp>.jsonl. Each record is
one JSON object on its own line, describing a discrete event in the
conversation:

  - {"type": "user", "content": "..."}                       user input
  - {"type": "assistant", "content": [blocks]}               full assistant message
  - {"type": "tool_result", "tool_use_id": "...", ...}       tool dispatch result
  - {"type": "usage", "turn": N, "tokens": {...}, "cost_usd": ...}
  - {"type": "error", "where": "...", "detail": "..."}       any caught error

Append-only, line-buffered so partial sessions are still readable if the
process dies. Used both for replay/debugging and as future fine-tuning
data.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"


class Transcript:
    """JSONL session writer. Use as a context manager:

        with Transcript() as t:
            t.record("user", content="hello")
            t.record("usage", turn=1, tokens={"in": 100, "out": 50}, cost_usd=0.0005)
    """

    def __init__(self, log_dir: Path | None = None, session_id: str | None = None):
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.session_id = session_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        self.path = self.log_dir / f"{self.session_id}.jsonl"
        self._fh = None

    def __enter__(self) -> "Transcript":
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Line-buffered (buffering=1) so a Ctrl+C still leaves a usable file.
        self._fh = self.path.open("a", encoding="utf-8", buffering=1)
        self.record("session_start", session_id=self.session_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            if exc is not None:
                self.record("session_end", reason="exception", detail=str(exc))
            else:
                self.record("session_end", reason="normal")
            self._fh.close()
            self._fh = None

    def record(self, type: str, **fields: Any) -> None:
        """Write one JSON record. `type` is the discriminator; everything
        else goes into the same object."""
        if self._fh is None:
            raise RuntimeError("Transcript used outside of context manager")
        obj: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "type": type}
        obj.update(fields)
        self._fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
