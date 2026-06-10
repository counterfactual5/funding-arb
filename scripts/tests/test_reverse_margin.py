#!/usr/bin/env python3
"""Hermetic unit tests for Reverse C&C margin borrow/repay wiring (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.delta_neutral_executor import (
    _margin_rollback_tags,
    execute_delta_neutral_trades,
)
from execution.run_cash_and_carry import apply_live_safety, disable_reverse
from strategies.futures.cross_asset_arbitrage import (
    _close_pair_trades,
    decide_cross_asset_arbitrage,
)
from accounting.futures.delta_neutral_portfolio import default_futures_state
from venues.binance import BinanceSpotVenue


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _cfg(**over):
    cc = {
        "maxConcurrentPairs": 1,
        "tradeUsdPerSlot": 1000.0,
        "entryFundingRatePct": 0.05,
        "exitFundingRatePct": 0.01,
        "reverseEntryFundingRatePct": -0.05,
        "reverseExitFundingRatePct": -0.01,
        "minReverseSpreadPct": 0.02,
        "minNetEdgePct": 0.02,
        "preemptionFrictionBufferPct": 1e9,
    }
    cc.update(over)
    return {"cash": "USDT", "crossAssetArbitrage": cc}


class FakeVenue:
    """记录 execute_trades 调用并按指令成功/失败的假 venue。"""

    venue_id = "fake"

    def __init__(self, fail_types: set[str] | None = None, margin_debt: float | None = None):
        self.calls: list[dict] = []
        self.transfers: list[tuple] = []
        self.fail_types = fail_types or set()
        self._margin_debt = margin_debt

    def execute_trades(self, trades, market, dry_run):
        out = []
        for t in trades:
            self.calls.append(dict(t))
            rec = dict(t)
            px = market.get(t["symbol"], {}).get("price", 100.0)
            if t["type"] in self.fail_types:
                rec["status"] = "failed"
            else:
                rec["status"] = "filled"
                rec["exec_qty"] = t["amount_base"]
                rec["exec_price"] = px
            out.append(rec)
        return out

    def transfer_asset(self, asset, amount, from_acct, to_acct):
        self.transfers.append((asset, amount, from_acct, to_acct))
        return True

    def fetch_margin_debt(self, assets):
        if self._margin_debt is None:
            return {a: 0.0 for a in assets}
        return {a: self._margin_debt for a in assets}


def test_reverse_open_tags_margin_legs():
    trades, _ = decide_cross_asset_arbitrage(
        {"USDT": 10000.0, "ETH": 0.0}, default_futures_state(),
        {"ETH": 100.0}, {"ETH": {"price": 100.0}}, _cfg(),
        {"ETH": -0.10}, {"ETH": 0.01},
    )
    by_type = {t["type"]: t for t in trades}
    assert set(by_type) == {"sell", "open_long"}
    sell = by_type["sell"]
    assert sell["account"] == "margin"
    assert sell["side_effect"] == "auto_borrow"
    assert "account" not in by_type["open_long"]


def test_reverse_close_tags_auto_repay():
    pos = {"amount": 10.0, "side": "long", "entry_price": 100.0}
    trades = _close_pair_trades("ETH", pos, -10.0, 100.0, "test close")
    by_type = {t["type"]: t for t in trades}
    assert set(by_type) == {"buy", "close_long"}
    buy = by_type["buy"]
    assert buy["account"] == "margin"
    assert buy["side_effect"] == "auto_repay"
    # forward 平仓不应带 margin 标记
    fwd = _close_pair_trades(
        "ETH", {"amount": 10.0, "side": "short", "entry_price": 100.0}, 10.0, 100.0, "fwd"
    )
    assert all("account" not in t for t in fwd)


def test_margin_rollback_tags_invert():
    assert _margin_rollback_tags(
        {"account": "margin", "side_effect": "auto_borrow"}
    ) == {"account": "margin", "side_effect": "auto_repay"}
    assert _margin_rollback_tags(
        {"account": "margin", "side_effect": "auto_repay"}
    ) == {"account": "margin", "side_effect": "auto_borrow"}
    assert _margin_rollback_tags({"type": "buy"}) == {}


def test_executor_reverse_open_live():
    venue = FakeVenue()
    trades = [
        {"symbol": "ETH", "type": "sell", "amount_base": 10.0, "amount_usdt": 1000.0,
         "account": "margin", "side_effect": "auto_borrow", "reason": "rev open"},
        {"symbol": "ETH", "type": "open_long", "amount_base": 10.0, "amount_usdt": 1000.0,
         "reason": "rev open"},
    ]
    market = {"ETH": {"price": 100.0}}
    executed = execute_delta_neutral_trades(venue, trades, market, dry_run=False, config={"cash": "USDT"})
    statuses = {t["type"]: t["status"] for t in executed}
    assert statuses == {"sell": "filled", "open_long": "filled"}
    # 现货腿带着 margin 标记到达 venue
    sell_call = next(c for c in venue.calls if c["type"] == "sell")
    assert sell_call["account"] == "margin"
    assert sell_call["side_effect"] == "auto_borrow"
    # 开多前应有 spot->futures 的保证金划转
    assert any(t[2] == "spot" and t[3] == "futures" for t in venue.transfers)


def test_executor_rollback_inverts_margin_tags():
    venue = FakeVenue(fail_types={"open_long"})
    trades = [
        {"symbol": "ETH", "type": "sell", "amount_base": 10.0, "amount_usdt": 1000.0,
         "account": "margin", "side_effect": "auto_borrow", "reason": "rev open"},
        {"symbol": "ETH", "type": "open_long", "amount_base": 10.0, "amount_usdt": 1000.0,
         "reason": "rev open"},
    ]
    market = {"ETH": {"price": 100.0}}
    execute_delta_neutral_trades(venue, trades, market, dry_run=False, config={"cash": "USDT"})
    rollback = next(c for c in venue.calls if "ROLLBACK" in str(c.get("reason", "")))
    assert rollback["type"] == "buy"
    assert rollback["account"] == "margin"
    assert rollback["side_effect"] == "auto_repay"


def test_executor_reverse_close_aligns_debt():
    # 实际债务 10.05（含利息尘埃），买回量应对齐到债务而非 10.0
    venue = FakeVenue(margin_debt=10.05)
    trades = [
        {"symbol": "ETH", "type": "buy", "amount_base": 10.0, "amount_usdt": 1000.0,
         "account": "margin", "side_effect": "auto_repay", "reason": "rev close"},
        {"symbol": "ETH", "type": "close_long", "amount_base": 10.0, "amount_usdt": 1000.0,
         "reason": "rev close"},
    ]
    market = {"ETH": {"price": 100.0}}
    execute_delta_neutral_trades(venue, trades, market, dry_run=False, config={"cash": "USDT"})
    buy_call = next(c for c in venue.calls if c["type"] == "buy")
    assert approx(buy_call["amount_base"], 10.05)

    # 债务偏离 >2% 视为异常，不采用
    venue2 = FakeVenue(margin_debt=12.0)
    trades2 = [dict(t) for t in trades]
    execute_delta_neutral_trades(venue2, trades2, market, dry_run=False, config={"cash": "USDT"})
    buy_call2 = next(c for c in venue2.calls if c["type"] == "buy")
    assert approx(buy_call2["amount_base"], 10.0)


def test_bitget_margin_order_body_and_dispatch():
    import venues.bitget as bg

    captured: list[dict] = []

    def fake_api(method, path, params=None, body=None):
        if path.endswith("place-order"):
            captured.append(dict(body))
            return {"code": "00000", "data": {"orderId": "123"}}
        if path.endswith("/fills"):
            return {"code": "00000", "data": {"fills": [
                {"size": "1", "amount": "100", "priceAvg": "100"}]}}
        return {"code": "00000", "data": []}

    old = bg._api_call
    bg._api_call = fake_api
    try:
        v = bg.BitgetSpotVenue()
        # sell + auto_borrow → loanType=autoLoan, base 计量
        ok, detail = v.place_margin_order(
            "ETHUSDT", "sell", 1.0, 4, ref_price=100.0, side_effect="auto_borrow"
        )
        assert ok and detail["exec_qty"] == 1.0
        assert captured[-1]["loanType"] == "autoLoan"
        assert captured[-1]["force"] == "gtc"
        assert captured[-1]["baseSize"] == "1"
        # buy + auto_repay → loanType=autoRepay, quote 计量（含 1% buffer）
        ok2, _ = v.place_margin_order(
            "ETHUSDT", "buy", 1.0, 4, ref_price=100.0, side_effect="auto_repay"
        )
        assert ok2
        assert captured[-1]["loanType"] == "autoRepay"
        assert approx(float(captured[-1]["quoteSize"]), 101.0, 0.01)
        # execute_trades 将 margin 标记的现货腿路由到 place_margin_order
        seen: dict = {}

        def spy(pair, ttype, amount_base, qp=6, ref_price=0.0, side_effect=""):
            seen.update({"pair": pair, "type": ttype, "side_effect": side_effect})
            return True, {"order_id": "1", "exec_price": ref_price, "exec_qty": amount_base}

        v.place_margin_order = spy
        res = v.execute_trades(
            [{"symbol": "ETH", "type": "sell", "amount_base": 1.0, "amount_usdt": 100.0,
              "account": "margin", "side_effect": "auto_borrow", "reason": "t"}],
            {"ETH": {"price": 100.0, "pair": "ETHUSDT", "quantity_precision": 4}},
            dry_run=False,
        )
        assert res[0]["status"] == "filled"
        assert seen == {"pair": "ETHUSDT", "type": "sell", "side_effect": "auto_borrow"}
    finally:
        bg._api_call = old


def test_bybit_margin_order_body_and_dispatch():
    import venues.bybit as bb

    captured: list[dict] = []

    def fake_api(method, path, params=None, body=None):
        if path.endswith("/order/create"):
            captured.append(dict(body))
            return {"retCode": 0, "result": {"orderId": "456"}}
        if path.endswith("/order/realtime"):
            return {"retCode": 0, "result": {"list": [
                {"avgPrice": "100", "cumExecQty": "1", "cumExecValue": "100",
                 "orderStatus": "Filled"}]}}
        return {"retCode": 0, "result": {}}

    old = bb._api_call
    bb._api_call = fake_api
    try:
        v = bb.BybitSpotVenue()
        # sell：isLeverage=1 自动借币卖出
        ok, detail = v.place_margin_order("ETHUSDT", "sell", 1.0, 4, ref_price=100.0)
        assert ok and detail["exec_qty"] == 1.0
        assert captured[-1]["isLeverage"] == 1
        assert "marketUnit" not in captured[-1]
        # buy：base 计量买回，自动冲销负债
        ok2, _ = v.place_margin_order("ETHUSDT", "buy", 1.0, 4, ref_price=100.0)
        assert ok2
        assert captured[-1]["marketUnit"] == "baseCoin"
        assert captured[-1]["isLeverage"] == 1
        # execute_trades margin 分支
        seen: dict = {}

        def spy(pair, ttype, amount_base, qp=6, ref_price=0.0):
            seen.update({"pair": pair, "type": ttype})
            return True, {"order_id": "1", "exec_price": ref_price, "exec_qty": amount_base}

        v.place_margin_order = spy
        res = v.execute_trades(
            [{"symbol": "ETH", "type": "buy", "amount_base": 1.0, "amount_usdt": 100.0,
              "account": "margin", "side_effect": "auto_repay", "reason": "t"}],
            {"ETH": {"price": 100.0, "pair": "ETHUSDT", "quantity_precision": 4}},
            dry_run=False,
        )
        assert res[0]["status"] == "filled"
        assert seen == {"pair": "ETHUSDT", "type": "buy"}
    finally:
        bb._api_call = old


def test_bybit_margin_repay_prefers_account_repay():
    import venues.bybit as bb

    paths: list[str] = []

    def fake_api(method, path, params=None, body=None):
        paths.append(path)
        if path.endswith("/account/repay"):
            return {"retCode": 0, "result": {"resultStatus": "SU"}}
        raise RuntimeError("should not reach quick-repayment")

    old = bb._api_call
    bb._api_call = fake_api
    try:
        assert bb.BybitSpotVenue().margin_repay("ETH", 1.0) is True
        assert paths[0].endswith("/account/repay")
    finally:
        bb._api_call = old


def test_okx_margin_order_body_and_dispatch():
    import venues.okx as ox

    captured: list[dict] = []
    acct_lv = {"v": "2"}

    def fake_api(method, path, params=None, body=None):
        if path == "/api/v5/account/config":
            return {"code": "0", "data": [{"acctLv": acct_lv["v"]}]}
        if path == "/api/v5/account/set-auto-loan":
            return {"code": "0", "data": [{"autoLoan": True}]}
        if path == "/api/v5/account/set-auto-repay":
            return {"code": "0", "data": [{"autoRepay": True}]}
        if path.endswith("/trade/order") and method == "POST":
            captured.append(dict(body))
            return {"code": "0", "data": [{"ordId": "789"}]}
        if path.endswith("/trade/order") and method == "GET":
            return {"code": "0", "data": [{"avgPx": "100", "fillSz": "1", "accFillSz": "1", "state": "filled"}]}
        return {"code": "0", "data": []}

    old = ox._api_call
    old_cache = ox._acct_config_cache
    ox._api_call = fake_api
    ox._acct_config_cache = None
    try:
        v = ox.OkxSpotVenue()
        v.fetch_symbol_rules = lambda pair, cache_sec=3600: {"quote_precision": 2}
        # acctLv=2 合约模式：sell 市价 + ccy（隐式借币，不再手动 borrow）
        ok, detail = v.place_margin_order(
            "ETH-USDT", "sell", 1.0, 4, ref_price=100.0, side_effect="auto_borrow"
        )
        assert ok and detail["exec_qty"] == 1.0
        assert captured[-1]["tdMode"] == "cross"
        assert captured[-1]["side"] == "sell"
        assert captured[-1]["ordType"] == "market"
        assert captured[-1]["ccy"] == "USDT"
        # buy 平仓：IOC 限价（px 上浮 1%）+ reduceOnly，禁用 tgtCcy
        ok2, _ = v.place_margin_order(
            "ETH-USDT", "buy", 1.0, 4, ref_price=100.0, side_effect="auto_repay"
        )
        assert ok2
        assert captured[-1]["ordType"] == "ioc"
        assert captured[-1]["px"] == "101.00"
        assert captured[-1]["reduceOnly"] is True
        assert "tgtCcy" not in captured[-1]
        # buy 无 ref_price → 回退 ticker；ticker 也无价则拒单（IOC 需要限价）
        v.get_ticker = lambda pair: 200.0
        ok3a, _ = v.place_margin_order("ETH-USDT", "buy", 1.0, 4, ref_price=0.0)
        assert ok3a and captured[-1]["px"] == "202.00"
        v.get_ticker = lambda pair: 0.0
        ok3, err = v.place_margin_order("ETH-USDT", "buy", 1.0, 4, ref_price=0.0)
        assert not ok3 and "ref_price" in err["error"]
        # acctLv=3 多币种：不带 ccy，靠 autoLoan/autoRepay
        acct_lv["v"] = "3"
        ox._acct_config_cache = None
        ok4, _ = v.place_margin_order(
            "ETH-USDT", "sell", 1.0, 4, ref_price=100.0, side_effect="auto_borrow"
        )
        assert ok4 and "ccy" not in captured[-1]
        seen: dict = {}

        def spy(pair, ttype, amount_base, qp=6, ref_price=0.0, side_effect=""):
            seen.update({"pair": pair, "type": ttype, "side_effect": side_effect})
            return True, {"order_id": "1", "exec_price": ref_price, "exec_qty": amount_base}

        v.place_margin_order = spy
        res = v.execute_trades(
            [{"symbol": "ETH", "type": "sell", "amount_base": 1.0, "amount_usdt": 100.0,
              "account": "margin", "side_effect": "auto_borrow", "reason": "t"}],
            {"ETH": {"price": 100.0, "pair": "ETH-USDT", "quantity_precision": 4}},
            dry_run=False,
        )
        assert res[0]["status"] == "filled"
        assert seen == {"pair": "ETH-USDT", "type": "sell", "side_effect": "auto_borrow"}
    finally:
        ox._api_call = old
        ox._acct_config_cache = old_cache


def test_capability_and_safety_gate():
    assert BinanceSpotVenue().supports_reverse_arbitrage() is True
    # 默认协议：未实现的 venue 不支持
    assert not getattr(FakeVenue(), "supports_reverse_arbitrage", lambda: False)()
    # 实盘未显式开启 → reverse 阈值钳死
    cfg = {"dry_run": False, "crossAssetArbitrage": {"reverseEntryFundingRatePct": -0.05}}
    assert apply_live_safety(cfg)["crossAssetArbitrage"]["reverseEntryFundingRatePct"] == -999.0
    # disable_reverse 同时钳两个配置块
    cfg2 = {"crossAssetArbitrage": {"reverseEntryFundingRatePct": -0.05},
            "cashAndCarry": {"reverseEntryFundingRatePct": -0.05}}
    disable_reverse(cfg2)
    assert cfg2["crossAssetArbitrage"]["reverseEntryFundingRatePct"] == -999.0
    assert cfg2["cashAndCarry"]["reverseEntryFundingRatePct"] == -999.0


if __name__ == "__main__":
    test_reverse_open_tags_margin_legs()
    test_reverse_close_tags_auto_repay()
    test_margin_rollback_tags_invert()
    test_executor_reverse_open_live()
    test_executor_rollback_inverts_margin_tags()
    test_executor_reverse_close_aligns_debt()
    test_bitget_margin_order_body_and_dispatch()
    test_bybit_margin_order_body_and_dispatch()
    test_bybit_margin_repay_prefers_account_repay()
    test_okx_margin_order_body_and_dispatch()
    test_capability_and_safety_gate()
    print("ALL PASSED")
