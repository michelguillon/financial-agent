"""Tests for agent.tools.classification — preview/apply two-step + LLM suggest."""
from __future__ import annotations

import pytest

from agent.tools.classification import (
    apply_classification_rule,
    apply_taxonomy_extension,
    get_unclassified_transactions,
    list_categories,
    preview_rule_application,
    preview_taxonomy_extension,
    suggest_classification,
)
from db.database import open_db


def test_get_unclassified_returns_missing_rows(tmp_db):
    rows = get_unclassified_transactions(limit=5)
    assert 0 < len(rows) <= 5
    for r in rows:
        assert "memo" in r and "amount" in r and "date" in r


def test_list_categories_has_expected_mains(tmp_db):
    taxonomy = list_categories()
    expected = {"Income", "House", "Shopping", "Transport", "Leisure",
                "Bills", "Savings", "Withdrawal", "Health", "Travel"}
    missing = expected - set(taxonomy.keys())
    assert not missing, f"taxonomy missing main categories: {missing}"


def test_a2_new_subs_present_in_taxonomy(tmp_db):
    taxonomy = list_categories()
    assert "rail" in taxonomy.get("Transport", {}), \
        "A2: Transport/rail missing from taxonomy"
    leisure_sub_video = taxonomy.get("Leisure", {}).get("subscription", [])
    assert "video" in leisure_sub_video, \
        f"A2: Leisure/subscription/video missing; got {leisure_sub_video}"
    assert "accommodation" in taxonomy.get("Travel", {}), \
        "A2: Travel/accommodation missing from taxonomy"


