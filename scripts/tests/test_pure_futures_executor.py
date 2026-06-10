#!/usr/bin/env python3
"""Hermetic tests for pure futures cross-venue executor."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.pure_futures_executor import (  # noqa: E402
    close_pure_futures_pair,
    load_pure_futures_positions,
    open_pure_futures_pair,
    rebalance_pure_futures_pair,
)

TMP = Path("/tmp/funding-arb-test-pure-futures")


class FakeFuturesVenue:
    def __init__(
        self,
        venue_id: str,
        price: float = 100.0,
        fail_types: set[str] | None = None,
        balances: dict[str, float] | None = None,
    ):
        self.venue_id = venue_id
        self.price = price
        self.fail_types = fail_types or set()
        self.trades: list[dict] = []
        self.initialized: list[str] = []
        self.transfers: list[tuple] = []
        self.balances = balances if balances is not None else {
            "spot": 100000.0,
            "futures": 100000.0,
        }

    def fetch_futures_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return {
            "symbol": pair,
            "quantity_precision": 4,
            "quote_precision": 2,
            "min_trade_usdt": 5.0,
            "min_trade_base": 0.0,
        }

    def fetch_symbol_rules(self, pair: str, cache_sec: int = 3600):
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    def get_ticker(self, pair: str):
        return self.price

    def initialize_futures_symbol(self, pair: str):
        self.initialized.append(pair)

    def fetch_usdt_account_balances(self):
        return dict(self.balances)

    def transfer_asset(
        self, asset: str, amount: float, from_account: str, to_account: str
    ):
        self.transfers.append((asset, amount, from_account, to_account))
        if self.balances.get(from_account, 0.0) < amount:
            return False
        self.balances[from_account] -= amount
        self.balances[to_account] = self.balances.get(to_account, 0.0) + amount
        return True

    def execute_trades(self, trades, market, dry_run=True):
        out = []
        for t in trades:
            self.trades.append(dict(t, dry_run=dry_run))
            typ = t["type"]
            if typ in self.fail_types and not dry_run:
                out.append(
                    {
                        "symbol": t["symbol"],
                        "type": typ,
                        "status": "failed",
                        "error": f"fail {typ}",
                    }
                )
            else:
                out.append(
                    {
                        "symbol": t["symbol"],
                        "type": typ,
                        "status": "simulated" if dry_run else "filled",
                        "exec_qty": t["amount_base"],
                        "exec_price": self.price,
                    }
                )
        return out


def _path(name: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    p = TMP / f"{name}.json"
    if p.exists():
        p.unlink()
    return p


def test_dry_run_open_and_close_records_position():
    path = _path("dry")
    lv, sv = FakeFuturesVenue("okx"), FakeFuturesVenue("bybit")
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=True,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok and res.state == "simulated"
    rows = load_pure_futures_positions(path)
    assert len(rows) == 1 and rows[0]["status"] == "open"
    assert rows[0]["direction"] == "forward"
    assert [t["type"] for t in lv.trades + sv.trades] == ["open_long", "open_short"]

    res2 = close_pure_futures_pair(
        res.position_id,
        dry_run=True,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res2.ok and res2.state == "simulated"
    rows = load_pure_futures_positions(path)
    assert rows[0]["status"] == "closed"


def test_live_open_both_legs_filled():
    path = _path("live")
    lv, sv = FakeFuturesVenue("okx"), FakeFuturesVenue("bybit")
    res = open_pure_futures_pair(
        "ETH",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok and res.state == "filled"
    assert [t["type"] for t in lv.trades] == ["open_long"]
    assert [t["type"] for t in sv.trades] == ["open_short"]
    rows = load_pure_futures_positions(path)
    assert rows[0]["dry_run"] is False
    assert rows[0]["qty"] > 0


def test_short_leg_fail_rolls_back_long():
    path = _path("rollback_open")
    lv = FakeFuturesVenue("okx")
    sv = FakeFuturesVenue("bybit", fail_types={"open_short"})
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert not res.ok and res.state == "rolled_back"
    assert [t["type"] for t in lv.trades] == ["open_long", "close_long"]
    assert load_pure_futures_positions(path) == []


def test_short_leg_fail_and_rollback_fail_is_naked():
    path = _path("naked_open")
    lv = FakeFuturesVenue("okx", fail_types={"close_long"})
    sv = FakeFuturesVenue("bybit", fail_types={"open_short"})
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert not res.ok and res.state == "naked"


def test_close_long_fail_reopens_short():
    path = _path("rollback_close")
    lv = FakeFuturesVenue("okx")
    sv = FakeFuturesVenue("bybit")
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok
    lv.fail_types.add("close_long")
    res2 = close_pure_futures_pair(
        res.position_id,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert not res2.ok and res2.state == "rolled_back"
    assert [t["type"] for t in sv.trades] == ["open_short", "close_short", "open_short"]
    assert load_pure_futures_positions(path)[0]["status"] == "open"


def test_mark_spread_gate_aborts():
    path = _path("spread_gate")
    lv = FakeFuturesVenue("okx", price=100.0)
    sv = FakeFuturesVenue("bybit", price=103.0)
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=True,
        max_mark_spread_pct=1.0,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert not res.ok and res.state == "aborted"
    assert "标记价差" in res.logs[0]


def _open_live(path: Path) -> tuple:
    lv, sv = FakeFuturesVenue("okx"), FakeFuturesVenue("bybit")
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok
    return lv, sv, res.position_id


def test_rebalance_balanced_is_noop():
    path = _path("rebal_noop")
    lv, sv, pid = _open_live(path)
    res = rebalance_pure_futures_pair(
        pid,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=5.0,
        short_qty=5.0,
    )
    assert res.ok and res.state == "balanced"
    # No extra trades beyond the two opens
    assert [t["type"] for t in lv.trades] == ["open_long"]
    assert [t["type"] for t in sv.trades] == ["open_short"]


def test_rebalance_trims_long_leg():
    path = _path("rebal_trim_long")
    lv, sv, pid = _open_live(path)
    res = rebalance_pure_futures_pair(
        pid,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=5.0,
        short_qty=4.0,
    )
    assert res.ok and res.state == "filled"
    trims = [t for t in lv.trades if t["type"] == "close_long"]
    assert len(trims) == 1
    assert abs(trims[0]["amount_base"] - 1.0) < 1e-9
    pos = load_pure_futures_positions(path)[0]
    assert pos["qty"] == 4.0
    assert pos["long_qty"] == 4.0 and pos["short_qty"] == 4.0
    assert pos["last_rebalance"]["venue"] == "okx"


def test_rebalance_trims_short_leg():
    path = _path("rebal_trim_short")
    lv, sv, pid = _open_live(path)
    res = rebalance_pure_futures_pair(
        pid,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=3.0,
        short_qty=3.5,
    )
    assert res.ok and res.state == "filled"
    trims = [t for t in sv.trades if t["type"] == "close_short"]
    assert len(trims) == 1
    assert abs(trims[0]["amount_base"] - 0.5) < 1e-9
    assert load_pure_futures_positions(path)[0]["qty"] == 3.0


def test_rebalance_trim_failure_aborts():
    path = _path("rebal_fail")
    lv, sv, pid = _open_live(path)
    lv.fail_types.add("close_long")
    res = rebalance_pure_futures_pair(
        pid,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=5.0,
        short_qty=4.0,
    )
    assert not res.ok and res.state == "aborted"
    # Position record untouched on failure
    pos = load_pure_futures_positions(path)[0]
    assert "last_rebalance" not in pos


def test_rebalance_leg_gone_aborts():
    path = _path("rebal_gone")
    lv, sv, pid = _open_live(path)
    res = rebalance_pure_futures_pair(
        pid,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=5.0,
        short_qty=0.0,
    )
    assert not res.ok and res.state == "aborted"


def test_open_aborts_when_margin_insufficient():
    """两所余额都不足 → 不下任何单直接放弃。"""
    path = _path("margin_insufficient")
    lv = FakeFuturesVenue("okx", balances={"spot": 0.0, "futures": 100.0})
    sv = FakeFuturesVenue("bybit", balances={"spot": 0.0, "futures": 100.0})
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,  # 需要 ≥ 525 (1.05x)
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert not res.ok and res.state == "aborted"
    assert lv.trades == [] and sv.trades == []
    assert any("保证金不足" in log for log in res.logs)


def test_open_transfers_shortfall_from_spot():
    """futures 不足但 spot 可补 → 划转差额后正常开仓。"""
    path = _path("margin_transfer")
    lv = FakeFuturesVenue("okx", balances={"spot": 1000.0, "futures": 100.0})
    sv = FakeFuturesVenue("bybit", balances={"spot": 1000.0, "futures": 600.0})
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok and res.state == "filled"
    # okx 划转差额 525 - 100 = 425；bybit 600 ≥ 525 无需划转
    assert len(lv.transfers) == 1
    assert abs(lv.transfers[0][1] - 425.0) < 1e-9
    assert sv.transfers == []


def test_open_margin_includes_capital_buffer():
    """capital_buffer_pct 计入保证金要求。"""
    path = _path("margin_buffer")
    # 余额刚好满足 1.05x 但不够 1.05x + 10% buffer
    lv = FakeFuturesVenue("okx", balances={"spot": 0.0, "futures": 530.0})
    sv = FakeFuturesVenue("bybit", balances={"spot": 0.0, "futures": 530.0})
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,  # 1.05x = 525 ≤ 530，但 + 10% buffer = 575 > 530
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        capital_buffer_pct=10.0,
    )
    assert not res.ok and res.state == "aborted"
    # 不带 buffer 则可开
    res2 = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res2.ok


def test_open_margin_check_skipped_when_api_fails():
    """余额接口异常 → 跳过校验放行（不阻塞交易）。"""
    path = _path("margin_api_fail")
    lv = FakeFuturesVenue("okx")
    sv = FakeFuturesVenue("bybit")

    def _boom():
        raise RuntimeError("api down")

    lv.fetch_usdt_account_balances = _boom
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=False,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    assert res.ok and res.state == "filled"
    assert any("跳过校验" in log for log in res.logs)


def test_rebalance_dry_run_no_record_change():
    path = _path("rebal_dry")
    lv, sv = FakeFuturesVenue("okx"), FakeFuturesVenue("bybit")
    res = open_pure_futures_pair(
        "BTC",
        "okx",
        "bybit",
        500,
        dry_run=True,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
    )
    res2 = rebalance_pure_futures_pair(
        res.position_id,
        dry_run=True,
        long_venue=lv,
        short_venue=sv,
        positions_path=path,
        long_qty=5.0,
        short_qty=4.0,
    )
    assert res2.ok and res2.state == "simulated"
    pos = load_pure_futures_positions(path)[0]
    assert "last_rebalance" not in pos
