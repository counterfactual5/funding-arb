# Documentation (zh-TW)

Algorithm and strategy reference for the Funding Arb scanner. Generated from `web/src/content/docs/`.

| Article | Description |
| --- | --- |
| [專案概覽（README）](./overview.md) | 策略、快速啟動、API、配置 |
| [CLI 手冊（SKILL.md）](./skill-cli.md) | 掃描、交易、回測命令速查 |
| [資金費率基礎](./funding-basics.md) | 費率機制、Delta 中性與策略總覽 |
| [正向與反向 Cash & Carry](./cash-and-carry.md) | 現貨+永續對沖、借幣反向與門檻 |
| [Unified 跨所套利](./unified-carry.md) | 兩腿拆所路由與轉帳成本 |
| [Pure Futures 永續套利](./pure-futures.md) | 雙永續費率差、net / real edge |
| [跨週期資金費率套利](./cross-interval.md) | basis blend、real_edge 與程式實作 |
| [費率與邊際計算](./fees-and-edge.md) | fee_mode、VIP 等級與各類邊際欄位 |

## Repository docs

| Doc | Path |
| --- | --- |
| Project README | [../README.md](../README.md) |
| CLI playbook (SKILL) | [../SKILL.md](../SKILL.md) |

---

_Regenerate: `npx tsx scripts/tools/export_docs_md.mts`_
