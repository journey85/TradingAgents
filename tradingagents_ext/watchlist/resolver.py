"""Turn a company name into the listing it means.

Two backends, same answer shape:

* ``MassiveResolver`` - Massive's ticker search and ticker details (needs MASSIVE_API_KEY).
* ``EdgarResolver`` - SEC EDGAR's company list and submissions (free; needs only a
  User-Agent with a contact address, set through SEC_EDGAR_USER_AGENT).

``resolve_query`` scores the candidates and decides: one clear match is *resolved*;
several plausible ones (including share classes such as GOOG and GOOGL) are
*ambiguous* and wait for you to choose; no match is *not_found*.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import requests

from .models import Candidate, Company

# Trailing words that describe the legal form or the share class rather than the company.
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "llc", "lp", "holdings", "holding", "group", "sa", "nv", "ag", "se", "common", "stock",
    "ordinary", "shares", "share", "capital", "class", "a", "b", "c", "adr", "ads",
    "depositary", "the",
}
# Listings that are not the company's ordinary shares.
_NON_EQUITY_WORDS = (
    "depositary shares representing", "preferred", "warrant", "rights", "units", "notes due",
    "etf", "etn", "fund", "trust units",
)
_EQUITY_TYPES = {"CS", "ADRC"}
_SPECIAL_FIFTH_LETTERS = set("MNOPQRUWZ")
_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")

EXACT = 0.97        # at or above: the names match once legal suffixes are removed
PLAUSIBLE = 0.90    # at or above: close enough that you should choose


def normalise(name: str) -> str:
    """Lower-case a company name and drop punctuation, a leading 'the' and trailing legal words."""
    text = name.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = text.split()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def score(query: str, name: str) -> float:
    """How well a listed name matches what you typed, from 0 to 1."""
    q, n = normalise(query), normalise(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    ratio = SequenceMatcher(None, q, n).ratio()
    if n.startswith(q + " ") or q.startswith(n + " "):
        ratio = max(ratio, 0.92)
    return round(ratio, 4)


def looks_like_ticker(query: str) -> bool:
    return bool(_TICKER_PATTERN.match(query.strip()))


def is_equity(candidate: Candidate) -> bool:
    """True for a company's ordinary shares (any class); False for preferreds, warrants, funds."""
    if candidate.security_type and candidate.security_type not in _EQUITY_TYPES:
        return False
    name = candidate.name.lower()
    if any(word in name for word in _NON_EQUITY_WORDS):
        return False
    ticker = candidate.ticker.upper()
    if re.search(r"-(P|W|U|R)", ticker):
        return False
    # Nasdaq's fifth letter marks special issues: M-P preferred, Q bankruptcy, R rights,
    # U units, W warrants, Z miscellaneous. Share classes (A, B, K, L...) are kept.
    return not (len(ticker) == 5 and ticker.isalpha() and ticker[-1] in _SPECIAL_FIFTH_LETTERS)


class Resolver(Protocol):
    source: str

    def search(self, query: str) -> list[Candidate]: ...

    def details(self, candidate: Candidate) -> dict: ...


def resolve_query(company: Company, resolver: Resolver) -> Company:
    """Resolve one watchlist entry in place and return it."""
    query = company.query.strip()
    found = [c for c in resolver.search(query) if is_equity(c)]
    if looks_like_ticker(query):
        for c in found:
            c.score = 1.0 if c.ticker.upper() == query.upper() else score(query, c.name)
    else:
        for c in found:
            c.score = score(query, c.name)
    found = sorted((c for c in found if c.score >= 0.6), key=lambda c: (-c.score, c.ticker))
    company.resolved_at = datetime.now(UTC).isoformat(timespec="seconds")
    company.confirmed = False

    if not found:
        company.status, company.candidates = "not_found", []
        return company

    best = found[0]
    # Share classes of one company share its CIK; each is a plausible answer.
    same_company = [c for c in found if c.cik and c.cik == best.cik and c.score >= best.score - 0.02]
    rivals = [c for c in found[1:] if c.cik != best.cik and c.score >= PLAUSIBLE]
    company.candidates = [c.to_dict() for c in found[:6]]

    if best.score >= EXACT and len(same_company) <= 1 and not rivals:
        company.apply(best, resolver.source)
        company.status = "resolved"
        _enrich(company, best, resolver)
    else:
        company.status = "ambiguous"
    return company


def choose(company: Company, ticker: str, resolver: Resolver | None) -> Company:
    """Settle an ambiguous entry on one of its candidates (or on a ticker you name)."""
    match = next((c for c in company.candidates if c["ticker"].upper() == ticker.upper()), None)
    if match is None:
        if resolver is None:
            raise ValueError(f"{ticker} is not among the candidates for '{company.query}'")
        found = [c for c in resolver.search(ticker) if c.ticker.upper() == ticker.upper()]
        if not found:
            raise ValueError(f"no listing found for ticker {ticker}")
        candidate = found[0]
    else:
        candidate = Candidate(**match)
    company.apply(candidate, resolver.source if resolver else company.source or "manual")
    company.status = "resolved"
    if resolver is not None:
        _enrich(company, candidate, resolver)
    return company


