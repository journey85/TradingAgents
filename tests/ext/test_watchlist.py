"""Watchlist: name matching, resolution decisions, confirmation and the files on disk."""

import json

import pytest
import yaml

from tradingagents_ext.watchlist import Candidate, Company, Watchlist, normalise, score
from tradingagents_ext.watchlist.resolver import resolve_query


class FakeResolver:
    source = "fake"

    def __init__(self, listings, details=None):
        self.listings = listings
        self._details = details or {}

    def search(self, query):
        q = query.lower()
        return [Candidate(**c) for c in self.listings
                if q in c["name"].lower() or q == c["ticker"].lower()]

    def details(self, candidate):
        return self._details.get(candidate.ticker, {})


LISTINGS = [
    {"ticker": "EXEL", "name": "Exelixis Inc", "cik": "0000939767", "exchange": "XNAS", "security_type": "CS"},
    {"ticker": "GOOGL", "name": "Alphabet Inc. Class A Common Stock", "cik": "0001652044", "exchange": "XNAS", "security_type": "CS"},
    {"ticker": "GOOG", "name": "Alphabet Inc. Class C Capital Stock", "cik": "0001652044", "exchange": "XNAS", "security_type": "CS"},
    {"ticker": "GOOGM", "name": "Alphabet Inc. Depositary Shares representing a 1/20th Interest", "cik": "0001652044", "exchange": "XNAS", "security_type": "CS"},
    {"ticker": "GOOX", "name": "T-Rex 2X Long Alphabet Daily Target ETF", "cik": "0001771146", "exchange": "BATS", "security_type": "ETF"},
    {"ticker": "VRTX", "name": "Vertex Pharmaceuticals Inc", "cik": "0000875320", "exchange": "XNAS", "security_type": "CS"},
    {"ticker": "VERX", "name": "Vertex Inc", "cik": "0001806837", "exchange": "XNAS", "security_type": "CS"},
]
DETAILS = {"EXEL": {"sic_code": "2836", "sector": "biological products, (no diagnostic substances)"}}


@pytest.fixture
def resolver():
    return FakeResolver(LISTINGS, DETAILS)


@pytest.fixture
def wl(tmp_path, resolver):
    return Watchlist(folder=tmp_path / "watchlist", resolver=resolver)


def test_special_issue_tickers_are_not_ordinary_shares():
    from tradingagents_ext.watchlist.resolver import is_equity
    assert not is_equity(Candidate(ticker="GOOGM", name="Alphabet Inc."))   # preferred depositary
    assert not is_equity(Candidate(ticker="ABCDW", name="Example Corp"))    # warrants
    assert is_equity(Candidate(ticker="GOOGL", name="Alphabet Inc."))       # class A
    assert is_equity(Candidate(ticker="BRK-B", name="Berkshire Hathaway Inc"))


def test_display_name_tidies_sec_capitals():
    assert Company(query="x", name="EXELIXIS, INC.").display_name == "Exelixis, Inc."
    assert Company(query="x", name="BANK OF AMERICA CORP /DE/").display_name.startswith("Bank Of America")


def test_normalise_drops_legal_and_share_class_words():
    assert normalise("Alphabet Inc. Class C Capital Stock") == "alphabet"
    assert normalise("The Coca-Cola Company") == "coca cola"
    assert normalise("Johnson & Johnson") == "johnson and johnson"
    assert normalise("American Express Company") == "american express"


def test_score_exact_after_normalising():
    assert score("Exelixis", "Exelixis Inc") == 1.0
    assert score("vertex", "Vertex Pharmaceuticals Inc") >= 0.9


def test_clear_match_resolves_and_enriches(resolver):
    company = resolve_query(Company(query="Exelixis"), resolver)
    assert company.status == "resolved"
    assert company.ticker == "EXEL"
    assert company.sic_code == "2836"
    assert company.confirmed is False          # resolution never confirms by itself


def test_share_classes_are_ambiguous_and_non_equity_is_dropped(resolver):
    company = resolve_query(Company(query="Alphabet"), resolver)
    assert company.status == "ambiguous"
    tickers = {c["ticker"] for c in company.candidates}
    assert {"GOOGL", "GOOG"} <= tickers
    assert "GOOGM" not in tickers and "GOOX" not in tickers


def test_two_companies_with_close_names_are_ambiguous(resolver):
    company = resolve_query(Company(query="Vertex"), resolver)
    assert company.status == "ambiguous"
    assert {"VRTX", "VERX"} <= {c["ticker"] for c in company.candidates}


def test_unknown_name_is_not_found(resolver):
    assert resolve_query(Company(query="Nonexistent Widgets"), resolver).status == "not_found"


def test_ticker_input_matches_directly(resolver):
    company = resolve_query(Company(query="VRTX"), resolver)
    assert company.status == "resolved" and company.ticker == "VRTX"


def test_add_keeps_comments_and_skips_duplicates(wl):
    assert wl.add("Exelixis", tags=["biotech"]) is True
    assert wl.add("exelixis inc") is False
    text = wl.companies_path.read_text()
    assert text.startswith("# Your watchlist")
    assert yaml.safe_load(text)["companies"][0] == {"name": "Exelixis", "tags": ["biotech"]}


def test_add_matches_the_files_existing_indentation(wl):
    wl.companies_path.write_text("companies:\n- name: Exelixis\n")
    wl.add("Vertex Pharmaceuticals")
    names = [e["name"] for e in yaml.safe_load(wl.companies_path.read_text())["companies"]]
    assert names == ["Exelixis", "Vertex Pharmaceuticals"]


def test_resolve_confirm_and_read_back(wl):
    wl.add("Exelixis")
    wl.add("Alphabet")
    changed = wl.resolve()
    assert {c.query: c.status for c in changed} == {"Exelixis": "resolved", "Alphabet": "ambiguous"}
    assert wl.confirmed() == []                       # nothing reviewed before you confirm

    assert [c.ticker for c in wl.confirm_exact()] == ["EXEL"]
    with pytest.raises(ValueError):
        wl.confirm("Alphabet")                         # ambiguous needs a choice
    wl.confirm("Alphabet", ticker="GOOGL")
    assert wl.tickers() == ["EXEL", "GOOGL"]
    assert wl.get("EXEL").prompt_context().startswith("Company: Exelixis Inc (ticker EXEL, Nasdaq).")

    stored = json.loads(wl.resolved_path.read_text())
    assert stored["alphabet"]["ticker"] == "GOOGL" and stored["alphabet"]["confirmed"] is True


def test_resolve_skips_settled_entries_unless_refreshed(wl):
    wl.add("Exelixis")
    wl.resolve()
    assert wl.resolve() == []
    assert [c.query for c in wl.resolve(refresh=True)] == ["Exelixis"]


def test_pinned_ticker_in_yaml_is_used(wl):
    wl.companies_path.write_text("companies:\n  - name: Alphabet\n    ticker: GOOG\n")
    [company] = wl.resolve()
    assert company.status == "resolved" and company.ticker == "GOOG"


def test_get_refuses_unconfirmed(wl):
    wl.add("Exelixis")
    wl.resolve()
    with pytest.raises(PermissionError):
        wl.get("EXEL")


def test_remove_drops_from_both_files(wl):
    wl.add("Exelixis")
    wl.add("Vertex Pharmaceuticals")
    wl.resolve()
    assert wl.remove("EXEL") is True
    assert [e["name"] for e in wl.entries()] == ["Vertex Pharmaceuticals"]
    assert "exelixis" not in json.loads(wl.resolved_path.read_text())
