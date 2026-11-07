"""Month-to-date spend from the ledger.

    python -m tradingagents_ext.cost              # this month
    python -m tradingagents_ext.cost --month 2026-11
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from ..config import research_config
from .ledger import BudgetGuard, CostLedger

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tradingagents_ext.cost")
    parser.add_argument("--month", help="YYYY-MM, default this month")
    args = parser.parse_args(argv)

    ledger = CostLedger()
    s = ledger.summary(args.month)
    cap = research_config()["monthly_budget_usd"]
    console.print(f"[bold]{s['month']}[/]: ${s['usd']:.2f} of ${cap:.0f} across {s['runs']} runs "
                  f"and {s['calls']} calls · cache hit rate {s['cache_hit_rate']:.0%}")
    if not args.month:
        status = BudgetGuard(ledger, cap).status()
        console.print(f"Left this month: ${status['left']:.2f}" +
                      ("  [yellow](above 85% of the cap)[/]" if status["warning"] else ""))
    if s["estimated_calls"]:
        console.print(f"[yellow]{s['estimated_calls']} calls used an estimated price (model not in the price table)[/]")
    for title, rows in (("By model", s["by_model"]), ("By agent", s["by_node"])):
        table = Table(title=title)
        for col in ("", "Calls", "USD", "Input (uncached)", "Cache read", "Cache write", "Output"):
            table.add_column(col, justify="right" if col else "left")
        for r in rows:
            table.add_row(str(r["key"]), str(r["calls"]), f"{r['usd']:.3f}", f"{r['uncached_input']:,}",
                          f"{r['cache_read']:,}", f"{r['cache_write']:,}", f"{r['output']:,}")
        console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
