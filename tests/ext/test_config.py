"""Research configuration and the per-tier Anthropic effort hook in the core factory."""

from tradingagents.llm_clients.factory import create_tier_client
from tradingagents_ext import MODELS, research_config


def test_research_config_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    config = research_config()
    assert config["quick_think_llm"] == MODELS["analyst"] == "claude-sonnet-5-5"
    assert config["deep_think_llm"] == MODELS["decision"] == "claude-opus-5-5"
    assert (config["max_debate_rounds"], config["max_risk_discuss_rounds"]) == (2, 2)
    assert config["research_dir"] == str(tmp_path)
    assert research_config(max_risk_discuss_rounds=1)["max_risk_discuss_rounds"] == 1


def test_only_the_deep_tier_thinks_hard(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_DIR", str(tmp_path))
    config = research_config(anthropic_effort="low")       # a global setting the tiers override
    assert create_tier_client(config, "deep").kwargs.get("effort") == "high"
    assert "effort" not in create_tier_client(config, "quick").kwargs


def test_global_effort_still_applies_without_tier_keys():
    config = {"llm_provider": "anthropic", "quick_think_llm": "claude-sonnet-5-5",
              "deep_think_llm": "claude-opus-5-5", "anthropic_effort": "medium"}
    assert create_tier_client(config, "quick").kwargs.get("effort") == "medium"
    assert create_tier_client(config, "deep").kwargs.get("effort") == "medium"
