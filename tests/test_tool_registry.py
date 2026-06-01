"""Tests for agent.tool_registry — schema/dispatch pairing + B1 gate."""
from __future__ import annotations

import pytest

from agent import tool_registry
from agent.tool_registry import (
    ANTHROPIC_TOOLS,
    GATED_TOOLS,
    TOOL_FUNCTIONS,
    ApprovalRequiredError,
    _find_latest_approval_message,
    _regex_classify,
    check_approval,
    dispatch,
)


def test_every_schema_has_a_callable():
    for schema in ANTHROPIC_TOOLS:
        name = schema["name"]
        assert name in TOOL_FUNCTIONS, f"missing dispatch entry: {name}"
        assert callable(TOOL_FUNCTIONS[name])


def test_dispatch_read_only_tool(tmp_db):
    result = dispatch("get_unclassified_transactions", {"limit": 3})
    assert isinstance(result, list)
    assert len(result) <= 3


def test_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError, match="Unknown tool"):
        dispatch("nope_not_a_tool", {})


def test_registered_count_matches_modules():
    # 15 tools total across state (2), classification (9), scenarios (4).
    # Classification: get_unclassified_transactions, list_categories,
    # suggest_classification, preview_rule_application,
    # apply_classification_rule, preview_taxonomy_extension,
    # apply_taxonomy_extension, bulk_classify_async, check_batch_results.
    assert len(ANTHROPIC_TOOLS) == 15


# ---------------------------------------------------------------------------
# B1 — preview-before-apply gate
# ---------------------------------------------------------------------------

def test_gated_tools_covers_both_apply_paths():
    # B1 must cover apply_classification_rule (Step 4) AND
    # apply_taxonomy_extension (A3). Both write to classification_rules.
    assert "apply_classification_rule" in GATED_TOOLS
    assert "apply_taxonomy_extension" in GATED_TOOLS
    assert GATED_TOOLS["apply_classification_rule"] == "preview_rule_application"
    assert GATED_TOOLS["apply_taxonomy_extension"] == "preview_taxonomy_extension"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("yes", "approve"),
        ("Yes, go ahead", "approve"),
        ("looks right", "approve"),
        ("ok apply it", "approve"),
        ("do it", "approve"),
        ("please proceed", "approve"),
        ("no", "deny"),
        ("not yet", "deny"),
        ("wait, hold on", "deny"),
        ("cancel that please", "deny"),
        ("hmm, can you re-check the count?", "ambiguous"),
        ("", "ambiguous"),
        ("   ", "ambiguous"),
        # both lists hit — must be ambiguous, never silently approve
        ("yes but actually no", "ambiguous"),
        # "don't apply that" hits both `don't` (deny) and `apply` (approve);
        # the gate correctly returns ambiguous and escalates to the LLM
        # fallback (which then refuses).
        ("don't apply that", "ambiguous"),
    ],
)
def test_regex_classify_table(text, expected):
    assert _regex_classify(text) == expected


def _preview_assistant(tool_use_id: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}],
    }


def _preview_result(tool_use_id: str, payload: str = '{"would_match": 7}') -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": payload}],
    }


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def test_find_latest_approval_message_finds_user_reply_after_preview():
    messages = [
        _user("show me a preview for tesco"),
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1"),
        _assistant_text("7 rows would match."),
        _user("yes apply it"),
        # the assistant message that contained the apply tool_use is what we'd
        # be running the gate against; it's NOT in `messages` here because the
        # gate runs before the apply assistant message is appended in real life,
        # BUT in agent.py the assistant message IS appended before dispatch.
        # Either way, the most-recent preview is still tu_1.
    ]
    found = _find_latest_approval_message("preview_rule_application", messages)
    assert found is not None
    user_text, preview_text = found
    assert user_text == "yes apply it"
    assert "would_match" in preview_text


def test_find_latest_approval_message_picks_most_recent_preview():
    messages = [
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1", '{"would_match": 7, "pattern": "old"}'),
        _user("yes apply it"),
        _preview_assistant("tu_2", "preview_rule_application"),
        _preview_result("tu_2", '{"would_match": 12, "pattern": "new"}'),
        _user("hmm, on second thought no"),
    ]
    found = _find_latest_approval_message("preview_rule_application", messages)
    assert found is not None
    user_text, preview_text = found
    assert user_text == "hmm, on second thought no"
    assert '"pattern": "new"' in preview_text  # got the newer preview's payload


