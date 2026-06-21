# Documentation (en)

Algorithm and strategy reference for the Funding Arb scanner. Generated from `web/src/content/docs/`.

| Article | Description |
| --- | --- |
| [Project Overview (README)](./overview.md) | Strategies, quick start, API, config |
| [CLI Handbook (SKILL.md)](./skill-cli.md) | Scan, trade, backtest command reference |
| [Funding Rate Basics](./funding-basics.md) | Funding mechanics, delta-neutral hedging, strategy map |
| [Forward & Reverse Cash & Carry](./cash-and-carry.md) | Spot+perp hedge, borrow reverse, thresholds |
| [Unified Cross-Venue Carry](./unified-carry.md) | Split-leg routing and transfer costs |
| [Pure Futures Spread](./pure-futures.md) | Perp-perp rate differential, net / real edge |
| [Cross-Interval Funding Arbitrage](./cross-interval.md) | Basis blend, real_edge, and implementation |
| [Fees & Edge Calculation](./fees-and-edge.md) | fee_mode, VIP tiers, and edge fields |
| [Serverless Data Pipeline](./serverless-pipeline.md) | GitHub Actions → gh-pages → jsDelivr → Vercel: zero-cost live demo architecture |

## Repository docs

| Doc | Path |
| --- | --- |
| Project README | [../../README.md](../../README.md) |
| CLI playbook (SKILL) | [../../SKILL.md](../../SKILL.md) |

---

_Regenerate: `npx tsx scripts/tools/export_docs_md.mts`_
