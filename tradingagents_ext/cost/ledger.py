"""Record what every LLM call costs, and stop before the month's budget runs out.

``CostLedgerCallback`` plugs into TradingAgents (``TradingAgentsGraph(callbacks=[...])``)
and writes one row per call to a SQLite ledger: model, agent node, uncached input,
cache reads, cache writes, output and dollars. ``BudgetGuard`` checks month-to-date
spend before a review starts and before every call, so a run stops cleanly at the cap
instead of being cut off by the API mid-review.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from ..config import research_dir
from .pricing import call_cost

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    month TEXT NOT NULL,
    run_label TEXT,
    ticker TEXT,
    node TEXT,
    model TEXT,
    uncached_input INTEGER NOT NULL,
    cache_read INTEGER NOT NULL,
    cache_write_5m INTEGER NOT NULL,
    cache_write_1h INTEGER NOT NULL,
    output INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    estimated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_month ON calls(month);
CREATE INDEX IF NOT EXISTS calls_run ON calls(run_label);
"""


@dataclass
class CallRecord:
    model: str
    uncached_input: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output: int
    node: str = "unknown"
    run_label: str | None = None
    ticker: str | None = None
    ts: datetime | None = None

    @property
    def cost(self) -> tuple[float, bool]:
        return call_cost(self.model, self.uncached_input, self.cache_read, self.cache_write_5m,
                         self.cache_write_1h, self.output)


class BudgetExceeded(RuntimeError):
    """Raised before a review or a call that would take spend past the monthly cap."""


