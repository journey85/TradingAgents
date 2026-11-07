"""Research configuration: which model does what, how deep the debates go, and the budget."""

from __future__ import annotations

import copy
import os
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG

# One place to change a model. The core only knows two tiers (quick, deep); the reader
# tier is used by the extension's own Haiku steps (data digests, daily monitor).
MODELS = {
    "reader": "claude-haiku-5-5",     # condenses news and social text
    "analyst": "claude-sonnet-5-5",   # analysts, bull/bear debate, trader, risk debate
    "decision": "claude-opus-5-5",    # Research Manager and Portfolio Manager
}

_REPO_ROOT = Path(__file__).resolve().parent.parent


def research_dir() -> Path:
    """The folder for personal research data (watchlist, ledger, reports).

    Defaults to ``research_data/`` at the repository root, which git ignores. Set
    ``TRADINGAGENTS_RESEARCH_DIR`` to keep it elsewhere.
    """
    configured = os.getenv("TRADINGAGENTS_RESEARCH_DIR", "").strip()
    path = Path(configured).expanduser() if configured else _REPO_ROOT / "research_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_config(**overrides) -> dict:
    """The TradingAgents config for a full review, with the research settings applied.

    Keyword arguments override any key, e.g. ``research_config(max_risk_discuss_rounds=1)``.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    data_dir = research_dir()
    config.update({
        "llm_provider": "anthropic",
        "quick_think_llm": MODELS["analyst"],
        "deep_think_llm": MODELS["decision"],
        # Effort per tier (read by the core's tier factory): Opus thinks hard on the
        # decision; Sonnet runs analysts and debates at its default effort.
        "quick_anthropic_effort": None,
        "deep_anthropic_effort": "high",
        "max_debate_rounds": 2,
        "max_risk_discuss_rounds": 2,
        # Analysts stop fetching after 8 tool rounds (core default 20) so a long loop
        # cannot run up input tokens.
        "max_tool_rounds": 8,
        "results_dir": str(data_dir / "reports"),
        # Extension keys, ignored by the core.
        "reader_llm": MODELS["reader"],
        "monthly_budget_usd": 200.0,
        "upkeep_cap_usd": 100.0,
        "estimated_review_usd": 0.95,
        "research_dir": str(data_dir),
    })
    config.update(overrides)
    return config
