# Project rules: personal research fork

This repository is Tejas's personal fork of TauricResearch/TradingAgents, customised for his
own research. It is not a contribution to the original project.

- **Never contribute upstream.** Do not open pull requests, issues, discussions or comments on
  TauricResearch/TradingAgents, and never push to it. The `upstream` remote is fetch-only
  (its push URL is deliberately disabled); keep it that way.
- **All work stays in this fork.** Changes go on the `research` branch of
  journey85/TradingAgents. `main` only mirrors upstream so releases can be merged in.
- **Taking upstream releases is one-way:** fetch `upstream`, merge into `main`, then merge
  `main` into `research`. Nothing flows back.
- **Personal data never enters git.** Watchlist, cost ledger, reports and API keys live in
  `research_data/` and `.env`, both git-ignored. The fork is public on GitHub.
- **Extension code lives in `tradingagents_ext/`.** Keep changes to core files small and
  clearly commented so upstream merges stay easy.
- The plan, phases and budget are in the plan doc; follow its done-when criteria.
