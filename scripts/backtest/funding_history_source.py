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
  - 已知局限：价差正负反转时 long/short 腿排序翻转，原配对 key 消失，
    回测会以 spread_disappeared 平仓（结果等价于价差崩塌退出）。
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

CACHE_DIR = ROOT / "data" / "cache" / "funding-history"
CACHE_TTL_SEC = 6 * 3600


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


def infer_interval_h(rows: list[dict[str, Any]]) -> float:
    """从相邻结算时间差推断结算周期，吸附到常见档位 (1/2/4/8h)。"""
    if len(rows) < 3:
        return 8.0
    diffs = sorted(
        (rows[i + 1]["ts"] - rows[i]["ts"]) / 3600000.0 for i in range(len(rows) - 1)
    )
    med = diffs[len(diffs) // 2]
    for cand in (1.0, 2.0, 4.0, 8.0):
        if abs(med - cand) < 0.25 * cand:
            return cand
    return max(1.0, float(round(med)))


def build_snapshots(
    histories: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """合成 run_backtest 可直接消费的快照序列（含 _ts）。

    histories: {(venue, base): [{ts, rate_pct} 升序]}
    """
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
        for (venue, base), (ts_list, rates, interval_h) in legs.items():
            idx = bisect.bisect_left(ts_list, t)
            if idx >= len(ts_list):
                continue  # 该腿历史已结束
            by_base.setdefault(base, {})[venue.lower()] = {
                "symbol": f"{base}USDT",
                "rate_pct": rates[idx],
                "interval_h": interval_h,
                "next_funding_ts": ts_list[idx],
                "mark_price": 0.0,
            }
        # 阈值放开：行情持续可见，入场阈值交给 run_backtest 处理
        forward, reverse = _scan_spreads(by_base, min_spread=0.0, min_edge=-999.0)
        dt = datetime.fromtimestamp(t / 1000, timezone.utc)
        snapshots.append({
            "timestamp": dt.isoformat(),
            "forward": forward,
            "reverse": reverse,
            "_ts": dt,
        })
    return snapshots


def fetch_history_snapshots(
    venues: list[str],
    bases: list[str],
    days: int,
    *,
    refresh: bool = False,
    workers: int = 8,
) -> list[dict[str, Any]]:
    """并行拉取所有 venue × base 历史费率并合成快照。"""
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
    return build_snapshots(histories)
