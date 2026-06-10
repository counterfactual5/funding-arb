#!/usr/bin/env python3
"""历史 funding API → 回测快照合成器。

不依赖 `scan --watch` 采集：直接拉交易所已结算的历史资金费
（4 所均走 funding_providers.fetch_since 公开端点），在每个真实
结算时间点合成与 scanner JSONL 同构的快照，喂给 run_backtest。

约定:
  - 时刻 t 的「可见费率」= 下一个结算时刻 ts >= t 将结算的费率。
    多数交易所当期费率在区间内实时可见/可预测，且 run_backtest 在
    跨过结算边界时应用最近快照费率——如此组合，资金费累计与真实
    已结算序列完全一致。
  - 快照网格 = 所有腿结算时间的并集（取整到分钟去重）。
  - 上市前空窗 / 数据洞（相邻结算间隔 > 8h×1.5）期间该腿视为不可交易，
    避免把首笔结算费率向前桥接产生幻影资金费累计。
  - 除纯永续配对（forward/reverse）外，还合成单所 cash-and-carry 行
    （snapshot["cc"]）：cc_forward = 现货多 + 永续空吃正费率；
    cc_reverse = 借币卖出 + 永续多吃负费率（扣借币利息）。
    历史借币利率无公开数据，按 --cc-borrow-apr 常数假设折算到结算周期。
  - 已知局限：价差正负反转时 long/short 腿排序翻转，原配对 key 消失，
    回测会以 spread_disappeared 平仓（结果等价于价差崩塌退出）；
    cc 行假设该币在对应所现货可买/可借（历史上市与可借状态无从查证）。
"""

from __future__ import annotations

import bisect
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.funding_providers import get_funding_provider  # noqa: E402
from cli.scan_pure_futures_spreads import _scan_spreads  # noqa: E402
from core.fee_providers import (  # noqa: E402
    offline_fee_cache_from_by_base,
    prefetch_futures_fee_rates,
    taker_fee_pct,
)

CACHE_DIR = ROOT / "data" / "cache" / "funding-history"
CACHE_TTL_SEC = 6 * 3600
SPOT_TAKER_FEE_PCT = 0.10  # 四所现货 taker 普遍 0.1%
DEFAULT_BORROW_APR_PCT = 15.0  # cc_reverse 借币年化假设（小币种实际可能更高）
HOURS_PER_YEAR = 365.0 * 24.0


def fetch_leg_history(
    venue: str,
    base: str,
    days: int,
    *,
    refresh: bool = False,
    cache_dir: Path = CACHE_DIR,
) -> list[dict[str, Any]]:
    """拉取某 venue × base 近 N 天已结算资金费，磁盘缓存 6h。

    返回 [{"ts": ms, "rate_pct": float}, ...] 升序；失败/无数据返回 []。
    """
    sym = f"{base.upper()}USDT"
    start_ms = int((time.time() - days * 86400) * 1000)
    cache_path = cache_dir / f"{venue.lower()}_{sym}.json"

    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fresh = time.time() - float(cached.get("fetched_at", 0)) < CACHE_TTL_SEC
            covers = int(cached.get("start_ms", 1 << 62)) <= start_ms
            if fresh and covers:
                return [r for r in cached.get("rows", []) if r["ts"] >= start_ms]
        except Exception:
            pass

    # 100 行/页的所（bitget/okx/bybit）按 2h 周期最坏情况估算页数
    max_pages = max(10, int(days * 24 / 2 / 100) + 3)
    try:
        rows = get_funding_provider(venue).fetch_since(sym, start_ms, max_pages=max_pages)
    except Exception as e:
        print(f"[history] {venue} {sym} 拉取失败: {e}", file=sys.stderr)
        return []
    rows.sort(key=lambda r: r["ts"])

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"fetched_at": time.time(), "start_ms": start_ms, "rows": rows},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return rows


# 主流所最大结算周期 8h；超出（含宽限）视为上市前空窗或数据洞
MAX_SETTLE_INTERVAL_H = 8.0
GAP_GRACE = 1.5


