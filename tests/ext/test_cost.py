"""Cost ledger: prices, usage parsing, the LangChain callback and the budget guard."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tradingagents_ext.cost import (
    BudgetExceeded,
    BudgetGuard,
    CallRecord,
    CostLedger,
    CostLedgerCallback,
    call_cost,
    parse_usage,
    price_for,
)
from tradingagents_ext.cost.pricing import canonical_model


def test_opus_cost_with_cache_reads_and_writes():
    # 10k uncached, 20k cache read (0.05x), 5k 5-minute write (1.25x), 2k output
    usd, estimated = call_cost("claude-opus-5-5", 10_000, 20_000, 5_000, 0, 2_000)
    expected = (10_000 * 4 + 20_000 * 4 * 0.05 + 5_000 * 4 * 1.25 + 2_000 * 20) / 1e6
    assert usd == pytest.approx(expected)
    assert estimated is False


def test_haiku_switches_price_band_past_100k_prompt():
    short, _ = price_for("claude-haiku-5-5", 90_000)
    long, _ = price_for("claude-haiku-5-5", 120_000)
    assert (short.input, long.input) == (0.10, 0.50)


def test_unknown_model_is_priced_by_family_and_flagged():
    price, estimated = price_for("claude-sonnet-9-9")
    assert estimated is True and price.input == 2.00
    assert canonical_model("us.anthropic.claude-opus-5-5-20261001") == "claude-opus-5-5"


def test_parse_usage_prefers_raw_anthropic_counts():
    message = SimpleNamespace(
        response_metadata={"usage": {
            "input_tokens": 1_000, "cache_read_input_tokens": 8_000,
            "cache_creation_input_tokens": 2_000, "output_tokens": 500,
            "cache_creation": {"ephemeral_5m_input_tokens": 2_000, "ephemeral_1h_input_tokens": 0},
        }},
        usage_metadata={"input_tokens": 11_000, "output_tokens": 500},
    )
    assert parse_usage(message) == (1_000, 8_000, 2_000, 0, 500)


def test_parse_usage_falls_back_to_langchain_totals():
    message = SimpleNamespace(response_metadata={}, usage_metadata={
        "input_tokens": 11_000, "output_tokens": 500,
        "input_token_details": {"cache_read": 8_000, "cache_creation": 2_000}})
    assert parse_usage(message) == (1_000, 8_000, 2_000, 0, 500)


def test_ledger_month_to_date_and_summary(tmp_path):
    ledger = CostLedger(tmp_path / "ledger.sqlite")
    now = datetime.now(UTC)
    ledger.record(CallRecord("claude-sonnet-5-5", 10_000, 0, 0, 0, 1_000, node="Bull Researcher",
                             run_label="r1", ts=now))
    ledger.record(CallRecord("claude-opus-5-5", 5_000, 5_000, 0, 0, 2_000, node="Portfolio Manager",
                             run_label="r1", ts=now))
    s = ledger.summary()
    assert s["calls"] == 2 and s["runs"] == 1
    assert ledger.month_to_date() == pytest.approx(s["usd"])
    assert ledger.run_cost("r1") == pytest.approx(s["usd"])
    assert {r["key"] for r in s["by_node"]} == {"Bull Researcher", "Portfolio Manager"}
    assert s["cache_hit_rate"] == pytest.approx(5_000 / 20_000)


def test_guard_refuses_work_past_the_cap(tmp_path):
    ledger = CostLedger(tmp_path / "ledger.sqlite")
    ledger.record(CallRecord("claude-opus-5-5", 0, 0, 0, 0, 9_500_000))   # $190 of output
    guard = BudgetGuard(ledger, monthly_cap_usd=200)
    guard.check(upcoming_usd=5)
    with pytest.raises(BudgetExceeded):
        guard.check(upcoming_usd=11)
    assert guard.status()["warning"] is True


def _llm_result(model, usage):
    message = SimpleNamespace(response_metadata={"model_name": model, "usage": usage}, usage_metadata={})
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def test_callback_records_node_model_and_cost(tmp_path):
    ledger = CostLedger(tmp_path / "ledger.sqlite")
    cb = CostLedgerCallback(ledger, run_label="EXEL:2026-11-06", ticker="EXEL")
    run_id = uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run_id,
                           metadata={"checkpoint_ns": "Market Analyst:abc|agent:def"},
                           invocation_params={"model": "claude-sonnet-5-5"})
    cb.on_llm_end(_llm_result("claude-sonnet-5-5", {"input_tokens": 3_000, "output_tokens": 800}),
                  run_id=run_id)
    s = ledger.summary()
    assert s["by_node"][0]["key"] == "Market Analyst"
    assert s["by_model"][0]["key"] == "claude-sonnet-5-5"
    assert cb.calls == 1 and cb.usd == pytest.approx((3_000 * 2 + 800 * 10) / 1e6)


def test_callback_stops_a_run_at_the_cap(tmp_path):
    ledger = CostLedger(tmp_path / "ledger.sqlite")
    ledger.record(CallRecord("claude-opus-5-5", 0, 0, 0, 0, 10_000_000))  # $200
    cb = CostLedgerCallback(ledger, guard=BudgetGuard(ledger, 200))
    assert cb.raise_error is True
    with pytest.raises(BudgetExceeded):
        cb.on_chat_model_start({}, [[]], run_id=uuid4(), metadata={})
