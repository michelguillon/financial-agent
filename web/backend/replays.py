"""replays.py — curated replay catalogue for the web UI's Live/Replay toggle.

The catalogue is intentionally a Python dict, not a JSON manifest file:
  - Content + metadata + path validation stay co-located.
  - Adding a new replay is a code change, which goes through the same
    review as everything else in this repo.
  - Easy to unit-test (`assert REPLAY_CATALOGUE["demo_3turn"].path.exists()`).

When the catalogue grows past ~3 entries, revisit and consider a manifest.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPLAYS_DIR = PROJECT_ROOT / "web" / "replays"


@dataclass(frozen=True)
class ReplayMeta:
    id: str
    title: str
    summary: str
    path: Path


REPLAY_CATALOGUE: dict[str, ReplayMeta] = {
    "demo_3turn": ReplayMeta(
        id="demo_3turn",
        title="3-turn demo — spending, scenario, classification",
        summary=(
            "A short canned conversation covering the agent's three main "
            "capabilities: a 12-month spending summary, a mortgage-rate-rise "
            "scenario, and suggesting categories for unclassified transactions."
        ),
        path=REPLAYS_DIR / "demo_3turn.jsonl",
    ),
}


def get_replay(replay_id: str) -> ReplayMeta | None:
    """Lookup; returns None if unknown. Callers raise 404 on None."""
    return REPLAY_CATALOGUE.get(replay_id)


def list_replays() -> list[dict]:
    """JSON-serialisable summaries for the GET /api/replays endpoint."""
    return [
        {"id": m.id, "title": m.title, "summary": m.summary}
        for m in REPLAY_CATALOGUE.values()
    ]
