# 费率与边际计算

fee_mode、VIP 档位与各类边际字段

## 概述

<!-- id: fe-overview -->

资金费套利的毛利往往只有千分之几，手续费直接决定一笔机会是否真实可做。Scanner 展示的所有边际都已扣除开仓 taker 费；本篇解释费率从哪来、各类边际字段的区别。

## 费率模式（fee_mode）

<!-- id: fe-modes -->

| 模式 | 行为 |
| --- | --- |
| auto | 已配置 API key 的所从账户 API 读真实费率；未配置的所按 VIP 档位表估算 |
| tier | 全部用静态 VIP 档位表（scripts/core/vip_fee_tiers.py） |
| manual | 用策略配置中的手动覆盖值 |

在 Settings → 交易手续费 中配置 fee_mode 与各所 VIP 档位（venue_fee_tiers）。已用 API 读取的所会标记「已用 API」，档位选择对其无效。

## 现货费与永续费

<!-- id: fe-spot-futures -->

现货 taker（典型 0.1%）通常远高于永续 taker（约 0.02% ~ 0.06%）。这是 Pure Futures 相对 C&C 的结构性优势之一。

| 策略 | 开仓费组成 |
| --- | --- |
| Cash & Carry / Unified | spot_taker + futures_taker |
| Pure Futures | long_futures_taker + short_futures_taker |

> ⚠️ Scanner 的 net_edge 只扣开仓费。完整一轮（开 + 平）是两倍：round_trip_fee_pct = fee_pct × 2。判断持仓多久回本时要按 round-trip 算。

## VIP 档位的影响

<!-- id: fe-vip -->

VIP 等级越高 taker 越低，直接放大 net_edge / real_edge。同一笔费率差，VIP0 可能为负边际，VIP 高档则为正——费率配置错误会让 Scanner 整页机会失真。

- 档位表来源：各所官网公开费率表，维护于 vip_fee_tiers.py
- 设置入口：Settings → 交易手续费 → 各所 VIP 档位
- 有 API key 时优先用账户真实费率（包含返佣后的实际值）

## Perp DEX 默认 taker（无 API 时）

<!-- id: fe-dex-defaults -->

DEX 无账户 API 时使用 fee_providers 中的公开默认值或合约元数据（EdgeX defaultTakerFeeRate）。下表为 VIP0 / 默认档参考，实际以 Settings 或链上费率为准。

| Venue | 默认 futures taker | 备注 |
| --- | --- | --- |
| Hyperliquid | 0.045% | userFees 可更低 |
| Aster | 0.04% | Binance-fapi 兼容 |
| Lighter | 0% | 当前零费促销，以链上为准 |
| EdgeX | 0.038% | getMetaData defaultTakerFeeRate |
| dYdX v4 | 0.05% | 扫描估算；交易未接入 |

## 各类边际字段

<!-- id: fe-edges -->

| 字段 | 定义 | 适用 |
| --- | --- | --- |
| spread_pct | 毛费率差（或单所费率） | 全部 |
| fee_pct | 双腿开仓 taker 之和 | 全部 |
| net_edge_pct | spread − fee（反向再扣借币） | 全部 |
| mark_spread_pct | 两所标记价相对偏差 | Pure Futures |
| real_edge_pct | net_edge − mark_spread | Pure Futures（默认排序） |
| net_edge_all_in_pct | net_edge − 跨所转账费 | Unified 跨所路由 |
| annual_apy_pct | 按结算周期年化的净边际 | 全部 |

保守程度：net_edge < real_edge（Pure Futures）/ net_edge_all_in（Unified）。看到大 net_edge 先看 real / all-in 是否同样成立。

## 改费率后重算

<!-- id: fe-recalc -->

修改 fee_mode 或 VIP 档位后，不需要重新扫描：POST /api/scanner/recalc-fees 会直接用新费率重算缓存中所有机会的 net_edge / real_edge，并通过 WebSocket 推送前端。

- Settings 页「保存并重算净收益」按钮即调用该接口
- 重算覆盖 pure / carry / unified 三类缓存
- 费率解析入口：scripts/core/fee_providers.py 的 resolve_venue_fee / parse_fee_policy
