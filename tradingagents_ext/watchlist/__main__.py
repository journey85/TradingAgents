"""Command line for the watchlist.

    python -m tradingagents_ext.watchlist add "Exelixis" "Vertex Pharmaceuticals" --tags biotech
    python -m tradingagents_ext.watchlist resolve
    python -m tradingagents_ext.watchlist confirm "Alphabet" --ticker GOOGL
    python -m tradingagents_ext.watchlist confirm --all-exact
    python -m tradingagents_ext.watchlist list
    python -m tradingagents_ext.watchlist show EXEL
    python -m tradingagents_ext.watchlist remove EXEL
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .models import Company
from .store import Watchlist

console = Console()

_STATUS_STYLE = {
    "resolved": "green",
    "ambiguous": "yellow",
    "not_found": "red",
    "pending": "dim",
}


def _table(companies: list[Company], title: str) -> Table:
    table = Table(title=title, show_lines=False)
    for col in ("You typed", "Ticker", "Listed name", "Exchange", "Sector", "Status", "Confirmed"):
        table.add_column(col)
    for c in companies:
        status = c.status
        if status == "ambiguous" and c.candidates:
            status += " (" + ", ".join(x["ticker"] for x in c.candidates[:4]) + ")"
        table.add_row(
            c.query, c.ticker or "-", c.display_name if c.name else "-", c.exchange_name or "-",
            (c.sector or "-")[:40], f"[{_STATUS_STYLE.get(c.status, 'white')}]{status}[/]",
            "yes" if c.confirmed else "no",
        )
    return table


def _show_candidates(company: Company) -> None:
    if not company.candidates:
        return
    table = Table(title=f"Candidates for '{company.query}'")
    for col in ("Ticker", "Listed name", "Exchange", "Type", "Match"):
        table.add_column(col)
    for c in company.candidates:
        table.add_row(c["ticker"], c["name"], c.get("exchange") or "-", c.get("security_type") or "-",
                      f"{c.get('score', 0):.2f}")
    console.print(table)
    console.print(f'Choose with: python -m tradingagents_ext.watchlist confirm "{company.query}" --ticker <TICKER>')


def _resolve(wl: Watchlist, refresh: bool) -> int:
    try:
        changed = wl.resolve(refresh=refresh)
    except Exception as exc:  # network or key problems: say so plainly
        console.print(f"[red]Could not resolve: {exc}[/]")
        return 1
    if not changed:
        console.print("Nothing new to resolve.")
        return 0
    console.print(_table(changed, "Resolved now"))
    for company in changed:
        if company.status == "ambiguous":
            _show_candidates(company)
    if any(c.status == "resolved" for c in changed):
        console.print("Confirm clear matches with: python -m tradingagents_ext.watchlist confirm --all-exact")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m tradingagents_ext.watchlist",
                                     description="Company-name watchlist")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add companies by name, then resolve them")
    p_add.add_argument("names", nargs="+")
    p_add.add_argument("--tags", default="", help="comma-separated, e.g. biotech,oncology")
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--no-resolve", action="store_true", help="add only; resolve later")

    p_res = sub.add_parser("resolve", help="resolve names that have no ticker yet")
    p_res.add_argument("--refresh", action="store_true", help="re-resolve every name")

    p_conf = sub.add_parser("confirm", help="confirm a resolved ticker so it can be reviewed")
    p_conf.add_argument("key", nargs="?", help="the name you typed, or the ticker")
    p_conf.add_argument("--ticker", help="choose this ticker for an ambiguous name")
    p_conf.add_argument("--all-exact", action="store_true", help="confirm every clear match")

    p_list = sub.add_parser("list", help="show the watchlist")
    p_list.add_argument("--confirmed", action="store_true", help="only confirmed companies")

    p_show = sub.add_parser("show", help="show one company and the context agents receive")
    p_show.add_argument("key")

    p_rm = sub.add_parser("remove", help="remove a company")
    p_rm.add_argument("key")

    args = parser.parse_args(argv)
    wl = Watchlist()

    if args.command == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        for name in args.names:
            added = wl.add(name, tags=tags, notes=args.notes)
            console.print(f"{'Added' if added else 'Already listed'}: {name}")
        return 0 if args.no_resolve else _resolve(wl, refresh=False)

    if args.command == "resolve":
        return _resolve(wl, refresh=args.refresh)

    if args.command == "confirm":
        if args.all_exact:
            done = wl.confirm_exact()
            console.print(f"Confirmed {len(done)}: " + (", ".join(c.ticker for c in done) or "none"))
            return 0
        if not args.key:
            parser.error("give a name or ticker, or use --all-exact")
        try:
            company = wl.confirm(args.key, ticker=args.ticker)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            found = wl.find(args.key)
            if found:
                _show_candidates(found)
            return 1
        console.print(f"Confirmed: {company.query} -> {company.ticker} ({company.name})")
        return 0

    if args.command == "list":
        companies = wl.confirmed() if args.confirmed else wl.companies()
        console.print(_table(companies, f"Watchlist ({len(companies)})"))
        return 0

    if args.command == "show":
        company = wl.find(args.key)
        if company is None:
            console.print(f"[red]'{args.key}' is not on the watchlist[/]")
            return 1
        console.print(_table([company], company.query))
        console.print(f"Agent context: {company.prompt_context()}")
        if company.status == "ambiguous":
            _show_candidates(company)
        return 0

    if args.command == "remove":
        removed = wl.remove(args.key)
        console.print("Removed." if removed else f"'{args.key}' is not on the watchlist")
        return 0 if removed else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