def _enrich(company: Company, candidate: Candidate, resolver: Resolver) -> None:
    """Add sector details; a failure here leaves the resolution itself intact."""
    try:
        extra = resolver.details(candidate)
    except Exception:  # enrichment is optional, the ticker is what matters
        return
    company.sic_code = extra.get("sic_code") or company.sic_code
    company.sector = extra.get("sector") or company.sector
    company.name = extra.get("name") or company.name
    company.exchange = extra.get("exchange") or company.exchange


# --------------------------------------------------------------------------- Massive


class MassiveResolver:
    """Massive's reference data: ticker search plus ticker details (SIC code, exchange)."""

    source = "massive"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("MASSIVE_API_KEY is not set")
        self.base_url = (base_url or os.getenv("MASSIVE_BASE_URL") or "https://api.massive.com").rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        # The key travels in a header, never in the URL.
        response = requests.get(f"{self.base_url}{path}", params=params or {},
                                headers={"Authorization": f"Bearer {self.api_key}"},
                                timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def search(self, query: str) -> list[Candidate]:
        params = {"market": "stocks", "active": "true", "limit": 25}
        if looks_like_ticker(query):
            params["ticker"] = query.strip().upper()
            results = self._get("/v3/reference/tickers", params).get("results", [])
            if results:
                return [self._candidate(r) for r in results]
            params.pop("ticker")
        params["search"] = query
        return [self._candidate(r) for r in self._get("/v3/reference/tickers", params).get("results", [])]

    def details(self, candidate: Candidate) -> dict:
        r = self._get(f"/v3/reference/tickers/{candidate.ticker}").get("results", {})
        return {
            "name": r.get("name"),
            "sic_code": r.get("sic_code"),
            "sector": (r.get("sic_description") or "").lower() or None,
            "exchange": r.get("primary_exchange"),
        }

    @staticmethod
    def _candidate(r: dict) -> Candidate:
        return Candidate(ticker=r.get("ticker", ""), name=r.get("name", ""), cik=r.get("cik"),
                         exchange=r.get("primary_exchange"), security_type=r.get("type"))


# --------------------------------------------------------------------------- SEC EDGAR

_EDGAR_LIST_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
_EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_LIST_MAX_AGE_SECONDS = 7 * 24 * 3600


def _sec_user_agent() -> str:
    configured = os.getenv("SEC_EDGAR_USER_AGENT", "").strip()
    return configured or "TradingAgents-research (contact@example.com)"


class EdgarResolver:
    """SEC EDGAR's list of every SEC registrant with a ticker, cached for a week."""

    source = "sec_edgar"

    def __init__(self, cache_dir: Path, timeout: int = 30):
        self.cache_path = Path(cache_dir) / "company_tickers_exchange.json"
        self.timeout = timeout
        self._rows: list[Candidate] | None = None

    def _fetch(self, url: str) -> dict:
        response = requests.get(url, headers={"User-Agent": _sec_user_agent()}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _load(self) -> list[Candidate]:
        if self._rows is not None:
            return self._rows
        fresh = self.cache_path.exists() and time.time() - self.cache_path.stat().st_mtime < _LIST_MAX_AGE_SECONDS
        if fresh:
            payload = json.loads(self.cache_path.read_text())
        else:
            payload = self._fetch(_EDGAR_LIST_URL)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload))
        fields = payload["fields"]
        rows = [dict(zip(fields, row, strict=False)) for row in payload["data"]]
        self._rows = [
            Candidate(ticker=str(r["ticker"]), name=str(r["name"]), cik=str(r["cik"]).zfill(10),
                      exchange=r.get("exchange"))
            for r in rows if r.get("ticker")
        ]
        return self._rows

    def search(self, query: str) -> list[Candidate]:
        rows = self._load()
        if looks_like_ticker(query):
            exact = [c for c in rows if c.ticker.upper() == query.strip().upper()]
            if exact:
                return [Candidate(**c.to_dict()) for c in exact]
        q = normalise(query)
        hits = [c for c in rows if score(query, c.name) >= 0.6 or (q and q in normalise(c.name))]
        listed = [c for c in hits if (c.exchange or "").upper() != "OTC"]
        return [Candidate(**c.to_dict()) for c in (listed or hits)]

    def details(self, candidate: Candidate) -> dict:
        if not candidate.cik:
            return {}
        data = self._fetch(_EDGAR_SUBMISSIONS_URL.format(cik=str(candidate.cik).zfill(10)))
        return {
            "name": data.get("name"),
            "sic_code": data.get("sic"),
            "sector": (data.get("sicDescription") or "").lower() or None,
        }


def build_resolver(cache_dir: Path) -> Resolver:
    """Massive when its key is set, otherwise SEC EDGAR."""
    if os.getenv("MASSIVE_API_KEY", "").strip():
        return MassiveResolver()
    return EdgarResolver(cache_dir)
