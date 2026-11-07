"""Company-name watchlist: type names, confirm the resolved tickers, use them everywhere.

    from tradingagents_ext.watchlist import Watchlist
    wl = Watchlist()
    for company in wl.confirmed():
        print(company.ticker, company.prompt_context())
"""

from .models import Candidate, Company
from .resolver import EdgarResolver, MassiveResolver, build_resolver, normalise, score
from .store import Watchlist

__all__ = [
    "Candidate",
    "Company",
    "EdgarResolver",
    "MassiveResolver",
    "Watchlist",
    "build_resolver",
    "normalise",
    "score",
]
