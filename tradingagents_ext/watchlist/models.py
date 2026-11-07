"""The records the watchlist keeps: a company you asked for and the listing it resolved to."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Massive reports the primary exchange as an ISO 10383 MIC; SEC EDGAR as a plain name.
EXCHANGE_NAMES = {
    "XNAS": "Nasdaq",
    "XNYS": "NYSE",
    "XASE": "NYSE American",
    "ARCX": "NYSE Arca",
    "BATS": "Cboe BZX",
    "CBOE": "Cboe",
}

STATUSES = ("pending", "resolved", "ambiguous", "not_found")


@dataclass
class Candidate:
    """One listing that might be the company you meant."""

    ticker: str
    name: str
    cik: str | None = None
    exchange: str | None = None
    security_type: str | None = None
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Company:
    """A watchlist entry: what you typed, what it resolved to, and whether you confirmed it.

    Only confirmed companies are reviewed, so a wrong match never reaches an agent.
    """

    query: str
    ticker: str | None = None
    name: str | None = None
    cik: str | None = None
    exchange: str | None = None
    security_type: str | None = None
    sic_code: str | None = None
    sector: str | None = None
    status: str = "pending"
    confirmed: bool = False
    source: str | None = None
    candidates: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    resolved_at: str | None = None

    @property
    def display_name(self) -> str:
        """The listed name, with SEC's all-capitals filings names set in title case."""
        name = self.name or self.query
        if name.isupper():
            small = {"INC", "CO", "THE", "OF", "AND", "FOR", "DE", "LA"}
            name = " ".join(w if len(w) <= 3 and w.isalpha() and w not in small
                            else w.title() for w in name.split())
        return name

    @property
    def exchange_name(self) -> str | None:
        if not self.exchange:
            return None
        return EXCHANGE_NAMES.get(self.exchange, self.exchange)

    def prompt_context(self) -> str:
        """The one description of this company every agent prompt uses."""
        if not self.ticker:
            return f"Company: {self.query} (not yet resolved to a ticker)."
        listing = f"ticker {self.ticker}" + (f", {self.exchange_name}" if self.exchange_name else "")
        parts = [f"Company: {self.display_name} ({listing})."]
        if self.sector:
            sector = self.sector.capitalize()
            parts.append(f"Sector: {sector}" + (f" (SIC {self.sic_code})." if self.sic_code else "."))
        if self.cik:
            parts.append(f"SEC CIK {self.cik}.")
        return " ".join(parts)

    def apply(self, candidate: Candidate, source: str) -> None:
        """Take a candidate's listing details as this company's resolution."""
        self.ticker = candidate.ticker
        self.name = candidate.name
        self.cik = candidate.cik
        self.exchange = candidate.exchange
        self.security_type = candidate.security_type
        self.source = source

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Company:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})
