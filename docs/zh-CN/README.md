# Documentation (zh-CN)

Algorithm and strategy reference for the Funding Arb scanner. Generated from `web/src/content/docs/`.

| Article | Description |
| --- | --- |
| [项目概览（README）](./overview.md) | 策略、快速启动、API、配置 |
| [CLI 手册（SKILL.md）](./skill-cli.md) | 扫描、交易、回测命令速查 |
| [资金费率基础](./funding-basics.md) | 费率机制、Delta 中性与策略总览 |
| [正向与反向 Cash & Carry](./cash-and-carry.md) | 现货+永续对冲、借币反向与阈值 |
| [Unified 跨所套利](./unified-carry.md) | 两腿拆所路由与转账成本 |
| [Pure Futures 永续套利](./pure-futures.md) | 双永续费率差、net / real edge |
| [跨周期资金费率套利](./cross-interval.md) | basis blend、real_edge 与代码实现 |
| [费率与边际计算](./fees-and-edge.md) | fee_mode、VIP 档位与各类边际字段 |

## Repository docs

| Doc | Path |
| --- | --- |
| Project README | [../README.md](../README.md) |
| CLI playbook (SKILL) | [../SKILL.md](../SKILL.md) |
| Cross-interval model (legacy path) | [cross-interval-funding-model.md](../cross-interval-funding-model.md) |

---

_Regenerate: `npx tsx scripts/tools/export_docs_md.mts`_
