"""state.py — agent_state CRUD tools (SPEC §5.3).

The agent_state table is the cross-session knowledge store: facts the agent
has learned about this user's finances, persisted across sessions. Examples:
  - avg_monthly_groceries_6m -> 412.50 (calculated, high confidence)
  - primary_income_source -> "salary" (inferred from patterns)
  - mortgage_rate_change_date -> "2027-03-01" (user_confirmed)

Values are JSON-serialised so dicts/lists round-trip cleanly. The
value_type column records the original Python type so we can deserialise
without ambiguity (was "3.14" a float or a str?).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow `python -m agent.tools.state` to find the sibling db package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.database import open_db  # noqa: E402


# ---------------------------------------------------------------------------
# Type tagging
# ---------------------------------------------------------------------------

_TYPE_TAGS = {
    bool: "bool",
    int: "int",
    float: "float",
    str: "str",
    list: "list",
    dict: "dict",
    type(None): "null",
}

_DESERIALISERS = {
    "bool": lambda s: bool(json.loads(s)),
    "int": lambda s: int(json.loads(s)),
    "float": lambda s: float(json.loads(s)),
    "str": json.loads,
    "list": json.loads,
    "dict": json.loads,
    "null": lambda s: None,
}


def _detect_type(value: Any) -> str:
    # bool is a subclass of int — check it first.
    tag = _TYPE_TAGS.get(type(value))
    if tag is None:
        raise TypeError(
            f"set_agent_state value must be JSON-serialisable scalar/list/dict; "
            f"got {type(value).__name__}"
        )
    return tag


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_agent_state(key: str) -> dict | None:
    """Return the stored entry for `key`, or None if not set.

    Result shape: {value, confidence, rationale, updated_at}.
    """
    with open_db() as conn:
        row = conn.execute(
            "SELECT value, value_type, confidence, rationale, updated_at "
            "FROM agent_state WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    return {
        "value": _DESERIALISERS[row["value_type"]](row["value"]),
        "confidence": row["confidence"],
        "rationale": row["rationale"],
        "updated_at": row["updated_at"],
    }


def set_agent_state(
    key: str,
    value: Any,
    rationale: str,
    confidence: str = "inferred",
    session_id: str | None = None,
) -> dict:
    """Persist a fact to agent_state. Upserts on the key.

    `rationale` is required — the agent must explain why this is worth
    storing (SPEC §5.3). `confidence` is one of inferred|calculated|user_confirmed.
    Returns {success: True} on insert/update.
    """
    if confidence not in ("inferred", "calculated", "user_confirmed"):
        raise ValueError(
            f"confidence must be inferred|calculated|user_confirmed, got {confidence!r}"
        )
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required and cannot be blank")

    value_type = _detect_type(value)
    serialised = json.dumps(value)

    with open_db() as conn:
        conn.execute(
            """
            INSERT INTO agent_state (key, value, value_type, rationale,
                                     confidence, session_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                value_type = excluded.value_type,
                rationale = excluded.rationale,
                confidence = excluded.confidence,
                session_id = excluded.session_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, serialised, value_type, rationale, confidence, session_id),
        )
    return {"success": True}


# ---------------------------------------------------------------------------
# JSON Schemas for the Anthropic tool registry
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "get_agent_state",
        "description": (
            "Read a stored fact from the agent's cross-session knowledge store. "
            "Returns {value, confidence, rationale, updated_at} or null if the key "
            "doesn't exist. Use to recall what you previously learned about this "
            "user (e.g. 'mortgage_rate_change_date', 'avg_monthly_groceries_6m')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The state key to read."}
            },
            "required": ["key"],
        },
    },
    {
        "name": "set_agent_state",
        "description": (
            "Persist a fact the agent has learned, durable across sessions. Only "
            "store things you would otherwise have to look up again next session. "
            "Do NOT use this for conversational scratch work — that lives in the "
            "messages array and dies with the session. A rationale is required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Stable, descriptive key (snake_case).",
                },
                "value": {
                    "description": "JSON-serialisable value (number, string, bool, list, dict).",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this is worth persisting across sessions.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["inferred", "calculated", "user_confirmed"],
                    "description": "How sure you are. 'user_confirmed' = user said so explicitly.",
                },
            },
            "required": ["key", "value", "rationale"],
        },
    },
]