class CostLedger:
    """The SQLite ledger of every call, by month."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else research_dir() / "ledger.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def record(self, rec: CallRecord) -> float:
        usd, estimated = rec.cost
        ts = rec.ts or datetime.now(UTC)
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO calls (ts, month, run_label, ticker, node, model, uncached_input, cache_read,"
                " cache_write_5m, cache_write_1h, output, cost_usd, estimated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts.isoformat(timespec="seconds"), ts.strftime("%Y-%m"), rec.run_label, rec.ticker,
                 rec.node, rec.model, rec.uncached_input, rec.cache_read, rec.cache_write_5m,
                 rec.cache_write_1h, rec.output, usd, int(estimated)),
            )
        return usd

    def month_to_date(self, month: str | None = None) -> float:
        month = month or datetime.now(UTC).strftime("%Y-%m")
        with closing(self._connect()) as db:
            (total,) = db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE month = ?",
                                  (month,)).fetchone()
        return float(total)

    def run_cost(self, run_label: str) -> float:
        with closing(self._connect()) as db:
            (total,) = db.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE run_label = ?",
                                  (run_label,)).fetchone()
        return float(total)

    def summary(self, month: str | None = None) -> dict:
        """Spend, calls and cache use for a month, by model and by agent node."""
        month = month or datetime.now(UTC).strftime("%Y-%m")
        with closing(self._connect()) as db:
            def grouped(col: str) -> list[dict]:
                rows = db.execute(
                    f"SELECT {col}, COUNT(*), SUM(cost_usd), SUM(uncached_input), SUM(cache_read),"
                    f" SUM(cache_write_5m + cache_write_1h), SUM(output) FROM calls WHERE month = ?"
                    f" GROUP BY {col} ORDER BY SUM(cost_usd) DESC", (month,)).fetchall()
                return [dict(zip(("key", "calls", "usd", "uncached_input", "cache_read",
                                  "cache_write", "output"), r, strict=True)) for r in rows]

            by_model, by_node = grouped("model"), grouped("node")
            (runs,) = db.execute("SELECT COUNT(DISTINCT run_label) FROM calls WHERE month = ?",
                                 (month,)).fetchone()
            (estimated,) = db.execute("SELECT COUNT(*) FROM calls WHERE month = ? AND estimated = 1",
                                      (month,)).fetchone()
        total_in = sum(r["uncached_input"] + r["cache_read"] + r["cache_write"] for r in by_model)
        cache_read = sum(r["cache_read"] for r in by_model)
        return {
            "month": month,
            "usd": sum(r["usd"] for r in by_model),
            "calls": sum(r["calls"] for r in by_model),
            "runs": runs,
            "cache_hit_rate": (cache_read / total_in) if total_in else 0.0,
            "estimated_calls": estimated,
            "by_model": by_model,
            "by_node": by_node,
        }


class BudgetGuard:
    """Refuses work that would take month-to-date spend past the cap."""

    def __init__(self, ledger: CostLedger, monthly_cap_usd: float, warn_at: float = 0.85):
        self.ledger = ledger
        self.cap = float(monthly_cap_usd)
        self.warn_at = warn_at

    def status(self) -> dict:
        spent = self.ledger.month_to_date()
        return {"spent": spent, "cap": self.cap, "left": max(self.cap - spent, 0.0),
                "warning": spent >= self.cap * self.warn_at}

    def check(self, upcoming_usd: float = 0.0) -> None:
        spent = self.ledger.month_to_date()
        if spent >= self.cap or spent + upcoming_usd > self.cap:
            raise BudgetExceeded(
                f"month-to-date spend ${spent:.2f} + ${upcoming_usd:.2f} would pass the "
                f"${self.cap:.2f} cap"
            )


def parse_usage(message: Any) -> tuple[int, int, int, int, int] | None:
    """(uncached input, cache read, 5-minute writes, 1-hour writes, output) from a response.

    Prefers Anthropic's raw usage, where input_tokens counts only uncached tokens, over
    LangChain's normalised usage_metadata, where input_tokens includes cached ones.
    """
    raw = (getattr(message, "response_metadata", None) or {}).get("usage") or {}
    if raw and "input_tokens" in raw:
        creation = raw.get("cache_creation") or {}
        w5 = int(creation.get("ephemeral_5m_input_tokens") or 0)
        w1 = int(creation.get("ephemeral_1h_input_tokens") or 0)
        if not (w5 or w1):
            w5 = int(raw.get("cache_creation_input_tokens") or 0)
        return (int(raw.get("input_tokens") or 0), int(raw.get("cache_read_input_tokens") or 0),
                w5, w1, int(raw.get("output_tokens") or 0))

    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        return None
    details = usage.get("input_token_details") or {}
    read = int(details.get("cache_read") or 0)
    w5 = int(details.get("ephemeral_5m_input_tokens") or 0)
    w1 = int(details.get("ephemeral_1h_input_tokens") or 0)
    if not (w5 or w1):
        w5 = int(details.get("cache_creation") or 0)
    total = int(usage.get("input_tokens") or 0)
    uncached = total - read - w5 - w1 if total >= read + w5 + w1 else total
    return uncached, read, w5, w1, int(usage.get("output_tokens") or 0)


def _node_from(metadata: dict | None) -> str:
    """The TradingAgents node making the call (an analyst's sub-steps report the analyst)."""
    metadata = metadata or {}
    ns = metadata.get("checkpoint_ns") or metadata.get("langgraph_checkpoint_ns") or ""
    if ns:
        return ns.split("|")[0].split(":")[0] or metadata.get("langgraph_node", "unknown")
    return metadata.get("langgraph_node") or "unknown"


class CostLedgerCallback(BaseCallbackHandler):
    """LangChain callback: one ledger row per LLM call; stops the run at the budget cap."""

    raise_error = True   # let BudgetExceeded stop the run instead of being swallowed

    def __init__(self, ledger: CostLedger, run_label: str | None = None, ticker: str | None = None,
                 guard: BudgetGuard | None = None):
        super().__init__()
        self.ledger = ledger
        self.run_label = run_label
        self.ticker = ticker
        self.guard = guard
        self._starts: dict[UUID, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.usd = 0.0

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[Any]], *,
                            run_id: UUID, metadata: dict | None = None, **kwargs: Any) -> None:
        if self.guard is not None:
            self.guard.check()
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or \
            ((serialized or {}).get("kwargs") or {}).get("model") or "unknown"
        with self._lock:
            self._starts[run_id] = (str(model), _node_from(metadata))

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            model, node = self._starts.pop(run_id, ("unknown", "unknown"))
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return
        message = getattr(generation, "message", None)
        usage = parse_usage(message) if message is not None else None
        if usage is None:
            return
        meta = getattr(message, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or model
        usd = self.ledger.record(CallRecord(str(model), *usage, node=node, run_label=self.run_label,
                                            ticker=self.ticker))
        with self._lock:
            self.calls += 1
            self.usd += usd
