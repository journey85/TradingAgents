# TradingAgents research extension

Personal research on a watchlist of about 100 US stocks, built on TradingAgents and kept
inside the $200 monthly Claude API credits. The plan, phases and budget are in the plan doc.

| Piece | What it does |
| --- | --- |
| `watchlist` | You type company names; each resolves once to a ticker you confirm; every module and agent prompt reads the confirmed record |
| `cost` | Ledger of tokens, cache use and dollars per LLM call, by model and agent; monthly cap that stops runs cleanly |
| `config` | Research settings: Sonnet analysts and debate, Opus decisions at high effort, 2 research and 2 risk rounds |
| `review` | One review of a confirmed company, under the budget guard |

## Setup

```bash
git clone https://github.com/<you>/TradingAgents.git
cd TradingAgents
git checkout research
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

Then add these to `.env` yourself:

| Variable | Needed for | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | reviews | From the Console organisation linked to your Ultra plan's monthly API credits |
| `SEC_EDGAR_USER_AGENT` | watchlist, fundamentals | Your name and email, e.g. `Jane Doe jane@example.com`; SEC requires a contact |
| `MASSIVE_API_KEY` | optional | Watchlist uses Massive for name search when set, SEC EDGAR otherwise; later phases use it for prices and news |
| `TRADINGAGENTS_RESEARCH_DIR` | optional | Where personal data lives; default `research_data/` in the repo (git-ignored) |

Keep auto-reload off in the Console so the $200 credits act as a hard cap.

## Watchlist

```bash
python -m tradingagents_ext.watchlist add "Exelixis" "Vertex Pharmaceuticals" --tags biotech
python -m tradingagents_ext.watchlist confirm --all-exact        # confirm every clear match
python -m tradingagents_ext.watchlist confirm "Alphabet" --ticker GOOGL   # choose for ambiguous names
python -m tradingagents_ext.watchlist list
python -m tradingagents_ext.watchlist show EXEL                  # the context agents receive
```

You can also edit `research_data/watchlist/companies.yaml` directly and run
`python -m tradingagents_ext.watchlist resolve`. Pin a ticker with `ticker: GOOGL` under a
name to skip matching. Names with several plausible listings (share classes, similar
names) wait for you to choose; unconfirmed names are never reviewed.

In Python:

```python
from tradingagents_ext.watchlist import Watchlist

wl = Watchlist()
for company in wl.confirmed():
    print(company.ticker, company.prompt_context())
```

## Review and cost

```bash
python -m tradingagents_ext.review EXEL                  # last weekday's close
python -m tradingagents_ext.review "Exelixis" --date 2026-11-06 --risk-rounds 1
python -m tradingagents_ext.cost                         # month-to-date spend, by model and agent
```

A review refuses to start when the month's remaining budget cannot cover it, and stops
before any call once spend reaches the cap.

## Ground rules

- **Personal fork, no contributions upstream.** Nothing from this fork goes to
  TauricResearch/TradingAgents: no pull requests, issues or pushes. The `upstream` remote is
  fetch-only; disable its push URL on any new clone with
  `git remote set-url --push upstream DISABLED-personal-fork-never-push-upstream`.
- **The fork is public.** Personal data stays in `research_data/`, which git ignores. Never
  commit API keys, the watchlist, the ledger or reports.
- **`main` mirrors upstream; work happens on `research`.** To take an upstream release:
  `git fetch upstream && git checkout main && git merge upstream/main && git checkout research && git merge main`
  (add the remote once with `git remote add upstream https://github.com/TauricResearch/TradingAgents.git`).
- **Core changes are kept to small, marked hooks** so merges stay easy. Today there is one:
  per-tier Anthropic effort in `tradingagents/llm_clients/factory.py`.
- **Cost figures are estimates until Phase 0 measures them.** Prices live in
  `tradingagents_ext/cost/pricing.py`; update them when the pricing page changes.
