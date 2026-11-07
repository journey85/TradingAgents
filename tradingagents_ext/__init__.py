"""Research extension for TradingAgents.

Adds what the personal research workflow needs on top of the core framework:

* ``watchlist`` - type company names; each resolves once to a confirmed ticker that
  every Python module and agent prompt reads.
* ``cost`` - a ledger of tokens, cache use and dollars per LLM call, with a monthly cap.
* ``config`` - the research configuration (Haiku readers, Sonnet analysts and debate,
  Opus decisions at high effort, 2 research and 2 risk debate rounds).
* ``review`` - runs one review of a confirmed watchlist company under the budget guard.

Personal data (watchlist, ledger, reports) lives in the research data folder, which git
ignores: a fork of a public repository is itself public.
"""

from .config import MODELS, research_config, research_dir

__all__ = ["MODELS", "research_config", "research_dir"]
