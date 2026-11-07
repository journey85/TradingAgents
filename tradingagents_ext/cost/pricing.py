"""Claude API prices, used to turn token counts into dollars.

Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 7 November 2026).
Update ``PRICES`` when the page changes; ``PRICES_AS_OF`` records when it was last checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PRICES_AS_OF = "2026-11-07"

# Cache multipliers on the base input price.
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0

# Haiku 5.5 charges more once a single request's prompt passes 100,000 tokens; cache
# reads and writes count towards that length.
HAIKU_55_LONG_PROMPT = 100_000


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens, and the cache-read multiplier on input."""

    input: float
    output: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.00, 50.00, 0.025),
    "claude-opus-5-5": Price(4.00, 20.00, 0.05),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.05),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.10),
    "claude-sonnet-5": Price(2.00, 10.00, 0.10),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10),
}
HAIKU_55_LONG = Price(0.50, 2.50, 0.10)

# Used only for a model missing from PRICES; the ledger marks such calls as estimated.
_FAMILY_FALLBACK = {
    "fable": "claude-fable-5-1",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}


def canonical_model(model: str) -> str:
    """Strip provider prefixes and snapshot dates: 'us.anthropic.claude-opus-5-5-20261001' -> 'claude-opus-5-5'."""
    name = model.lower().split("/")[-1]
    name = re.sub(r"^[a-z]{2}\.anthropic\.", "", name)
    name = re.sub(r"^anthropic\.", "", name)
    name = re.sub(r"-\d{8}$", "", name)
    name = re.sub(r"-v\d+(:\d+)?$", "", name)
    return name


def price_for(model: str, prompt_tokens: int = 0) -> tuple[Price, bool]:
    """The price for a model, and whether it is an estimate (model not in the table)."""
    name = canonical_model(model)
    if name == "claude-haiku-5-5" and prompt_tokens > HAIKU_55_LONG_PROMPT:
        return HAIKU_55_LONG, False
    if name in PRICES:
        return PRICES[name], False
    for family, fallback in _FAMILY_FALLBACK.items():
        if family in name:
            return PRICES[fallback], True
    return PRICES["claude-opus-5-5"], True   # unknown: price high rather than low


def call_cost(model: str, uncached_input: int, cache_read: int, cache_write_5m: int,
              cache_write_1h: int, output: int) -> tuple[float, bool]:
    """Dollar cost of one call, and whether the price was estimated."""
    prompt = uncached_input + cache_read + cache_write_5m + cache_write_1h
    price, estimated = price_for(model, prompt)
    usd = (
        uncached_input * price.input
        + cache_read * price.input * price.cache_read
        + cache_write_5m * price.input * CACHE_WRITE_5M
        + cache_write_1h * price.input * CACHE_WRITE_1H
        + output * price.output
    ) / 1_000_000
    return usd, estimated