def test_preview_does_not_mutate(tmp_db):
    before = get_unclassified_transactions(limit=1000)
    preview_rule_application(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    after = get_unclassified_transactions(limit=1000)
    assert len(before) == len(after)


def test_preview_and_apply_counts_match(tmp_db):
    preview = preview_rule_application(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    assert preview["would_match"] >= 10

    result = apply_classification_rule(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    assert result["rules_added"] == 1
    assert result["transactions_reclassified"] == preview["would_match"]


def test_apply_removes_rows_from_missing(tmp_db):
    apply_classification_rule(
        pattern="NETFLIX", category_main="Leisure",
        category_sub="subscription", category_sub2="entertainment",
    )
    remaining = get_unclassified_transactions(limit=1000)
    netflix = [r for r in remaining if "NETFLIX" in r["memo"]]
    assert netflix == []


# ---------------------------------------------------------------------------
# A3 — extend_taxonomy (preview + apply for NEW (main, sub, sub2) tuples)
# ---------------------------------------------------------------------------

# APPLE.COM/BILL is in generate_synthetic.py's NOISE_MEMOS pool and no seed
# rule matches it, so the synthetic CSV reliably contains APPLE.COM rows in
# Missing — usable as the test merchant for the new-taxonomy-entry flow.


def test_preview_taxonomy_extension_accepts_new_tuple(tmp_db):
    out = preview_taxonomy_extension(
        category_main="Shopping", category_sub="digital", category_sub2="apps",
        pattern=".*APPLE\\.COM",
    )
    assert out["is_new"] is True
    assert out["proposed_taxonomy_entry"]["category_main"] == "Shopping"
    assert out["would_match"] > 0
    assert len(out["sample_matches"]) > 0


def test_preview_taxonomy_extension_rejects_existing_tuple(tmp_db):
    # ('Travel', 'accommodation', 'hotel') already exists post-A2.
    with pytest.raises(ValueError, match="already exists"):
        preview_taxonomy_extension(
            category_main="Travel", category_sub="accommodation",
            category_sub2="hotel", pattern=".*BOOKING\\.COM",
        )


def test_preview_taxonomy_extension_rejects_zero_matches(tmp_db):
    with pytest.raises(ValueError, match="0 Missing rows"):
        preview_taxonomy_extension(
            category_main="Shopping", category_sub="digital", category_sub2="apps",
            pattern="ZZZ_NEVER_MATCHES_ANY_MEMO",
        )


def test_apply_taxonomy_extension_lands_rule_and_reclassifies(tmp_db):
    result = apply_taxonomy_extension(
        category_main="Shopping", category_sub="digital", category_sub2="apps",
        pattern=".*APPLE\\.COM",
    )
    assert result["taxonomy_entry_added"]["category_main"] == "Shopping"
    assert result["transactions_reclassified"] > 0

    # Rule landed with added_by='agent' (not 'seed' — survives migrate.py re-seed).
    with open_db() as conn:
        row = conn.execute(
            "SELECT added_by, category_main, category_sub, category_sub2 "
            "FROM classification_rules WHERE id = ?",
            (result["rule_id"],),
        ).fetchone()
    assert row["added_by"] == "agent"
    assert row["category_sub2"] == "apps"

    # New tuple now appears in list_categories.
    taxonomy = list_categories()
    assert "apps" in taxonomy["Shopping"].get("digital", []), \
        f"Shopping/digital/apps missing from taxonomy: {taxonomy.get('Shopping')}"

    # APPLE.COM rows no longer in Missing.
    remaining = get_unclassified_transactions(limit=1000)
    apple = [r for r in remaining if "APPLE.COM" in r["memo"]]
    assert apple == []


def test_apply_taxonomy_extension_rejects_existing_tuple(tmp_db):
    with pytest.raises(ValueError, match="already exists"):
        apply_taxonomy_extension(
            category_main="Leisure", category_sub="subscription",
            category_sub2="video", pattern=".*APPLE\\.COM",
        )


@pytest.mark.llm
def test_suggest_classification_for_dishoom(tmp_db):
    out = suggest_classification(
        memo="DISHOOM SHOREDITCH 1234",
        amount=-45.20,
        account_name="Amex",
    )
    assert out["category_main"] == "Leisure", \
        f"Haiku misclassified Dishoom: {out['category_main']!r}"
    assert "DISHOOM" in out["suggested_pattern"].upper()


# ---------------------------------------------------------------------------
# C2 — bulk_classify_async + check_batch_results
# ---------------------------------------------------------------------------

import json  # noqa: E402

from agent.tools.classification import (  # noqa: E402
    _build_batch_request,
    _parse_batch_result,
    bulk_classify_async,
    check_batch_results,
)


class _FakeBatchCreateResponse:
    def __init__(self, batch_id: str):
        self.id = batch_id


class _FakeToolUseBlock:
    def __init__(self, name: str, input_dict: dict):
        self.type = "tool_use"
        self.name = name
        self.input = input_dict


class _FakeMessage:
    def __init__(self, content_blocks: list, input_tokens: int = 250, output_tokens: int = 60):
        self.content = content_blocks

        class _Usage:
            pass
        u = _Usage()
        u.input_tokens = input_tokens
        u.output_tokens = output_tokens
        self.usage = u


class _FakeResultEnvelope:
    def __init__(self, type_: str, message: _FakeMessage | None = None, error_msg: str | None = None):
        self.type = type_
        self.message = message

        class _Err:
            pass
        self.error = _Err() if error_msg is not None else None
        if error_msg is not None:
            self.error.message = error_msg


class _FakeBatchResultRow:
    def __init__(self, custom_id: str, envelope: _FakeResultEnvelope):
        self.custom_id = custom_id
        self.result = envelope


class _FakeBatchRetrieve:
    def __init__(self, processing_status: str, succeeded: int = 0, expired: int = 0, canceled: int = 0):
        self.processing_status = processing_status

        class _Counts:
            pass
        c = _Counts()
        c.succeeded = succeeded
        c.expired = expired
        c.canceled = canceled
        self.request_counts = c


class _FakeBatchesAPI:
    """Mock for client.messages.batches.* — records calls for assertions."""

    def __init__(self):
        self.create_calls: list[dict] = []
        self.retrieve_calls: list[str] = []
        self.results_calls: list[str] = []
        self.create_response = _FakeBatchCreateResponse("batch_test_001")
        self.retrieve_response: _FakeBatchRetrieve | None = None
        self.results_response: list[_FakeBatchResultRow] = []

    def create(self, *, requests):
        self.create_calls.append({"requests": requests})
        return self.create_response

    def retrieve(self, batch_id: str):
        self.retrieve_calls.append(batch_id)
        if self.retrieve_response is None:
            raise RuntimeError("test fixture didn't set retrieve_response")
        return self.retrieve_response

    def results(self, batch_id: str):
        self.results_calls.append(batch_id)
        return iter(self.results_response)


class _FakeMessagesAPI:
    def __init__(self):
        self.batches = _FakeBatchesAPI()


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessagesAPI()


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Patch agent.claude_helpers.get_client to a recording fake. Also
    bypass call_with_retry so the test stays sync + visible."""
    fake = _FakeAnthropicClient()
    monkeypatch.setattr("agent.claude_helpers.get_client", lambda: fake)
    monkeypatch.setattr(
        "agent.claude_helpers.call_with_retry",
        lambda func, *a, **kw: func(*a, **kw),
    )
    yield fake


def _sample_memos() -> list[dict]:
    return [
        {"id": 101, "memo": "APPLE.COM/BILL", "amount": -7.99, "account_name": "Current"},
        {"id": 102, "memo": "GREGGS LONDON 22", "amount": -4.50, "account_name": "Current"},
        {"id": 103, "memo": "AMAZON UK MARKETPLACE", "amount": -34.10, "account_name": "Amex"},
    ]


def test_bulk_classify_async_submits_one_request_per_memo(tmp_db, fake_anthropic):
    memos = _sample_memos()
    out = bulk_classify_async(memos)
    assert out["status"] == "in_progress"
    assert out["batch_id"] == "batch_test_001"
    assert out["memos_count"] == 3

    # The fake recorded exactly one create() call with 3 properly-shaped requests.
    assert len(fake_anthropic.messages.batches.create_calls) == 1
    requests = fake_anthropic.messages.batches.create_calls[0]["requests"]
    assert len(requests) == 3
    assert {r["custom_id"] for r in requests} == {"tx-101", "tx-102", "tx-103"}
    for r in requests:
        params = r["params"]
        assert params["tool_choice"] == {"type": "tool", "name": "submit_classification"}
        assert params["tools"][0]["name"] == "submit_classification"


def test_bulk_classify_async_persists_pending_row(tmp_db, fake_anthropic):
    bulk_classify_async(_sample_memos())
    with open_db() as conn:
        row = conn.execute(
            "SELECT batch_id, status, memos_count, transaction_ids FROM pending_batches "
            "WHERE batch_id = 'batch_test_001'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "in_progress"
    assert row["memos_count"] == 3
    assert json.loads(row["transaction_ids"]) == [101, 102, 103]


def test_bulk_classify_async_rejects_empty_memos(tmp_db, fake_anthropic):
    with pytest.raises(ValueError, match="at least one"):
        bulk_classify_async([])


def test_check_batch_results_in_progress_when_not_ended(tmp_db, fake_anthropic):
    bulk_classify_async(_sample_memos())
    fake_anthropic.messages.batches.retrieve_response = _FakeBatchRetrieve(
        processing_status="in_progress",
    )
    out = check_batch_results("batch_test_001")
    assert out["status"] == "in_progress"
    assert out["memos_count"] == 3
    # Still in_progress in the DB.
    with open_db() as conn:
        row = conn.execute(
            "SELECT status FROM pending_batches WHERE batch_id='batch_test_001'",
        ).fetchone()
    assert row["status"] == "in_progress"


def _succeeded_row(custom_id: str, suggestion: dict) -> _FakeBatchResultRow:
    msg = _FakeMessage([_FakeToolUseBlock("submit_classification", suggestion)])
    envelope = _FakeResultEnvelope("succeeded", message=msg)
    return _FakeBatchResultRow(custom_id, envelope)


def test_check_batch_results_returns_suggestions_when_done(tmp_db, fake_anthropic):
    bulk_classify_async(_sample_memos())
    api = fake_anthropic.messages.batches
    api.retrieve_response = _FakeBatchRetrieve(processing_status="ended", succeeded=3)
    api.results_response = [
        _succeeded_row("tx-101", {
            "category_main": "Leisure", "category_sub": "subscription",
            "category_sub2": "apps", "details": None,
            "suggested_pattern": ".*APPLE\\.COM.*", "rationale": "subscription",
        }),
        _succeeded_row("tx-102", {
            "category_main": "Shopping", "category_sub": "Groceries",
            "category_sub2": None, "details": None,
            "suggested_pattern": ".*GREGGS", "rationale": "bakery",
        }),
        _succeeded_row("tx-103", {
            "category_main": "Shopping", "category_sub": "general",
            "category_sub2": None, "details": None,
            "suggested_pattern": ".*AMAZON", "rationale": "retail",
        }),
    ]

    out = check_batch_results("batch_test_001")
    assert out["status"] == "completed"
    assert len(out["suggestions"]) == 3
    by_id = {s["transaction_id"]: s for s in out["suggestions"]}
    assert by_id[101]["category_main"] == "Leisure"
    assert by_id[102]["category_main"] == "Shopping"
    assert out["cost_usd"] > 0  # 3 × (250 input + 60 output) tokens × Haiku × 0.5

    # DB row is now completed.
    with open_db() as conn:
        row = conn.execute(
            "SELECT status, result_json, cost_usd FROM pending_batches "
            "WHERE batch_id='batch_test_001'",
        ).fetchone()
    assert row["status"] == "completed"
    assert row["cost_usd"] > 0
    persisted = json.loads(row["result_json"])
    assert len(persisted) == 3


def test_check_batch_results_caches_completed(tmp_db, fake_anthropic):
    bulk_classify_async(_sample_memos())
    api = fake_anthropic.messages.batches
    api.retrieve_response = _FakeBatchRetrieve(processing_status="ended", succeeded=1)
    api.results_response = [
        _succeeded_row("tx-101", {
            "category_main": "Leisure", "category_sub": "subscription",
            "category_sub2": "apps", "details": None,
            "suggested_pattern": ".*APPLE", "rationale": "x",
        }),
    ]
    check_batch_results("batch_test_001")
    retrieves_after_first = len(api.retrieve_calls)

    # Second call returns the cached row without hitting Anthropic again.
    out = check_batch_results("batch_test_001")
    assert out["status"] == "completed"
    assert len(api.retrieve_calls) == retrieves_after_first


def test_check_batch_results_failed_batch_persists_error(tmp_db, fake_anthropic):
    bulk_classify_async(_sample_memos())
    api = fake_anthropic.messages.batches
    api.retrieve_response = _FakeBatchRetrieve(
        processing_status="ended", succeeded=0, expired=3,
    )
    api.results_response = []

    out = check_batch_results("batch_test_001")
    assert out["status"] == "failed"
    assert "expired" in out["error_detail"]

    with open_db() as conn:
        row = conn.execute(
            "SELECT status, error_detail FROM pending_batches "
            "WHERE batch_id='batch_test_001'",
        ).fetchone()
    assert row["status"] == "failed"
    assert "expired" in row["error_detail"]


def test_check_batch_results_unknown_id_raises(tmp_db, fake_anthropic):
    with pytest.raises(ValueError, match="Unknown batch_id"):
        check_batch_results("does_not_exist")