def test_find_latest_approval_message_returns_none_with_no_preview():
    messages = [
        _user("apply rule X"),
    ]
    assert _find_latest_approval_message("preview_rule_application", messages) is None


def test_find_latest_approval_message_returns_none_when_no_user_reply_after_preview():
    # preview + apply emitted in the same assistant turn — no user reply between
    # them. The gate must reject.
    messages = [
        _user("classify tesco"),
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "preview_rule_application", "input": {}},
                {"type": "tool_use", "id": "tu_2", "name": "apply_classification_rule", "input": {}},
            ],
        },
        _preview_result("tu_1"),
    ]
    assert _find_latest_approval_message("preview_rule_application", messages) is None


def test_check_approval_accepts_clear_yes():
    messages = [
        _user("preview tesco"),
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1"),
        _assistant_text("7 rows would match."),
        _user("yes apply"),
    ]
    # Should not raise.
    check_approval("apply_classification_rule", messages)


def test_check_approval_rejects_clear_no():
    messages = [
        _user("preview tesco"),
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1"),
        _assistant_text("7 rows would match."),
        _user("no don't"),
    ]
    with pytest.raises(ApprovalRequiredError, match="refusal"):
        check_approval("apply_classification_rule", messages)


def test_check_approval_rejects_when_no_preview():
    messages = [
        _user("apply the tesco rule"),
    ]
    with pytest.raises(ApprovalRequiredError, match="prior preview_rule_application"):
        check_approval("apply_classification_rule", messages)


def test_check_approval_escalates_ambiguous_to_llm(monkeypatch):
    calls = []

    def fake_llm(user_text, preview_text):
        calls.append((user_text, preview_text))
        return "deny"

    monkeypatch.setattr(tool_registry, "_llm_classify", fake_llm)

    messages = [
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1", '{"would_match": 7}'),
        _user("hmm let me think about it"),
    ]
    with pytest.raises(ApprovalRequiredError, match="did not clearly authorise"):
        check_approval("apply_classification_rule", messages)
    assert len(calls) == 1
    assert calls[0][0] == "hmm let me think about it"
    assert "would_match" in calls[0][1]


def test_check_approval_llm_can_rescue_ambiguous_phrasing(monkeypatch):
    monkeypatch.setattr(tool_registry, "_llm_classify", lambda u, p: "approve")
    messages = [
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1"),
        _user("yeah that seems fine to me"),  # ambiguous to regex
    ]
    # Should not raise.
    check_approval("apply_classification_rule", messages)


def test_dispatch_fires_gate_when_messages_present(monkeypatch):
    # Replace the underlying tool so the test doesn't depend on DB state.
    monkeypatch.setitem(
        tool_registry.TOOL_FUNCTIONS,
        "apply_classification_rule",
        lambda **kw: {"rules_added": 1, "rule_id": 99, "transactions_reclassified": 0},
    )

    # Gate fires with empty messages → no preview found → reject.
    with pytest.raises(ApprovalRequiredError):
        dispatch(
            "apply_classification_rule",
            {"pattern": ".*", "category_main": "X"},
            messages=[],
        )


@pytest.mark.llm
def test_llm_fallback_blocks_genuinely_ambiguous_reply():
    """Real Haiku call. Picks a reply that's clearly NOT approval but doesn't
    hit the deny regexes. The LLM should classify it as deny or ambiguous,
    NOT approve — and the gate must therefore raise."""
    messages = [
        _preview_assistant("tu_1", "preview_rule_application"),
        _preview_result("tu_1", '{"would_match": 12, "pattern": ".*tesco.*"}'),
        # Ambiguous to the regex (no yes/no/ok), but a thinking-out-loud reply,
        # not an authorisation. Haiku should treat it as ambiguous/deny.
        _user("I'm honestly not sure — what does the sample look like?"),
    ]
    with pytest.raises(ApprovalRequiredError):
        check_approval("apply_classification_rule", messages)


def test_dispatch_skips_gate_when_messages_omitted(monkeypatch):
    # Backwards-compat: existing tests / callers that don't pass `messages`
    # opt out of the gate. Only the agent loop opts in.
    called = {}

    def fake_apply(**kw):
        called["yes"] = True
        return {"rules_added": 1, "rule_id": 99, "transactions_reclassified": 0}

    monkeypatch.setitem(tool_registry.TOOL_FUNCTIONS, "apply_classification_rule", fake_apply)

    result = dispatch("apply_classification_rule", {"pattern": ".*", "category_main": "X"})
    assert called.get("yes") is True
    assert result["rule_id"] == 99
