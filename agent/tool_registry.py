"""tool_registry.py — assembles the Anthropic tools list and the
name -> Python-callable dispatch table for the agent loop.

Each tool module exports two things:
  - `SCHEMAS`: list of JSON-Schema definitions for the Anthropic API
  - the implementation functions themselves

This module imports both, validates the shapes, and exposes:
  - `ANTHROPIC_TOOLS`: pass directly to `client.messages.create(tools=...)`
  - `TOOL_FUNCTIONS`: {name: callable} — the agent loop dispatches tool_use
    blocks against this dict.

Single source of truth — both API and dispatch derive from the same per-module
SCHEMAS list, so they can't drift apart.

It also enforces the preview-before-apply contract at dispatch time (B1).
The `GATED_TOOLS` map names each apply_* tool that must not run without a
matching preview_* call earlier in the conversation AND an approval signal
in the user message that followed. See `check_approval`.
"""

from __future__ import annotations

import re
from typing import Literal

from agent.tools import classification, scenarios, state

# Modules in registration order. Within each module, SCHEMAS lists the tools
# in the order their definitions appear in code.
_TOOL_MODULES = (state, classification, scenarios)


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

ANTHROPIC_TOOLS: list[dict] = []
TOOL_FUNCTIONS: dict[str, callable] = {}


def _validate_schema(schema: dict, module_name: str) -> None:
    """Cheap shape check — catches typos before the API does."""
    for key in ("name", "description", "input_schema"):
        if key not in schema:
            raise ValueError(
                f"{module_name}: tool schema missing required key {key!r}: {schema}"
            )
    if not isinstance(schema["input_schema"], dict):
        raise ValueError(
            f"{module_name}: tool {schema['name']!r} input_schema must be a dict"
        )
    if schema["input_schema"].get("type") != "object":
        raise ValueError(
            f"{module_name}: tool {schema['name']!r} input_schema.type must be 'object'"
        )


for module in _TOOL_MODULES:
    for schema in module.SCHEMAS:
        _validate_schema(schema, module.__name__)
        name = schema["name"]

        if name in TOOL_FUNCTIONS:
            raise ValueError(f"Duplicate tool name across modules: {name!r}")

        func = getattr(module, name, None)
        if func is None or not callable(func):
            raise ValueError(
                f"{module.__name__}: schema names {name!r} but module has no "
                "matching callable. Add the function or rename the schema."
            )

        ANTHROPIC_TOOLS.append(schema)
        TOOL_FUNCTIONS[name] = func


# ---------------------------------------------------------------------------
# Preview-before-apply gate (B1)
# ---------------------------------------------------------------------------

# apply_* tool -> the preview_* tool that must precede it in conversation.
GATED_TOOLS: dict[str, str] = {
    "apply_classification_rule": "preview_rule_application",
    "apply_taxonomy_extension": "preview_taxonomy_extension",
}


class ApprovalRequiredError(Exception):
    """Raised by the dispatch gate when a gated apply_* tool is invoked
    without a matching preview_* call + approval signal in conversation
    history. The agent loop converts this into an is_error tool_result,
    so the agent can re-show the preview and ask the user explicitly."""


_APPROVE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\byes\b", r"\byeah\b", r"\byep\b", r"\byup\b", r"\bsure\b",
        r"\bok(ay)?\b", r"\bapply\b", r"\bgo ahead\b", r"\bdo it\b",
        r"\bproceed\b", r"\bconfirmed?\b", r"\blooks (right|good|correct|fine)\b",
        r"\bsounds (right|good|fine)\b", r"\bplease (do|apply|proceed)\b",
        r"\bgo for it\b", r"\bcommit\b", r"\bplease go\b",
    )
]

_DENY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bno\b", r"\bnope\b", r"\bnot yet\b", r"\bnot now\b", r"\bwait\b",
        r"\bstop\b", r"\bcancel\b", r"\babort\b", r"\bhold (on|off)\b",
        r"\bdon'?t\b", r"\bdo not\b", r"\breject\b",
        r"\b(let'?s )?(not|skip)\b",
    )
]


Verdict = Literal["approve", "deny", "ambiguous"]


def _regex_classify(text: str) -> Verdict:
    """Cheap deterministic pass over an approval/denial phrase list.

    Returns:
        "approve"  — at least one approve pattern hit, no deny pattern hit
        "deny"     — at least one deny pattern hit, no approve pattern hit
        "ambiguous"— none, both, or only contradictory matches; caller may
                     escalate to the LLM fallback.
    """
    if not text or not text.strip():
        return "ambiguous"
    approve_hit = any(p.search(text) for p in _APPROVE_PATTERNS)
    deny_hit = any(p.search(text) for p in _DENY_PATTERNS)
    if approve_hit and not deny_hit:
        return "approve"
    if deny_hit and not approve_hit:
        return "deny"
    return "ambiguous"


