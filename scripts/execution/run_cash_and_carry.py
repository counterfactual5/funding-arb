#!/usr/bin/env python3
"""Cash and carry & cross-asset arbitrage execution framework.

This engine unifies forward and reverse delta-neutral trades, performing global
asset selection based on funding rates and executing them defensively.

Live Execution relies on `delta_neutral_executor.py` for atomic transfers and rollback.
It periodically syncs real NAV using `fetch_live_state`.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from accounting.futures.delta_neutral_portfolio import (  # noqa: E402
    apply_borrow_fees,
    apply_funding_fees,
    apply_simulated_futures_trades,
    calculate_futures_nav,
    check_liquidations,
    default_futures_state,
    margin_health,
    normalize_executed_for_ledger,
)
from backtest.funding_cache import (  # noqa: E402
    fetch_all_funding,
    fetch_current_funding,
    fetch_funding_interval_map,
    fetch_funding_since,
)
from backtest.funding_providers import FundingProvider, get_funding_provider  # noqa: E402
from core.config import resolve_config_path, runs_base, strategy_dir  # noqa: E402
from execution.delta_neutral_executor import execute_delta_neutral_trades  # noqa: E402
from market.price_oracle import fetch_price_with_fallback  # noqa: E402
from market.parallel_fetch import fetch_assets_market_parallel  # noqa: E402
from market.funding_batch import (  # noqa: E402
    fetch_funding_history_parallel,
    fetch_funding_snaps_for_assets,
)
from strategies.futures.cross_asset_arbitrage import decide_cross_asset_arbitrage  # noqa: E402
from venues import get_venue, venue_quote  # noqa: E402
from venues.base import make_pair  # noqa: E402
from core.notify import send_notification  # noqa: E402

TZ = timezone(timedelta(hours=8))
HOURS_PER_YEAR = 365.0 * 24.0
FUNDINGS_PER_YEAR = 365.0 * 3.0  # 8h 结算近似，用于 APR 展示

DEFAULT_CONFIG: dict[str, Any] = {
    "strategyId": "cash-and-carry-btc",
    "strategy": "cash_and_carry",
    "assets": ["BTC"],
    "cash": "USDT",
    "dry_run": True,
    "initialCapitalUsd": 10000.0,
    "borrowAnnualRatePct": 8.0,
    "takerFeeRate": 0.0005,
    "runIntervalMinutes": 30,
    "venue": {"type": "binance", "quote": "USDT", "market": "spot"},
    "cashAndCarry": {
        "tradeUsd": 1000.0,
        "entryFundingRatePct": 0.05,
        "exitFundingRatePct": 0.01,
        "reverseEntryFundingRatePct": -0.05,
        "reverseExitFundingRatePct": -0.01,
        "minNetEdgePct": 0.02,
        "minReverseSpreadPct": 0.02,
        "maxMinutesToSettlement": 0,
    },
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


# ── config & paths ──────────────────────────────────────────────────────────

def normalize_arb_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """统一成引擎吃的 ``crossAssetArbitrage`` 配置；单资产 cashAndCarry 自动映射为单槽。"""
    out = dict(cfg)
    if cfg.get("crossAssetArbitrage"):
        return out
    cc = cfg.get("cashAndCarry") or {}
    out["crossAssetArbitrage"] = {
        "maxConcurrentPairs": 1,
        "tradeUsdPerSlot": float(cc.get("tradeUsd", 1000.0)),
        "entryFundingRatePct": float(cc.get("entryFundingRatePct", 0.05)),
        "exitFundingRatePct": float(cc.get("exitFundingRatePct", 0.01)),
        "reverseEntryFundingRatePct": float(cc.get("reverseEntryFundingRatePct", -0.05)),
        "reverseExitFundingRatePct": float(cc.get("reverseExitFundingRatePct", -0.01)),
        "minReverseSpreadPct": float(cc.get("minReverseSpreadPct", 0.02)),
        "minNetEdgePct": float(cc.get("minNetEdgePct", 0.02)),
        "preemptionFrictionBufferPct": 1e9,
        "maxMinutesToSettlement": float(cc.get("maxMinutesToSettlement", 0)),
        "forwardRequiredCashMult": float(cc.get("forwardRequiredCashMult", 2.1)),
        "reverseRequiredCashMult": float(cc.get("reverseRequiredCashMult", 1.5)),
    }
    return out


def disable_reverse(cfg: dict[str, Any]) -> None:
    for key in ("crossAssetArbitrage", "cashAndCarry"):
        block = cfg.get(key)
        if isinstance(block, dict):
            block["reverseEntryFundingRatePct"] = -999.0


def apply_live_safety(cfg: dict[str, Any]) -> dict[str, Any]:
    """实盘安全闸：Reverse C&C 需要 margin 借还币，必须显式 enableReverseArbitrage 才放行。

    即便显式开启，run_cycle 还会再校验 venue 是否实现借还币能力
    （supports_reverse_arbitrage），不支持的 venue 实盘强制关闭。
    """
    if cfg.get("dry_run", True):
        return cfg
    if cfg.get("enableReverseArbitrage", False):
        return cfg
    disable_reverse(cfg)
    return cfg


def load_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    cc = {**cfg["cashAndCarry"], **(user.get("cashAndCarry") or {})}
    cfg.update(user)
    cfg["cashAndCarry"] = cc
    if os.environ.get("DCA_DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
        cfg["dry_run"] = True
    if os.environ.get("DCA_LIVE", "").strip().lower() in ("1", "true", "yes"):
        cfg["dry_run"] = False
        
    cfg = normalize_arb_cfg(cfg)
    cfg = apply_live_safety(cfg)

    # Validation
    arb = cfg.get("crossAssetArbitrage", {})
    max_min = arb.get("maxMinutesToSettlement", 0)
    run_int = cfg.get("runIntervalMinutes", 30)
    if max_min > 0 and max_min < run_int * 1.5:
        arb["maxMinutesToSettlement"] = int(run_int * 1.5)
        
    return cfg


def resolve_paths(cfg: dict[str, Any]) -> dict[str, Path]:
    sid = str(cfg.get("strategyId", "cash-and-carry"))
    base = strategy_dir(sid)
    out = base / "output"
    state = Path(str(cfg.get("stateFile", base / "state.json")))
    if not state.is_absolute():
        state = runs_base().parent / state
    return {
        "base": base, "state": state, "lock": base / ".lock",
        "journal": out / "journal.jsonl", "summary": out / "summary.json",
        "equity": out / "equity-curve.jsonl",
    }


# ── state io（独立、原子写） ──────────────────────────────────────────────────

def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        backup = state_file.with_suffix(f".corrupt-{int(time.time())}.json")
        try:
            state_file.replace(backup)
        except OSError:
            backup = Path("(备份失败)")
        raise RuntimeError(
            f"state 文件损坏: {state_file}\n已备份到: {backup}\n请人工检查后再运行。原始错误: {e}"
        ) from e


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(state_file)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── market ──────────────────────────────────────────────────────────────────

def fetch_spot(cfg: dict[str, Any], venue: Any, quote: str, sym: str) -> dict[str, Any]:
    mkt = venue.fetch_asset_market(sym, quote, cfg)
    if mkt.get("rules_error"):
        return {"price": 0.0, "error": f"无法获取 {sym} 交易规则（{venue.venue_id}）"}
    px = float(mkt.get("price", 0) or 0)
    if px <= 0:
        fb_px, fb_meta = fetch_price_with_fallback(sym, quote, primary=venue.venue_id)
        if fb_px > 0:
            px = fb_px
            mkt["price_source"] = fb_meta.get("source", "fallback")
            
    # Inject futures min trade rules since this strategy trades futures
    if hasattr(venue, "fetch_futures_symbol_rules"):
        f_rules = venue.fetch_futures_symbol_rules(make_pair(sym, quote))
        if f_rules:
            mkt["min_trade_usdt"] = f_rules.get("min_trade_usdt", mkt.get("min_trade_usdt", 0.0))
            mkt["min_trade_base"] = f_rules.get("min_trade_base", mkt.get("min_trade_base", 0.0))

    mkt["price"] = px
    return mkt


def asset_annual_borrow(cfg: dict[str, Any], sym: str, live_rates: dict[str, float] | None = None) -> float:
    if live_rates and live_rates.get(sym, 0) > 0:
        return live_rates[sym] * 100.0  # Convert live decimal (0.12) to pct (12.0)
    cfg_borrow = cfg.get("borrowAnnualRatePct", 12.0)
    if isinstance(cfg_borrow, dict):
        return float(cfg_borrow.get(sym, cfg_borrow.get("default", 12.0)))
    return float(cfg_borrow)


# ── universe screening ──────────────────────────────────────────────────────

def build_funding_snapshots(
    cfg: dict[str, Any], quote: str, held_syms: list[str],
    funding_provider: FundingProvider | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """决定本轮关注哪些资产 + 各自资金费快照。

    - ``autoUniverse=true``：一次拉全市场永续，按 |funding| 排序取 ``universeTopN``（这才是
      “全市场找资金费率最高”）。已持仓资产恒并入，保证能继续管理/平仓。
      当 ``funding_provider`` 传入时，使用对应交易所的费率数据（而非 Binance）。
    - 否则：用配置里的 ``assets`` 列表（仍并入已持仓）。
    返回 (asset_list, {asset: snapshot})。
    """
    quote_suf = quote.upper()
    snaps: dict[str, dict[str, Any]] = {}
    fp = funding_provider  # may be None, fallback to Binance

    if bool(cfg.get("autoUniverse")):
        if fp is not None:
            rows = fp.fetch_all(quote_suf)
            interval_map = fp.fetch_interval_map(quote_suf)
        else:
            rows = fetch_all_funding()
            interval_map = fetch_funding_interval_map()
        blacklist = {x.upper() for x in (cfg.get("universeBlacklist") or [])}
        top_n = int(cfg.get("universeTopN", 10))
        min_abs = float(cfg.get("universeMinFundingPct", 0.0))
        row_by_asset: dict[str, dict[str, Any]] = {}
        cand: list[tuple[str, float]] = []
        for r in rows:
            sym = r["symbol"].upper()
            if not sym.endswith(quote_suf):
                continue
            asset = sym[: -len(quote_suf)]
            if not asset or asset in blacklist:
                continue
            row_by_asset[asset] = r
            if abs(r["rate_pct"]) >= min_abs:
                cand.append((asset, abs(r["rate_pct"])))
        cand.sort(key=lambda x: x[1], reverse=True)
        chosen = [a for a, _ in cand[:top_n]]
        asset_list = list(dict.fromkeys(list(held_syms) + chosen))
        # For venues where fetch_all returns next_funding_ts=0 (e.g. Bitget),
        # backfill via fetch_current for the chosen assets only.
        needs_backfill = fp is not None and any(
            row_by_asset.get(a, {}).get("next_funding_ts", 0) == 0 for a in asset_list if a in row_by_asset
        )
        if needs_backfill:
            workers = int(cfg.get("marketFetchWorkers", 8))
            backfill_assets = [
                a for a in asset_list
                if a in row_by_asset and row_by_asset[a].get("next_funding_ts", 0) == 0
            ]
            if backfill_assets and fp is not None:
                for asset, detail in fetch_funding_snaps_for_assets(
                    fp, backfill_assets, quote, workers=workers
                ).items():
                    if asset in row_by_asset:
                        row_by_asset[asset]["next_funding_ts"] = detail.get("next_funding_ts", 0)
                        if detail.get("interval_ms", 0) > 0:
                            row_by_asset[asset]["_interval_ms"] = detail["interval_ms"]
        for asset in asset_list:
            r = row_by_asset.get(asset)
            if not r:
                continue
            sym_key = asset + quote_suf
            default_interval_h = 8.0
            if isinstance(interval_map, dict):
                default_interval_h = interval_map.get(sym_key, 8.0)
            interval_ms = int(r.get("_interval_ms") or 0) or int(
                float(default_interval_h) * 3600 * 1000
            )
            next_ts = int(r["next_funding_ts"] or 0)
            snaps[asset] = {
                "rate_pct": r["rate_pct"],
                "next_funding_ts": next_ts,
                "last_settle_ts": next_ts - interval_ms if next_ts else 0,
                "interval_ms": interval_ms,
                "mark_price": r.get("mark_price", 0.0),
            }
        # 已持仓但不在 fetch_all 列表（下市/特殊合约）→ 单独补快照
        missing_held = [a for a in held_syms if a not in snaps]
        if missing_held and fp is not None:
            snaps.update(
                fetch_funding_snaps_for_assets(
                    fp, missing_held, quote, workers=int(cfg.get("marketFetchWorkers", 8))
                )
            )
        return asset_list, snaps

    asset_list = list(dict.fromkeys(list(cfg.get("assets", ["BTC"])) + list(held_syms)))
    workers = int(cfg.get("marketFetchWorkers", 8))
    snaps.update(
        fetch_funding_snaps_for_assets(
            fp,
            asset_list,
            quote,
            workers=workers,
            fetch_current_fn=(
                (lambda a: fetch_current_funding(make_pair(a, quote)))
                if fp is None
                else None
            ),
        )
    )
    return asset_list, snaps


# ── one cycle ───────────────────────────────────────────────────────────────

def run_cycle(cfg: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    dry_run = bool(cfg.get("dry_run", True))

    cash = str(cfg.get("cash", "USDT"))
    venue = get_venue(cfg)
    quote = venue_quote(cfg)
    venue_type = str((cfg.get("venue") or {}).get("type", "binance")).strip().lower()
    fp = get_funding_provider(venue_type)

    # Reverse C&C 能力闸：实盘且显式开启时，venue 必须实现 margin 借还币，否则强制关闭。
    reverse_disabled_reason = None
    if not dry_run and cfg.get("enableReverseArbitrage", False):
        supports = getattr(venue, "supports_reverse_arbitrage", None)
        if not (callable(supports) and supports()):
            disable_reverse(cfg)
            reverse_disabled_reason = (
                f"venue {venue.venue_id} 未开通 cross margin 或未实现借还币"
                "（supports_reverse_arbitrage=False），Reverse C&C 已强制关闭。"
            )
            send_notification("Reverse C&C Disabled", reverse_disabled_reason, cfg)

    # 1) state 先行：要先知道当前持仓，screener 才能把已持仓资产并入候选。
    state = load_state(paths["state"])
    if state.get("killed", False):
        return {"status": "skipped", "reason": "Strategy has been KILLED by Drawdown Cutoff. Clear state to restart."}

    futures_state = state.get("futures_state") or default_futures_state()
    positions = futures_state.get("positions", {})
    held_syms = [s for s, p in positions.items() if p.get("amount", 0) > 0]
    settle_ts: dict[str, int] = dict(state.get("funding_settle_ts") or {})
    liq_cooldown: dict[str, int] = dict(state.get("liq_cooldown") or {})
    last_run_ms = int(state.get("last_run_ms", 0) or 0)
    now = now_ms()
    elapsed_h = max(0.0, (now - last_run_ms) / 3_600_000.0) if last_run_ms else 0.0

    # 2) 全市场筛选 / 资金费快照
    try:
        asset_list, funding_snaps = build_funding_snapshots(cfg, quote, held_syms, funding_provider=fp)
    except Exception as e:
        return {"status": "skipped", "reason": f"资金费快照获取失败：{e}，整轮跳过"}
    if not asset_list:
        return {"status": "hold", "reason": "无候选资产（universe 为空且无持仓）"}

    # 3) 行情（并行拉取候选 + 持仓资产；失败的资产本轮排除出决策但不影响其它）
    prices: dict[str, float] = {}
    market: dict[str, dict[str, Any]] = {}
    workers = int(cfg.get("marketFetchWorkers", 8))
    raw_market = fetch_assets_market_parallel(
        venue, asset_list, quote, cfg, max_workers=workers
    )
    for sym in asset_list:
        mkt = raw_market.get(sym)
        if not mkt:
            continue
        if mkt.get("rules_error"):
            continue
        px = float(mkt.get("price", 0) or 0)
        if px <= 0:
            fb_px, fb_meta = fetch_price_with_fallback(sym, quote, primary=venue.venue_id)
            if fb_px > 0:
                px = fb_px
                mkt["price_source"] = fb_meta.get("source", "fallback")
        if hasattr(venue, "fetch_futures_symbol_rules"):
            f_rules = venue.fetch_futures_symbol_rules(make_pair(sym, quote))
            if f_rules:
                mkt["min_trade_usdt"] = f_rules.get("min_trade_usdt", mkt.get("min_trade_usdt", 0.0))
                mkt["min_trade_base"] = f_rules.get("min_trade_base", mkt.get("min_trade_base", 0.0))
        mkt["price"] = px
        if px > 0:
            prices[sym] = px
            market[sym] = mkt
    if not prices:
        return {"status": "skipped", "reason": "所有候选资产价格获取失败，整轮跳过"}

    # Fetch live borrow rates from venue only if not dry_run (to avoid API key noise in paper mode)
    live_borrow_rates = {}
    if not dry_run:
        try:
            live_borrow_rates = getattr(venue, "fetch_borrow_rates", lambda x: {})(list(prices.keys()))
        except Exception:
            pass

    # 4) seed holdings
    holdings = dict(state.get("holdings") or {})
    if not holdings:
        holdings = {cash: float(cfg.get("initialCapitalUsd", 10000.0))}

    initial_nav = float(state.get("initial_nav") or 0)
        
    if not dry_run:
        model_nav = calculate_futures_nav(holdings, futures_state, prices, cash)
        # Sync real global state from exchange to overwrite modeled holdings and positions
        live_state = getattr(venue, "fetch_live_state", lambda x: {"balances": {}})(list(prices.keys()) + [cash])
        if "balances" in live_state and live_state["balances"]:
            for k, v in live_state["balances"].items():
                holdings[k] = v
        if "futures_positions" in live_state:
            futures_state["positions"] = live_state["futures_positions"]
            
        real_nav = calculate_futures_nav(holdings, futures_state, prices, cash)
        if initial_nav <= 0:
            initial_nav = real_nav
        drift = abs(real_nav - model_nav) / (model_nav or 1)
        if drift > 0.05:
            send_notification(
                "Drift Alarm",
                f"NAV Drift detected: Model NAV = {model_nav:.2f}, Real NAV = {real_nav:.2f} ({(drift*100):.2f}%).",
                cfg,
            )
        
        max_drawdown = float(cfg.get("maxDrawdownKillSwitchPct", 0.15))
        if initial_nav > 0 and (initial_nav - real_nav) / initial_nav > max_drawdown:
            send_notification(
                "Drawdown Kill Switch",
                f"Drawdown exceeded {max_drawdown*100}%. Real NAV {real_nav:.2f} vs Initial {initial_nav:.2f}. Executing violent close.",
                cfg,
            )
            return trigger_kill_switch(venue, holdings, futures_state, prices, market, cfg, initial_nav, real_nav, paths, state, now)
    elif initial_nav <= 0:
        initial_nav = calculate_futures_nav(holdings, futures_state, prices, cash)
            
    for sym in asset_list:
        holdings.setdefault(sym, 0.0)

    # 5) 借币利息计提（独立于资金费拉取成败，按经过小时数线性）
    borrow_info: dict[str, Any] = {}
    if elapsed_h > 0:
        period_rates: dict[str, float] = {}
        for sym in prices:
            if holdings.get(sym, 0.0) < 0:
                per_h = asset_annual_borrow(cfg, sym, live_borrow_rates) / HOURS_PER_YEAR
                period_rates[sym] = per_h * elapsed_h
        if period_rates:
            before = holdings.get(cash, 0.0)
            if dry_run:
                holdings, futures_state = apply_borrow_fees(holdings, futures_state, prices, period_rates, cash)
                borrow_info = {"charged_usd": round(before - holdings.get(cash, 0.0), 6), "hours": round(elapsed_h, 3)}
            else:
                _, futures_state = apply_borrow_fees(dict(holdings), futures_state, prices, period_rates, cash)
                borrow_info = {"charged_usd": 0.0, "hours": round(elapsed_h, 3), "note": "live_sync"}

    # 6) 资金费快照 + 逐结算点补结算
    funding_rates: dict[str, float] = {}
    borrow_interval_rates: dict[str, float] = {}
    funding_meta: dict[str, Any] = {}
    pending_settle: list[tuple[str, int]] = []
    for sym in prices:
        snap = funding_snaps.get(sym)
        if snap is None:
            funding_meta[sym] = {"error": "no_funding"}
            continue
        rate = float(snap.get("rate_pct", 0.0))
        interval_h = int(snap.get("interval_ms", 8 * 3600 * 1000) or 8 * 3600 * 1000) / 3_600_000.0
        funding_rates[sym] = rate
        borrow_interval_rates[sym] = (asset_annual_borrow(cfg, sym, live_borrow_rates) / HOURS_PER_YEAR) * interval_h
        held = sym in positions and positions[sym].get("amount", 0) > 0
        prev_ts = int(settle_ts.get(sym, 0) or 0)
        if held and prev_ts > 0:
            pending_settle.append((sym, prev_ts))

    workers = int(cfg.get("marketFetchWorkers", 8))
    held_with_ts = {s: t for s, t in pending_settle}
    if held_with_ts and fp is not None:
        history = fetch_funding_history_parallel(
            fp, list(held_with_ts.keys()), quote, held_with_ts, workers=workers
        )
    elif held_with_ts:
        history = fetch_funding_history_parallel(
            None,
            list(held_with_ts.keys()),
            quote,
            held_with_ts,
            workers=workers,
            fetch_since_fn=lambda pair, start: fetch_funding_since(pair, start),
        )
    else:
        history = {}

    for sym in prices:
        snap = funding_snaps.get(sym)
        if snap is None:
            continue
        rate = funding_rates[sym]
        prev_ts = int(settle_ts.get(sym, 0) or 0)
        settled_n = 0
        if sym in history:
            for row in sorted(history[sym], key=lambda r: int(r.get("ts", 0))):
                if row["ts"] > now:
                    continue
                if dry_run:
                    holdings, futures_state = apply_funding_fees(
                        holdings, futures_state, prices, {sym: row["rate_pct"]}, cash
                    )
                else:
                    _, futures_state = apply_funding_fees(
                        dict(holdings), futures_state, prices, {sym: row["rate_pct"]}, cash
                    )
                settle_ts[sym] = row["ts"]
                settled_n += 1
        elif sym in positions and positions[sym].get("amount", 0) > 0 and prev_ts == 0:
            settle_ts[sym] = int(snap.get("last_settle_ts", 0) or 0)
        funding_meta[sym] = {
            "rate_pct": rate,
            "apr_pct": round(rate * FUNDINGS_PER_YEAR, 2),
            "last_settle_ts": int(snap.get("last_settle_ts", 0) or 0),
            "next_funding_ts": snap.get("next_funding_ts"),
            "settled_intervals": settled_n,
        }
        
    next_funding_times = {s: (fm.get("next_funding_ts") or 0) for s, fm in funding_meta.items()}

    leverage = float(cfg.get("leverage", 1.0))
    mmr = float(cfg.get("maintenanceMarginRate", 0.005))
    fee_rate = float(cfg.get("takerFeeRate", 0.0005))

    # 4.5) 强平守卫：永续腿触及强平价 → 紧急双腿拆解（平永续 + 平掉对冲现货）+ 罚金。
    #      delta-neutral 在 cross-margin 下极难触发；这里建模隔离保证金的最坏情况。
    liquidation_events: list[dict[str, Any]] = []
    liq_hits = check_liquidations(futures_state, prices)
    if liq_hits:
        penalty_pct = float(cfg.get("liquidationPenaltyPct", 0.01))  # 强平罚金（含滑点）
        unwind: list[dict[str, Any]] = []
        liq_notional = 0.0
        for hit in liq_hits:
            sym = hit["symbol"]
            pos = positions[sym]
            px = prices[sym]
            liq_notional += pos["amount"] * px  # 在 close 把 amount 清零之前先记下名义额
            unwind.append({
                "symbol": sym,
                "type": "close_short" if pos["side"] == "short" else "close_long",
                "amount_base": round(pos["amount"], 8),
                "amount_usdt": round(pos["amount"] * px, 2),
                "reason": f"LIQUIDATION: mark {px} 触及强平价 {hit['liq_price']:.6g}（{pos['side']}）。",
            })
            spot = holdings.get(sym, 0.0)
            if spot > 1e-9:   # forward：平掉多头现货
                unwind.append({"symbol": sym, "type": "sell", "amount_base": round(spot, 8),
                               "amount_usdt": round(spot * px, 2), "reason": "强平连带平掉对冲现货腿。"})
            elif spot < -1e-9:  # reverse：买回空头现货（margin 买入并自动还币）
                unwind.append({"symbol": sym, "type": "buy", "amount_base": round(-spot, 8),
                               "amount_usdt": round(-spot * px, 2),
                               "account": "margin", "side_effect": "auto_repay",
                               "reason": "强平连带买回对冲现货腿。"})
        if dry_run:
            ux = [{**t, "status": "simulated", "price": prices[t["symbol"]]} for t in unwind]
        else:
            ux = execute_delta_neutral_trades(venue, unwind, market, dry_run=False, config=cfg)
            
        ux_normalized = normalize_executed_for_ledger(ux, prices)

        if ux_normalized:
            holdings, futures_state = apply_simulated_futures_trades(
                holdings, futures_state, ux_normalized, prices, cash,
                spot_fee_rate=fee_rate, perp_fee_rate=fee_rate, leverage=leverage,
                maintenance_margin_rate=mmr,
            )
        # 强平罚金（按被强平名义额，在 amount 清零前已记录）
        # 注意：这里的强平罚金是本地预估的滑点惩罚。如果在实盘中真实发生交易所强平，
        # 则由于之后 fetch_live_state 同步真实余额，不再扣除本地 cash 避免双重计算。
        penalty = liq_notional * penalty_pct
        if not dry_run:
            send_notification(
                "Liquidation Event",
                f"Liquidations triggered for {', '.join([h['symbol'] for h in liq_hits])}. Penalty: {penalty:.2f} USD",
                cfg,
            )
        if dry_run:
            holdings[cash] = holdings.get(cash, 0.0) - penalty
        positions = futures_state.get("positions", {})
        liquidation_events = [{**h, "penalty_usd": round(penalty, 2)} for h in liq_hits]
        # 刚被强平的标的进入冷却，避免本轮/近期原地重开同一个爆仓仓位。
        cooldown_ms = int(float(cfg.get("liquidationCooldownHours", 24.0)) * 3600 * 1000)
        for h in liq_hits:
            liq_cooldown[h["symbol"]] = now + cooldown_ms

    # 冷却中的标的剔除出候选（仍可被现有持仓的结算/平仓逻辑管理，但不新开）。
    liq_cooldown = {s: t for s, t in liq_cooldown.items() if t > now}
    for sym in list(funding_rates):
        if sym in liq_cooldown:
            funding_rates.pop(sym, None)

    # 5) 决策：候选必须有 funding；已持仓额外并入 prices 以便引擎处理（funding 缺失则 hold）
    decision_prices = {s: prices[s] for s in funding_rates}
    decision_market = {s: market[s] for s in funding_rates}
    for sym, pos in positions.items():
        if pos.get("amount", 0) <= 0 or sym not in prices:
            continue
        decision_prices.setdefault(sym, prices[sym])
        decision_market.setdefault(sym, market.get(sym, {"price": prices[sym]}))
    
    if not dry_run:
        for sym in decision_prices:
            pair = make_pair(sym, quote)
            getattr(venue, "initialize_futures_symbol", lambda x: None)(pair)
            
    trades, meta = decide_cross_asset_arbitrage(
        holdings, futures_state, decision_prices, decision_market, cfg,
        funding_rates, borrow_interval_rates,
        next_funding_times=next_funding_times, current_time_ms=now,
    )

    # 6) 实际成交或纸面成交
    executed: list[dict[str, Any]] = []
    if trades:
        executed = execute_delta_neutral_trades(venue, trades, decision_market, dry_run=dry_run, config=cfg)
        
    if executed:
        ux = normalize_executed_for_ledger(executed, prices)
        if ux:
            holdings, futures_state = apply_simulated_futures_trades(
                holdings, futures_state, ux, prices, cash,
                spot_fee_rate=fee_rate, perp_fee_rate=fee_rate, leverage=leverage,
                maintenance_margin_rate=mmr,
            )
    # 新开仓的 symbol：结算戳对齐到最近已结算点，避免下轮把开仓前的资金费误计。
    for ex in executed:
        if str(ex.get("type", "")).startswith("open_"):
            s = ex["symbol"]
            settle_ts[s] = int(funding_meta.get(s, {}).get("last_settle_ts", 0) or 0)

    # 清理已平仓 symbol 的结算戳
    open_syms = set(futures_state.get("positions", {}).keys())
    settle_ts = {s: t for s, t in settle_ts.items() if s in open_syms}

    nav = calculate_futures_nav(holdings, futures_state, prices, cash)

    # 7) 持久化
    state.update({
        "strategyId": cfg.get("strategyId"),
        "holdings": {k: round(v, 10) for k, v in holdings.items()},
        "futures_state": futures_state,
        "funding_settle_ts": settle_ts,
        "liq_cooldown": liq_cooldown,
        "last_run_ms": now,
        "last_equity_usd": nav,
        "dry_run": dry_run,
        "initial_nav": initial_nav,
    })
    save_state(paths["state"], state)

    meta.update({
        "nav_usdt": round(nav, 2),
        "venue": venue.venue_id,
        "funding": funding_meta,
        "borrow": borrow_info,
        "margin": margin_health(holdings, futures_state, prices, cash, mmr),
        "missing_funding": [s for s in prices if s not in funding_rates],
        "cumulative_funding_paid": round(futures_state.get("cumulative_funding_paid", 0.0), 4),
        "cumulative_borrow_paid": round(futures_state.get("cumulative_borrow_paid", 0.0), 4),
        "cumulative_fees": round(futures_state.get("cumulative_fees", 0.0), 4),
        "dry_run": dry_run,
    })
    if reverse_disabled_reason:
        meta["reverse_disabled"] = reverse_disabled_reason
    if liquidation_events:
        meta["liquidations"] = liquidation_events
        meta["status"] = "LIQUIDATION"

    append_jsonl(paths["journal"], {
        "ts": now_iso(), "strategyId": cfg.get("strategyId"),
        "strategy": "cash_and_carry", "dry_run": dry_run, "meta": meta, "trades": executed,
    })
    append_jsonl(paths["equity"], {
        "ts": now_iso(), "equity_usd": round(nav, 4),
        "funding": funding_meta, "positions": futures_state.get("positions", {}),
        "holdings": state["holdings"], "dry_run": dry_run,
    })
    write_json(paths["summary"], {
        "ts": now_iso(), "strategyId": cfg.get("strategyId"),
        "status": meta.get("status"), "nav_usdt": round(nav, 2),
        "holdings": state["holdings"], "futures_state": futures_state, "funding": funding_meta,
    })

    return {"status": meta.get("status", "hold"), "trades": executed, "meta": meta, "dry_run": dry_run}


def trigger_kill_switch(venue, holdings, futures_state, prices, market, cfg, initial_nav, real_nav, paths, state, now):
    from strategies.futures.cross_asset_arbitrage import _close_pair_trades

    unwind: list[dict[str, Any]] = []
    positions = futures_state.get("positions", {})
    for sym, pos in positions.items():
        if pos.get("amount", 0) <= 1e-9:
            continue
        px = float(prices.get(sym, 0) or 0)
        if px <= 0:
            continue
        unwind.extend(_close_pair_trades(
            sym, pos, holdings.get(sym, 0.0), px, "KILL SWITCH"
        ))

    executed: list[dict[str, Any]] = []
    if unwind:
        executed = execute_delta_neutral_trades(venue, unwind, market, dry_run=False, config=cfg)

    if executed:
        # Kill switch 只入账真实成交（filled）；simulated 不应出现在 panic 平仓路径
        ux = normalize_executed_for_ledger(
            [ex for ex in executed if ex.get("status") == "filled"], prices
        )
        if ux:
            holdings, futures_state = apply_simulated_futures_trades(
                holdings, futures_state, ux, prices, cfg.get("cash", "USDT"),
                spot_fee_rate=float(cfg.get("takerFeeRate", 0.0005)),
                perp_fee_rate=float(cfg.get("takerFeeRate", 0.0005)),
            )
            state["holdings"] = {k: round(v, 10) for k, v in holdings.items()}
            state["futures_state"] = futures_state

    state["killed"] = True
    state["initial_nav"] = initial_nav
    save_state(paths["state"], state)
    
    # Log to journal
    append_jsonl(paths["journal"], {
        "ts": now_iso(), "strategyId": cfg.get("strategyId"),
        "strategy": "cash_and_carry", "dry_run": False, 
        "meta": {"status": "KILLED", "reason": "Drawdown limit reached", "initial_nav": initial_nav, "real_nav": real_nav}, 
        "trades": executed,
    })
    
    return {"status": "KILLED", "reason": "Drawdown limit reached. Strategy killed.", "trades": executed}


def run_once(cfg: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    """加文件锁后执行单轮；拿不到锁说明上一轮还在跑，本轮跳过（防并发写 state）。"""
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(paths["lock"]), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "另一实例正在运行（未取得锁），本轮跳过"}
        return run_cycle(cfg, paths)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cash-and-Carry 资金费率套利 runner（paper, 多资产）")
    parser.add_argument("--config", required=True, help="config.json 路径")
    parser.add_argument("--verbose", action="store_true", help="无交易也打印摘要")
    args = parser.parse_args()

    config_path = resolve_config_path(Path(args.config))
    if not config_path.exists():
        print(f"配置不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(config_path)
    paths = resolve_paths(cfg)

    result = run_once(cfg, paths)
    status = result.get("status")
    if status == "error":
        print(f"⚠️ {result.get('reason', '未知错误')}")
        sys.exit(1)
    if status == "skipped":
        print(f"⏸️ {result.get('reason')}")
        sys.exit(0)

    meta = result.get("meta", {})
    trades = result.get("trades") or []
    liqs = meta.get("liquidations") or []
    if liqs:
        for e in liqs:
            print(f"🚨 强平 {e['symbol']} {e['side']} @ mark {e['mark']} (liq {e['liq_price']:.6g}) 罚金 ${e.get('penalty_usd')}")
    if trades or liqs or args.verbose:
        run_mode = "模拟" if result.get("dry_run") else "实盘"
        print(f"💱 Cash&Carry（{run_mode}） status={status} NAV=${meta.get('nav_usdt')}")
        mh = meta.get("margin") or {}
        if mh.get("margin_ratio") is not None:
            print(f"  保证金: 占用 ${mh['used_margin_usd']} / 维持 ${mh['maintenance_margin_usd']} "
                  f"ratio {mh['margin_ratio']} 最近距强平 {mh['nearest_liq_distance_pct']}%")
        for sym, fm in (meta.get("funding") or {}).items():
            if isinstance(fm, dict) and "rate_pct" in fm:
                tag = f" 补结算×{fm['settled_intervals']}" if fm.get("settled_intervals") else ""
                print(f"  {sym}: funding {fm['rate_pct']}% (APR {fm['apr_pct']}%){tag}")
        if meta.get("borrow", {}).get("charged_usd"):
            print(f"  借币利息 -${meta['borrow']['charged_usd']}（{meta['borrow']['hours']}h）")
        for t in trades:
            print(f"  {t['type']} {t['symbol']} ${t.get('amount_usdt')} — {t.get('reason')}")


if __name__ == "__main__":
    main()