def _snap_interval(gap_h: float) -> float:
    """把结算间隔吸附到常见档位 (1/2/4/8h)，吸收毫秒级抖动。"""
    for cand in (1.0, 2.0, 4.0, 8.0):
        if abs(gap_h - cand) < 0.25 * cand:
            return cand
    return max(1.0, float(round(gap_h)))


def infer_interval_h(rows: list[dict[str, Any]]) -> float:
    """从相邻结算时间差推断结算周期（全局中位数，仅作首点 fallback）。

    注意：币种可能中途切换周期（如 ID 8h→4h→1h），全局中位数会失真，
    build_snapshots 内按相邻结算的局部间隔逐点推断，只有序列首点无前驱
    时才回退到这里。
    """
    if len(rows) < 3:
        return 8.0
    diffs = sorted(
        (rows[i + 1]["ts"] - rows[i]["ts"]) / 3600000.0 for i in range(len(rows) - 1)
    )
    return _snap_interval(diffs[len(diffs) // 2])


def fetch_cc_capability(
    venues: list[str],
    bases: list[str],
    *,
    workers: int = 8,
) -> dict[tuple[str, str], dict[str, Any]]:
    """实时探测各 venue × base 的现货可买 / 可借状态（历史无从查证，用当前代理）。

    返回 {(venue, base): {"has_spot", "borrowable", "borrow_apr_pct"}}。
    探测失败按保守处理（不可买/不可借）。
    """
    from backtest.borrow_providers import get_borrow_provider
    from backtest.unified_funding_pool import _get_venue
    from market.parallel_fetch import run_io_parallel

    out: dict[tuple[str, str], dict[str, Any]] = {
        (v.lower(), b.upper()): {
            "has_spot": False, "borrowable": False, "borrow_apr_pct": 0.0,
        }
        for v in venues for b in bases
    }

    def _spot_one(pair: tuple[str, str]) -> tuple[tuple[str, str], bool]:
        venue, base = pair
        try:
            px = _get_venue(venue).get_ticker(f"{base}USDT")
            return pair, float(px or 0) > 0
        except Exception:
            return pair, False

    spot_map = run_io_parallel(
        list(out.keys()), _spot_one,
        max_workers=min(workers, len(out)), swallow_errors=True,
    )
    for pair, has_spot in spot_map.items():
        out[pair]["has_spot"] = has_spot

    def _borrow_one(venue: str) -> tuple[str, dict[str, Any]]:
        try:
            return venue, get_borrow_provider(venue).fetch_borrow_info(
                [b.upper() for b in bases]
            )
        except Exception:
            return venue, {}

    borrow_map = run_io_parallel(
        [v.lower() for v in venues], _borrow_one,
        max_workers=len(venues), swallow_errors=True,
    )
    for venue in (v.lower() for v in venues):
        info = borrow_map.get(venue) or {}
        if not any(i.get("borrowable") for i in info.values()):
            # 全 False 可能是 API 凭证缺失（bitget/bybit/binance 借币查询需鉴权）
            print(
                f"[history] {venue}: 0 borrowable（若该所需要 API key，"
                f"探测可能失败而非真不可借）",
                file=sys.stderr,
            )
    for venue, info_by_coin in borrow_map.items():
        for base, info in (info_by_coin or {}).items():
            key = (venue, base.upper())
            if key not in out:
                continue
            out[key]["borrowable"] = bool(info.get("borrowable"))
            apr = float(info.get("annual_rate_pct", 0) or 0)
            if apr <= 0 and float(info.get("daily_rate_pct", 0) or 0) > 0:
                apr = float(info["daily_rate_pct"]) * 365.0
            out[key]["borrow_apr_pct"] = apr
    return out


def _cc_rows(
    by_base: dict[str, dict[str, dict[str, Any]]],
    borrow_apr_pct: float,
    cc_capability: dict[tuple[str, str], dict[str, Any]] | None = None,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """从单所费率合成 cash-and-carry 行（与 pure 行同构，供回测引擎消费）。

    cc_forward: 现货多(费率 0) + 永续空(收正费率)，short 腿挂费率。
    cc_reverse: 永续多(负费率→收钱) + 借币卖出，long 腿挂费率，
                借币利息按 APR 折算到结算周期，记入 borrow_per_settle_pct。

    cc_capability 非空时：cc_forward 要求该所现货可买，cc_reverse 要求可借，
    且可借时用真实借币 APR 替代常数假设。
    """
    rows: list[dict[str, Any]] = []
    for base, venue_map in by_base.items():
        for venue, info in venue_map.items():
            rate = float(info["rate_pct"])
            if rate == 0:
                continue
            cap = (
                cc_capability.get((venue, base))
                if cc_capability is not None
                else None
            )
            if cc_capability is not None and cap is None:
                continue
            if cap is not None:
                if rate > 0 and not cap["has_spot"]:
                    continue  # 无现货（如 pre-market 合约）→ 正向不可执行
                if rate < 0 and not cap["borrowable"]:
                    continue  # 不可借 → 反向不可执行
            effective_apr = borrow_apr_pct
            if cap is not None and cap["borrow_apr_pct"] > 0:
                effective_apr = cap["borrow_apr_pct"]
            interval_h = float(info["interval_h"])
            sym = str(info.get("symbol") or f"{base}USDT")
            perp_fee = taker_fee_pct(venue, sym, fee_cache=fee_cache)
            fee = SPOT_TAKER_FEE_PCT + perp_fee
            common = {
                "base": base,
                "fee_pct": round(fee, 4),
                "long_interval_h": interval_h,
                "short_interval_h": interval_h,
                "settle_mismatch": False,
                "long_settle_ms": info.get("next_funding_ts", 0),
                "short_settle_ms": info.get("next_funding_ts", 0),
            }
            if rate > 0:
                rows.append({
                    **common,
                    "direction": "cc_forward",
                    "long_venue": f"{venue}:spot",
                    "short_venue": venue,
                    "long_rate_pct": 0.0,
                    "short_rate_pct": rate,
                    "spread_pct": round(rate, 6),
                    "net_edge_pct": round(rate - fee, 6),
                    "borrow_per_settle_pct": 0.0,
                })
            else:
                borrow_per_settle = effective_apr / HOURS_PER_YEAR * interval_h
                rows.append({
                    **common,
                    "direction": "cc_reverse",
                    "long_venue": venue,
                    "short_venue": f"{venue}:margin",
                    "long_rate_pct": rate,
                    "short_rate_pct": 0.0,
                    "spread_pct": round(abs(rate), 6),
                    "net_edge_pct": round(abs(rate) - borrow_per_settle - fee, 6),
                    "borrow_per_settle_pct": round(borrow_per_settle, 6),
                })
    rows.sort(key=lambda x: -x["net_edge_pct"])
    return rows


def build_snapshots(
    histories: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    include_cc: bool = True,
    borrow_apr_pct: float = DEFAULT_BORROW_APR_PCT,
    cc_capability: dict[tuple[str, str], dict[str, Any]] | None = None,
    fee_cache: dict[tuple[str, str], dict[str, float]] | None = None,
    prefetch_live_fees: bool = False,
) -> list[dict[str, Any]]:
    """合成 run_backtest 可直接消费的快照序列（含 _ts）。

    histories: {(venue, base): [{ts, rate_pct} 升序]}
    """
    if fee_cache is None:
        pairs = [(v.lower(), f"{b.upper()}USDT") for v, b in histories.keys()]
        if prefetch_live_fees and pairs:
            fee_cache = prefetch_futures_fee_rates(pairs)
        else:
            by_base_seed: dict[str, dict[str, dict[str, Any]]] = {}
            for venue, base in histories.keys():
                by_base_seed.setdefault(base.upper(), {})[venue.lower()] = {
                    "symbol": f"{base.upper()}USDT",
                }
            fee_cache = offline_fee_cache_from_by_base(by_base_seed)

    legs: dict[tuple[str, str], tuple[list[int], list[float], float]] = {}
    grid: set[int] = set()
    for (venue, base), rows in histories.items():
        if len(rows) < 2:
            continue
        ts_list = [int(r["ts"]) for r in rows]
        rates = [float(r["rate_pct"]) for r in rows]
        legs[(venue, base)] = (ts_list, rates, infer_interval_h(rows))
        # 取整到分钟，吸收毫秒级抖动
        grid.update(t // 60000 * 60000 for t in ts_list)

    snapshots: list[dict[str, Any]] = []
    for t in sorted(grid):
        by_base: dict[str, dict[str, dict[str, Any]]] = {}
        for (venue, base), (ts_list, rates, fallback_interval) in legs.items():
            idx = bisect.bisect_left(ts_list, t)
            if idx >= len(ts_list):
                continue  # 该腿历史已结束
            if idx > 0:
                gap_h = (ts_list[idx] - ts_list[idx - 1]) / 3600000.0
                if gap_h > MAX_SETTLE_INTERVAL_H * GAP_GRACE:
                    continue  # 相邻结算间隔异常大 → 数据洞，剔除避免幻影累计
                # 周期可能中途切换（8h→4h→1h），按局部相邻间隔逐点推断
                interval_h = _snap_interval(gap_h)
            else:
                # t 在首次结算之前：仅当处于首个结算周期内才视为已上市
                if (ts_list[0] - t) / 3600000.0 > fallback_interval * GAP_GRACE:
                    continue
                interval_h = fallback_interval
            by_base.setdefault(base, {})[venue.lower()] = {
                "symbol": f"{base}USDT",
                "rate_pct": rates[idx],
                "interval_h": interval_h,
                "next_funding_ts": ts_list[idx],
                "mark_price": 0.0,
            }
        # 阈值放开：行情持续可见，入场阈值交给 run_backtest 处理
        forward, reverse = _scan_spreads(
            by_base, min_spread=0.0, min_edge=-999.0, fee_cache=fee_cache
        )
        dt = datetime.fromtimestamp(t / 1000, timezone.utc)
        snap: dict[str, Any] = {
            "timestamp": dt.isoformat(),
            "forward": forward,
            "reverse": reverse,
            "_ts": dt,
        }
        if include_cc:
            snap["cc"] = _cc_rows(by_base, borrow_apr_pct, cc_capability, fee_cache)
        snapshots.append(snap)
    return snapshots


def fetch_history_snapshots(
    venues: list[str],
    bases: list[str],
    days: int,
    *,
    refresh: bool = False,
    workers: int = 8,
    borrow_apr_pct: float = DEFAULT_BORROW_APR_PCT,
    check_cc_capability: bool = False,
    prefetch_live_fees: bool = False,
) -> list[dict[str, Any]]:
    """并行拉取所有 venue × base 历史费率并合成快照。

    check_cc_capability=True 时实时探测现货/借币能力过滤 cc 行
    （用当前状态代理历史，新上市币的历史可借性无从查证）。
    """
    from market.parallel_fetch import run_io_parallel

    pairs = [(v.lower(), b.upper()) for v in venues for b in bases]

    def _one(pair: tuple[str, str]) -> tuple[tuple[str, str], list[dict[str, Any]]]:
        venue, base = pair
        rows = fetch_leg_history(venue, base, days, refresh=refresh)
        print(f"[history] {venue} {base}: {len(rows)} settlements", file=sys.stderr)
        return pair, rows

    histories = run_io_parallel(
        pairs, _one, max_workers=min(workers, len(pairs)), swallow_errors=True
    )
    cc_capability = None
    if check_cc_capability:
        print("[history] probing spot/borrow capability...", file=sys.stderr)
        cc_capability = fetch_cc_capability(venues, bases, workers=workers)
        n_spot = sum(1 for c in cc_capability.values() if c["has_spot"])
        n_borrow = sum(1 for c in cc_capability.values() if c["borrowable"])
        print(
            f"[history] capability: {n_spot}/{len(cc_capability)} legs have spot, "
            f"{n_borrow} borrowable",
            file=sys.stderr,
        )
    return build_snapshots(
        histories,
        borrow_apr_pct=borrow_apr_pct,
        cc_capability=cc_capability,
        prefetch_live_fees=prefetch_live_fees,
    )