def _find_latest_approval_message(
    expected_preview: str, messages: list[dict]
) -> tuple[str, str] | None:
    """Locate the user reply that should be evaluated for approval.

    Returns (user_text, preview_text) where:
        user_text     — the first plain-string user message AFTER the most
                        recent assistant tool_use for `expected_preview`.
        preview_text  — the corresponding tool_result content (passed to the
                        LLM fallback for additional context). May be "" if
                        the preview hadn't produced a result by the time the
                        user replied (shouldn't happen in practice).

    Returns None if no matching preview tool_use is found, or if there is
    no plain-string user message after it (e.g. the agent emitted preview
    and apply in the same assistant turn — that's exactly what the gate
    is meant to catch).
    """
    prev_idx = -1
    prev_tool_use_id = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == expected_preview
            ):
                prev_idx = i
                prev_tool_use_id = block.get("id")
                break
        if prev_idx != -1:
            break

    if prev_idx == -1:
        return None

    preview_text = ""
    user_text = None
    for j in range(prev_idx + 1, len(messages)):
        msg = messages[j]
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == prev_tool_use_id
                ):
                    inner = block.get("content")
                    preview_text = inner if isinstance(inner, str) else str(inner)
            continue
        if isinstance(content, str):
            user_text = content
            break

    if user_text is None:
        return None
    return user_text, preview_text


def _llm_classify(user_text: str, preview_text: str) -> Verdict:
    """Haiku 4.5 fallback for ambiguous regex verdicts.

    Forced tool-use so the model can only emit one of three labels. Same
    pattern as classification.suggest_classification.
    """
    from agent.claude_helpers import (  # local import to avoid cycle at module load
        CLASSIFIER_MODEL,
        call_with_retry,
        get_client,
    )

    tool = {
        "name": "submit_verdict",
        "description": "Classify the user's reply as approve, deny, or ambiguous.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["approve", "deny", "ambiguous"],
                    "description": (
                        "approve = user clearly authorises the proposed "
                        "irreversible action; deny = user clearly refuses or "
                        "wants to pause/change it; ambiguous = anything else "
                        "(off-topic reply, question back, hedge)."
                    ),
                },
            },
            "required": ["verdict"],
        },
    }
    system = (
        "You are a strict approval-signal classifier. A finance agent has "
        "asked the user to approve an irreversible database write. Given the "
        "preview the user saw and the user's reply, decide whether the reply "
        "constitutes clear, affirmative approval. When in doubt, choose "
        "'ambiguous' or 'deny' — never 'approve'. You MUST call submit_verdict."
    )
    user = (
        f"Preview the user was shown (truncated):\n{preview_text[:2000]}\n\n"
        f"User's reply:\n{user_text}\n"
    )

    response = call_with_retry(
        get_client().messages.create,
        model=CLASSIFIER_MODEL,
        max_tokens=128,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_verdict"},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_verdict":
            verdict = dict(block.input).get("verdict")
            if verdict in ("approve", "deny", "ambiguous"):
                return verdict
    return "ambiguous"


def check_approval(tool_name: str, messages: list[dict]) -> None:
    """Raise ApprovalRequiredError unless the conversation contains a
    matching preview_* call followed by a user message that reads as
    approval. Pure side-effect-free check; returns None on success.
    """
    expected_preview = GATED_TOOLS[tool_name]
    found = _find_latest_approval_message(expected_preview, messages)
    if found is None:
        raise ApprovalRequiredError(
            f"{tool_name} requires a prior {expected_preview} call AND an "
            "explicit user approval in the message that followed. Neither "
            "was found. Call the preview tool first, present its output, "
            "and wait for the user to confirm before retrying."
        )
    user_text, preview_text = found
    verdict = _regex_classify(user_text)
    if verdict == "approve":
        return
    if verdict == "deny":
        raise ApprovalRequiredError(
            f"{tool_name} blocked: the user's most recent reply after the "
            f"{expected_preview} output reads as a refusal "
            f"({user_text[:120]!r}). Do not retry without asking the user "
            "for a fresh, unambiguous approval."
        )
    # Ambiguous — escalate to Haiku.
    verdict = _llm_classify(user_text, preview_text)
    if verdict == "approve":
        return
    raise ApprovalRequiredError(
        f"{tool_name} blocked: the user's reply after the {expected_preview} "
        f"output did not clearly authorise the action (verdict={verdict!r}, "
        f"reply={user_text[:120]!r}). Re-show the preview, ask the user to "
        "confirm with a clear yes/no, and only retry after an unambiguous "
        "approval."
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(
    tool_name: str,
    tool_input: dict,
    *,
    messages: list[dict] | None = None,
) -> object:
    """Invoke a registered tool by name.

    Raises:
        KeyError                — `tool_name` isn't registered.
        ApprovalRequiredError   — `tool_name` is in GATED_TOOLS and the
                                  conversation lacks a matching preview_*
                                  call + user approval. Skipped if
                                  `messages` is None (tests calling
                                  dispatch directly opt out of the gate).

    Argument validation is the tool function's own responsibility.
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise KeyError(
            f"Unknown tool: {tool_name!r}. "
            f"Registered: {sorted(TOOL_FUNCTIONS)}"
        )
    if tool_name in GATED_TOOLS and messages is not None:
        check_approval(tool_name, messages)
    return TOOL_FUNCTIONS[tool_name](**tool_input)
