"""The watchlist on disk.

``companies.yaml`` is yours: one entry per company, by name. ``resolved.json`` is the
system's: the listing each name resolved to and whether you confirmed it. Python code
and agent prompts read only confirmed companies, so every module names the same one.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import yaml

from ..config import research_dir
from .models import Company
from .resolver import Resolver, build_resolver, choose, normalise, resolve_query

_TEMPLATE = """\
# Your watchlist: one company per entry, by name. Tags and notes are optional.
# Add entries here or with:  python -m tradingagents_ext.watchlist add "Company name"
# Then resolve and confirm:  python -m tradingagents_ext.watchlist resolve
#
# Example:
# - name: Exelixis
#   tags: [biotech, oncology]
#   notes: core holding
companies:
"""


def _list_indent(text: str) -> str:
    """The indentation your file already uses for list items under ``companies:``."""
    after = text.split("companies:", 1)[-1]
    match = re.search(r"^([ \t]*)- ", after, flags=re.MULTILINE)
    return match.group(1) if match else "  "


def _format_entry(entry: dict, indent: str) -> str:
    """One companies.yaml entry, written the way a person would."""
    lines = [f"{indent}- name: {json.dumps(entry['name'])}"]
    if entry.get("ticker"):
        lines.append(f"{indent}  ticker: {json.dumps(str(entry['ticker']))}")
    if entry.get("tags"):
        lines.append(f"{indent}  tags: [{', '.join(json.dumps(t) for t in entry['tags'])}]")
    if entry.get("notes"):
        lines.append(f"{indent}  notes: {json.dumps(entry['notes'])}")
    return "\n".join(lines) + "\n"


class Watchlist:
    def __init__(self, folder: Path | None = None, resolver: Resolver | None = None):
        self.folder = Path(folder) if folder else research_dir() / "watchlist"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.companies_path = self.folder / "companies.yaml"
        self.resolved_path = self.folder / "resolved.json"
        self._resolver = resolver
        self._lock = threading.Lock()
        if not self.companies_path.exists():
            self.companies_path.write_text(_TEMPLATE)

    # ------------------------------------------------------------------ your list

    @property
    def resolver(self) -> Resolver:
        if self._resolver is None:
            self._resolver = build_resolver(self.folder.parent / "cache")
        return self._resolver

    def entries(self) -> list[dict]:
        """The companies you listed, each as {name, tags, notes, ticker?}."""
        data = yaml.safe_load(self.companies_path.read_text()) or {}
        raw = data.get("companies") or []
        entries = []
        for item in raw:
            if isinstance(item, str):
                entries.append({"name": item, "tags": [], "notes": ""})
            elif isinstance(item, dict) and item.get("name"):
                entries.append({
                    "name": str(item["name"]),
                    "tags": list(item.get("tags") or []),
                    "notes": str(item.get("notes") or ""),
                    "ticker": item.get("ticker"),
                })
        return entries

    def add(self, name: str, tags: list[str] | None = None, notes: str = "") -> bool:
        """Append a company to companies.yaml, keeping your comments. False if already listed."""
        key = normalise(name)
        if any(normalise(e["name"]) == key for e in self.entries()):
            return False
        text = self.companies_path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        entry = {"name": name, "tags": tags or [], "notes": notes}
        self.companies_path.write_text(text + _format_entry(entry, _list_indent(text)))
        return True

    def remove(self, key: str) -> bool:
        """Drop a company (by name or ticker) from both files. Rewrites companies.yaml."""
        company = self.find(key)
        query = company.query if company else key
        before = self.entries()
        entries = [e for e in before if normalise(e["name"]) != normalise(query)]
        if len(entries) == len(before):
            return False
        body = "".join(_format_entry(e, "  ") for e in entries)
        self.companies_path.write_text(_TEMPLATE + body)
        resolved = self._load_resolved()
        resolved.pop(normalise(query), None)
        self._save_resolved(resolved)
        return True

    # ------------------------------------------------------------------ resolutions

    def _load_resolved(self) -> dict[str, Company]:
        if not self.resolved_path.exists():
            return {}
        raw = json.loads(self.resolved_path.read_text())
        return {k: Company.from_dict(v) for k, v in raw.items()}

    def _save_resolved(self, resolved: dict[str, Company]) -> None:
        payload = {k: c.to_dict() for k, c in sorted(resolved.items())}
        tmp = self.resolved_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.resolved_path)

    def companies(self) -> list[Company]:
        """Every listed company with its current resolution, in your file's order."""
        resolved = self._load_resolved()
        out = []
        for entry in self.entries():
            company = resolved.get(normalise(entry["name"])) or Company(query=entry["name"])
            company.tags, company.notes = entry["tags"], entry["notes"]
            out.append(company)
        return out

    def resolve(self, refresh: bool = False) -> list[Company]:
        """Resolve every entry that has no resolution yet (all of them with refresh=True).

        A ticker pinned in companies.yaml is used as the choice directly.
        """
        with self._lock:
            resolved = self._load_resolved()
            changed = []
            for entry in self.entries():
                key = normalise(entry["name"])
                current = resolved.get(key)
                if current and current.status in ("resolved", "ambiguous") and not refresh:
                    continue
                company = Company(query=entry["name"], tags=entry["tags"], notes=entry["notes"])
                if entry.get("ticker"):
                    company.candidates = []
                    choose(company, str(entry["ticker"]), self.resolver)
                else:
                    resolve_query(company, self.resolver)
                resolved[key] = company
                changed.append(company)
            self._save_resolved(resolved)
            return changed

    def find(self, key: str) -> Company | None:
        """A company by the name you typed or by its ticker."""
        k = key.strip()
        for company in self.companies():
            if normalise(company.query) == normalise(k) or (company.ticker or "").upper() == k.upper():
                return company
        return None

    def confirm(self, key: str, ticker: str | None = None) -> Company:
        """Confirm a resolution, choosing among candidates with ``ticker`` when ambiguous."""
        with self._lock:
            resolved = self._load_resolved()
            company = self.find(key)
            if company is None:
                raise KeyError(f"'{key}' is not on the watchlist")
            stored = resolved.get(normalise(company.query), company)
            if ticker:
                choose(stored, ticker, self._resolver_or_none())
            if stored.status != "resolved" or not stored.ticker:
                raise ValueError(
                    f"'{stored.query}' is {stored.status}; choose a ticker with --ticker "
                    f"({', '.join(c['ticker'] for c in stored.candidates) or 'no candidates'})"
                )
            stored.confirmed = True
            resolved[normalise(stored.query)] = stored
            self._save_resolved(resolved)
            return stored

    def confirm_exact(self) -> list[Company]:
        """Confirm every entry that resolved to a single clear match."""
        with self._lock:
            resolved = self._load_resolved()
            done = []
            for company in resolved.values():
                if company.status == "resolved" and not company.confirmed:
                    company.confirmed = True
                    done.append(company)
            self._save_resolved(resolved)
            return done

    def _resolver_or_none(self) -> Resolver | None:
        try:
            return self.resolver
        except RuntimeError:
            return None

    # ------------------------------------------------------------------ what the rest reads

    def confirmed(self) -> list[Company]:
        """The companies the pipeline may review."""
        return [c for c in self.companies() if c.confirmed and c.ticker]

    def tickers(self) -> list[str]:
        return [c.ticker for c in self.confirmed()]

    def get(self, key: str) -> Company:
        """A confirmed company by ticker or name; raises if missing or unconfirmed."""
        company = self.find(key)
        if company is None:
            raise KeyError(f"'{key}' is not on the watchlist")
        if not company.confirmed:
            raise PermissionError(
                f"'{company.query}' is not confirmed yet; run: "
                f"python -m tradingagents_ext.watchlist confirm \"{company.query}\""
            )
        return company
