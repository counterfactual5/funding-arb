#!/usr/bin/env python3
"""CEX venue adapter contract."""

from __future__ import annotations

from typing import Any, Protocol


def resolve_venue_config(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("venue")
    if isinstance(raw, str):
        venue = {"type": raw}
    elif isinstance(raw, dict):
        venue = dict(raw)
    else:
        venue = {}
    venue.setdefault("type", "bitget")
    venue.setdefault("quote", str(cfg.get("cash", "USDT")))
    venue.setdefault("market", "spot")
    return venue


def make_pair(asset: str, quote: str) -> str:
    return f"{asset.upper()}{quote.upper()}"


class CexVenue(Protocol):
    venue_id: str

    def fetch_asset_market(
        self, asset: str, quote: str, cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def fetch_balances(self, coins: list[str]) -> dict[str, float]: ...

    def fetch_borrow_rates(self, coins: list[str]) -> dict[str, float]:
        """Fetch annualized borrow rates for given coins. Returns dict of {coin: rate_decimal}. E.g. 0.12 for 12%."""
        return {}

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        """Fetch symbol rules specific to futures/mix contracts."""
        return None

    def transfer_asset(
        self, asset: str, amount: float, from_account: str, to_account: str
    ) -> bool:
        """Transfer assets between sub-accounts (e.g., spot to futures). Returns True if successful."""
        return False

    def supports_reverse_arbitrage(self) -> bool:
        """Whether the venue can margin-borrow-sell / buy-repay (Reverse C&C 现货腿)."""
        return False

    def fetch_margin_debt(self, assets: list[str]) -> dict[str, float]:
        """Outstanding margin debt (borrowed + interest) per asset, in base units."""
        return {}

    def margin_borrow(self, asset: str, amount: float) -> bool:
        """Borrow asset on cross margin. Returns True if successful."""
        return False

    def margin_repay(self, asset: str, amount: float) -> bool:
        """Repay borrowed asset on cross margin. Returns True if successful."""
        return False

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Fetch separate Spot and Futures USDT balances. Returns {'spot': val, 'futures': val}."""
        return {"spot": 0.0, "futures": 0.0}

    def fetch_live_state(self, assets: list[str]) -> dict[str, Any]:
        """Fetch unified global state: spot balances, margin debt, futures margin, futures positions."""
        return {}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """USDT 永续持仓列表（watcher/executor 腿校验用）。

        返回 [{symbol, side, qty, entry_price, liq_price, leverage, unrealized_pnl}]。
        默认实现从 fetch_live_state 提取；各 venue 可覆写为单端点查询。
        失败时抛异常（调用方据此区分「查询失败」与「确实无持仓」）。
        """
        state = self.fetch_live_state(["USDT"]) or {}
        out: list[dict[str, Any]] = []
        for base, p in (state.get("futures_positions") or {}).items():
            out.append(
                {
                    "symbol": f"{base}{quote.upper()}",
                    "side": str(p.get("side", "")).lower(),
                    "qty": abs(float(p.get("amount", 0) or 0)),
                    "entry_price": float(p.get("entry_price", 0) or 0),
                    "liq_price": float(p.get("liq_price", 0) or 0),
                    "leverage": float(p.get("leverage", 1) or 1),
                    "unrealized_pnl": float(p.get("unrealized_pnl", 0) or 0),
                }
            )
        return out

    def get_ticker(self, pair: str) -> float:
        """Spot ticker price fallback; futures callers should prefer get_futures_ticker()."""
        return 0.0

    def get_futures_ticker(self, pair: str) -> float:
        """Perpetual futures ticker price fallback."""
        return 0.0

    def get_all_futures_tickers(self, cache_sec: int = 5) -> dict[str, float]:
        """Bulk perpetual futures last prices {PAIR: price}. Cached briefly for watcher loops."""
        return {}

    def initialize_futures_symbol(self, pair: str) -> None:
        """Initialize futures configuration (marginType, leverage, positionSide) for a specific pair."""
        pass

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]: ...
