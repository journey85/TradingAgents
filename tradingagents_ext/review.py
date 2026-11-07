"""Run one review of a confirmed watchlist company, inside the monthly budget.

    python -m tradingagents_ext.review EXEL
    python -m tradingagents_ext.review "Exelixis" --date 2026-11-06

The review uses the research configuration, records every call in the cost ledger,
refuses to start when the month's remaining budget cannot cover it, and saves the
report tree under the research data folder.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from dotenv import load_dotenv
from rich.console import Console

from .config import research_config
from .cost import BudgetExceeded, BudgetGuard, CostLedger, CostLedgerCallback
from .watchlist import Watchlist

console = Console()


def last_weekday(today: date | None = None) -> str:
    """The most recent weekday before today: a date whose close has settled."""
    day = (today or date.today()) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def run_review(key: str, trade_date: str | None = None, config: dict | None = None,
               watchlist: Watchlist | None = None, ledger: CostLedger | None = None):
    """Review one confirmed company. Returns (final_state, decision, cost_usd)."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = config or research_config()
    company = (watchlist or Watchlist()).get(key)          # raises if not confirmed
    trade_date = trade_date or last_weekday()
    ledger = ledger or CostLedger()
    guard = BudgetGuard(ledger, config["monthly_budget_usd"])
    guard.check(upcoming_usd=config.get("estimated_review_usd", 1.0))

    run_label = f"{company.ticker}:{trade_date}:{date.today().isoformat()}"
    callback = CostLedgerCallback(ledger, run_label=run_label, ticker=company.ticker, guard=guard)
    graph = TradingAgentsGraph(debug=False, config=config, callbacks=[callback])
    state, decision = graph.propagate(company.ticker, trade_date)
    graph.save_reports(state, company.ticker)
    return state, decision, ledger.run_cost(run_label)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m tradingagents_ext.review")
    parser.add_argument("company", help="ticker or the name you typed in the watchlist")
    parser.add_argument("--date", help="analysis date YYYY-MM-DD (default: last weekday)")
    parser.add_argument("--risk-rounds", type=int, help="override risk debate rounds (default 2)")
    parser.add_argument("--debate-rounds", type=int, help="override research debate rounds (default 2)")
    args = parser.parse_args(argv)

    overrides = {}
    if args.risk_rounds is not None:
        overrides["max_risk_discuss_rounds"] = args.risk_rounds
    if args.debate_rounds is not None:
        overrides["max_debate_rounds"] = args.debate_rounds
    try:
        _, decision, cost = run_review(args.company, args.date, research_config(**overrides))
    except (KeyError, PermissionError, BudgetExceeded) as exc:
        console.print(f"[red]{exc}[/]")
        return 1
    console.print(decision)
    console.print(f"[bold]Cost of this review:[/] ${cost:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
