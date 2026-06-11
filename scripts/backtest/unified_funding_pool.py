#!/usr/bin/env python3
"""Cross-exchange funding rate unified pool — spot leg and futures leg can be split across different venues.

Core idea:
  - Forward: open short perp at the venue with the highest funding, buy spot at the venue with the lowest cost and available spot
  - Reverse: open long perp at the venue with the lowest (most negative) funding, borrow-sell at the venue with the lowest borrow rate
  - Three venues (bitget/okx/bybit) are abstracted as a unified routing table; both legs need not be on the same venue
  - Pure perp: perp long + perp short, exploiting funding rate differences between two venues, no spot/borrow
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Literal

from backtest.borrow_providers import (
    _venue_reverse_executable,
    borrow_cost_per_period,
    get_borrow_provider,
)
from backtest.funding_providers import get_funding_provider
from core.fee_providers import (
    pair_open_taker_fee_pct,
    prefetch_futures_fee_rates,
    taker_fee_pct,
)
from market.parallel_fetch import run_io_parallel

Direction = Literal["forward", "reverse"]

DEFAULT_VENUES = ("bitget", "okx", "bybit")
BLACKLIST = {"USDC", "FDUSD", "TUSD", "BTCDOM", "BUSD"}
# Taker fees differ for spot vs futures: spot leg is typically 0.1%, much higher than futures leg
SPOT_TAKER_FEE = {
    "bitget": 0.001,
    "binance": 0.001,
    "okx": 0.001,
    "bybit": 0.001,
}
VENUE_CLASSES = {
    "bitget": "venues.bitget.BitgetSpotVenue",
    "bybit": "venues.bybit.BybitSpotVenue",
    "okx": "venues.okx.OkxSpotVenue",
    "binance": "venues.binance.BinanceSpotVenue",
}
HOURS_PER_YEAR = 365.0 * 24.0
DEFAULT_BORROW_FALLBACK_ANNUAL_PCT = 8.0
DEFAULT_IO_WORKERS = 8
DEFAULT_REFERENCE_TRADE_USD = 500.0


def _get_venue(venue: str):
    parts = VENUE_CLASSES[venue].rsplit(".", 1)
    mod = __import__(parts[0], fromlist=[parts[1]])
    return getattr(mod, parts[1])()


def _spot_fee_pct(venue: str) -> float:
    return SPOT_TAKER_FEE.get(venue, 0.001) * 100.0


def _borrow_interval_pct(
    daily_pct: float, annual_pct: float, interval_h: float, fallback_annual: float
) -> float:
    if daily_pct > 0:
        return borrow_cost_per_period(daily_pct, interval_h)
    annual = annual_pct if annual_pct > 0 else fallback_annual
    return (annual / HOURS_PER_YEAR) * interval_h if annual > 0 else 0.0


@dataclass
class VenueLeg:
    """Single-venue, single-asset capability snapshot."""

    venue: str
    base: str
    symbol: str
    rate_pct: float
    interval_h: float
    next_funding_ts: int
    mark_price: float
    has_spot: bool = False
    spot_price: float = 0.0
    borrowable: bool = False
    borrow_daily_pct: float = 0.0
    borrow_annual_pct: float = 0.0
    max_borrow: str = ""


@dataclass
class CrossRoute:
    """Cross-venue (or same-venue) arbitrage route."""

    base: str
    direction: Direction
    futures_venue: str
    spot_venue: str
    funding_rate_pct: float
    interval_h: float
    next_funding_ts: int
    borrow_per_period_pct: float
    futures_fee_pct: float
    spot_fee_pct: float
    total_fee_pct: float
    net_edge_pct: float
    annual_funding_pct: float
    same_venue: bool
    spot_price: float = 0.0
    borrow_annual_pct: float = 0.0
    transfer_fee_usdt: float = 0.0
    transfer_fee_pct: float = 0.0
    net_edge_all_in_pct: float = 0.0
    transfer_chain: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "direction": self.direction,
            "futures_venue": self.futures_venue,
            "spot_venue": self.spot_venue,
            "funding_rate_pct": self.funding_rate_pct,
            "interval_h": self.interval_h,
            "next_funding_ts": self.next_funding_ts,
            "borrow_per_period_pct": self.borrow_per_period_pct,
            "futures_fee_pct": self.futures_fee_pct,
            "spot_fee_pct": self.spot_fee_pct,
            "total_fee_pct": self.total_fee_pct,
            "net_edge_pct": self.net_edge_pct,
            "annual_funding_pct": self.annual_funding_pct,
            "same_venue": self.same_venue,
            "spot_price": self.spot_price,
            "borrow_annual_pct": self.borrow_annual_pct,
            "transfer_fee_usdt": self.transfer_fee_usdt,
            "transfer_fee_pct": self.transfer_fee_pct,
            "net_edge_all_in_pct": self.net_edge_all_in_pct,
            "transfer_chain": self.transfer_chain,
            "note": self.note,
        }


@dataclass
class UnifiedFundingPool:
    """Multi-venue aggregated pool."""

    venues: tuple[str, ...] = DEFAULT_VENUES
    borrow_fallback_annual_pct: float = DEFAULT_BORROW_FALLBACK_ANNUAL_PCT
    max_workers: int = DEFAULT_IO_WORKERS
    reference_trade_usd: float = DEFAULT_REFERENCE_TRADE_USD
    legs_by_base: dict[str, list[VenueLeg]] = field(default_factory=dict)
    fee_cache: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)

    def _leg_futures_fee_pct(self, leg: VenueLeg) -> float:
        return taker_fee_pct(
            leg.venue,
            leg.symbol,
            fee_cache=self.fee_cache or None,
        )

    def _pair_open_fee_pct(
        self, long_l: VenueLeg, short_l: VenueLeg
    ) -> tuple[float, float, float]:
        return pair_open_taker_fee_pct(
            long_l.venue,
            long_l.symbol,
            short_l.venue,
            short_l.symbol,
            fee_cache=self.fee_cache or None,
        )

    def _apply_transfer_cost(self, route: CrossRoute) -> CrossRoute:
        """Add USDT transfer cost to cross-venue routes (estimated at reference_trade_usd)."""
        if route.same_venue or self.reference_trade_usd <= 0:
            route.net_edge_all_in_pct = route.net_edge_pct
            return route
        try:
            from transfer.cross_venue_router import estimate_transfer_fee

            fee_usdt, fee_pct, chain = estimate_transfer_fee(
                route.futures_venue,
                route.spot_venue,
                "USDT",
                self.reference_trade_usd,
            )
            route.transfer_fee_usdt = round(fee_usdt, 6)
            route.transfer_fee_pct = round(fee_pct, 4)
            route.transfer_chain = chain or ""
            route.net_edge_all_in_pct = round(route.net_edge_pct - fee_pct, 4)
        except Exception as e:
            route.note = (
                route.note + "; " if route.note else ""
            ) + f"transfer_est_err:{e}"
            route.net_edge_all_in_pct = route.net_edge_pct
        return route

    def refresh(self, universe_min: float = 0.03) -> None:
        """Fetch funding from all venues, and supplement spot/borrow capabilities by threshold (parallel I/O)."""
        self.legs_by_base = {}

        def _fetch_funding(venue: str) -> tuple[str, list[VenueLeg]]:
            fp = get_funding_provider(venue)
            rows = fp.fetch_all("USDT")
            imap = fp.fetch_interval_map("USDT")
            legs: list[VenueLeg] = []
            for row in rows:
                sym = str(row["symbol"]).upper()
                if not sym.endswith("USDT"):
                    continue
                base = sym[:-4]
                if not base or base in BLACKLIST:
                    continue
                legs.append(
                    VenueLeg(
                        venue=venue,
                        base=base,
                        symbol=sym,
                        rate_pct=float(row["rate_pct"]),
                        interval_h=float(imap.get(sym, 8.0)),
                        next_funding_ts=int(row.get("next_funding_ts", 0) or 0),
                        mark_price=float(row.get("mark_price", 0) or 0),
                    )
                )
            return venue, legs

        per_venue = run_io_parallel(
            list(self.venues),
            _fetch_funding,
            max_workers=len(self.venues),
            swallow_errors=True,
            on_error=lambda v, e: print(
                f"[unified_pool] skip {v} funding: {e}", file=sys.stderr
            ),
        )

        pos_bases: set[str] = set()
        neg_bases: set[str] = set()
        for legs in per_venue.values():
            for leg in legs:
                if leg.rate_pct >= universe_min:
                    pos_bases.add(leg.base)
                if leg.rate_pct <= -universe_min:
                    neg_bases.add(leg.base)

        def _fetch_spot(venue: str) -> tuple[str, dict[str, tuple[bool, float]]]:
            checked: dict[str, tuple[bool, float]] = {}
            if not pos_bases:
                return venue, checked
            try:
                v = _get_venue(venue)
            except Exception:
                return venue, checked
            bulk: dict[str, float] = {}
            if hasattr(v, "get_all_spot_tickers"):
                try:
                    bulk = v.get_all_spot_tickers()
                except Exception:
                    pass
            legs = per_venue.get(venue, [])
            for leg in legs:
                if leg.base not in pos_bases:
                    continue
                if bulk:
                    px = float(bulk.get(leg.symbol, 0) or 0)
                    checked[leg.base] = (px > 0, px)
                else:
                    try:
                        px = v.get_ticker(leg.symbol)
                        checked[leg.base] = (px > 0, px)
                    except Exception:
                        checked[leg.base] = (False, 0.0)
            return venue, checked

        spot_by_venue = run_io_parallel(
            list(per_venue.keys()),
            _fetch_spot,
            max_workers=len(per_venue),
            swallow_errors=True,
        )

        def _fetch_borrow(venue: str) -> tuple[str, dict[str, dict[str, Any]]]:
            legs = per_venue.get(venue, [])
            venue_negs = sorted(
                {leg.base for leg in legs if leg.rate_pct <= -universe_min}
            )
            if not venue_negs:
                return venue, {}
            try:
                info = get_borrow_provider(venue).fetch_borrow_info(
                    venue_negs, max_workers=self.max_workers
                )
            except Exception as e:
                print(f"[unified_pool] skip {venue} borrow: {e}", file=sys.stderr)
                info = {}
            return venue, info

        borrow_by_venue = run_io_parallel(
            list(per_venue.keys()),
            _fetch_borrow,
            max_workers=len(per_venue),
            swallow_errors=True,
        )

        for venue, legs in per_venue.items():
            for leg in legs:
                if leg.base in pos_bases:
                    has_spot, px = spot_by_venue.get(venue, {}).get(
                        leg.base, (False, 0.0)
                    )
                    leg.has_spot = has_spot
                    leg.spot_price = px
                if leg.base in neg_bases:
                    info = borrow_by_venue.get(venue, {}).get(leg.base, {})
                    leg.borrowable = bool(info.get("borrowable"))
                    leg.borrow_daily_pct = float(info.get("daily_rate_pct", 0) or 0)
                    leg.borrow_annual_pct = float(info.get("annual_rate_pct", 0) or 0)
                    leg.max_borrow = str(info.get("max_borrow", "") or "")
                self.legs_by_base.setdefault(leg.base, []).append(leg)

        pairs: list[tuple[str, str]] = []
        for legs in self.legs_by_base.values():
            for leg in legs:
                pairs.append((leg.venue, leg.symbol))
        if pairs:
            self.fee_cache = prefetch_futures_fee_rates(pairs, workers=self.max_workers)

    def _annual_funding(self, rate_pct: float, interval_h: float) -> float:
        return abs(rate_pct) * (24.0 / interval_h) * 365.0

    def best_forward(self, base: str, entry: float) -> CrossRoute | None:
        legs = self.legs_by_base.get(base, [])
        futures_candidates = [l for l in legs if l.rate_pct >= entry]
        if not futures_candidates:
            return None
        futures_leg = max(futures_candidates, key=lambda l: l.rate_pct)
        spot_candidates = [l for l in legs if l.has_spot]
        if not spot_candidates:
            return None
        spot_leg = min(spot_candidates, key=lambda l: _spot_fee_pct(l.venue))
        f_fee = self._leg_futures_fee_pct(futures_leg)
        s_fee = _spot_fee_pct(spot_leg.venue)
        total_fee = f_fee + s_fee
        net = futures_leg.rate_pct - total_fee
        route = CrossRoute(
            base=base,
            direction="forward",
            futures_venue=futures_leg.venue,
            spot_venue=spot_leg.venue,
            funding_rate_pct=futures_leg.rate_pct,
            interval_h=futures_leg.interval_h,
            next_funding_ts=futures_leg.next_funding_ts,
            borrow_per_period_pct=0.0,
            futures_fee_pct=f_fee,
            spot_fee_pct=s_fee,
            total_fee_pct=total_fee,
            net_edge_pct=round(net, 4),
            annual_funding_pct=round(
                self._annual_funding(futures_leg.rate_pct, futures_leg.interval_h), 1
            ),
            same_venue=futures_leg.venue == spot_leg.venue,
            spot_price=spot_leg.spot_price,
        )
        return self._apply_transfer_cost(route)

    def best_reverse(self, base: str, entry: float) -> CrossRoute | None:
        legs = self.legs_by_base.get(base, [])
        futures_candidates = [l for l in legs if l.rate_pct <= -entry]
        if not futures_candidates:
            return None
        futures_leg = min(futures_candidates, key=lambda l: l.rate_pct)
        borrow_candidates = [
            l for l in legs if l.borrowable and _venue_reverse_executable(l.venue)
        ]
        if not borrow_candidates:
            return None

        def borrow_cost(leg: VenueLeg) -> float:
            return _borrow_interval_pct(
                leg.borrow_daily_pct,
                leg.borrow_annual_pct,
                futures_leg.interval_h,
                self.borrow_fallback_annual_pct,
            )

        margin_leg = min(borrow_candidates, key=borrow_cost)
        borrow_period = borrow_cost(margin_leg)
        f_fee = self._leg_futures_fee_pct(futures_leg)
        s_fee = _spot_fee_pct(margin_leg.venue)
        total_fee = f_fee + s_fee
        net = abs(futures_leg.rate_pct) - borrow_period - total_fee
        annual_borrow = margin_leg.borrow_annual_pct
        if annual_borrow <= 0 and margin_leg.borrow_daily_pct > 0:
            annual_borrow = margin_leg.borrow_daily_pct * 365.0
        elif annual_borrow <= 0:
            annual_borrow = self.borrow_fallback_annual_pct
        route = CrossRoute(
            base=base,
            direction="reverse",
            futures_venue=futures_leg.venue,
            spot_venue=margin_leg.venue,
            funding_rate_pct=futures_leg.rate_pct,
            interval_h=futures_leg.interval_h,
            next_funding_ts=futures_leg.next_funding_ts,
            borrow_per_period_pct=round(borrow_period, 4),
            futures_fee_pct=f_fee,
            spot_fee_pct=s_fee,
            total_fee_pct=total_fee,
            net_edge_pct=round(net, 4),
            annual_funding_pct=round(
                self._annual_funding(futures_leg.rate_pct, futures_leg.interval_h), 1
            ),
            same_venue=futures_leg.venue == margin_leg.venue,
            spot_price=margin_leg.spot_price,
            borrow_annual_pct=round(annual_borrow, 2),
        )
        return self._apply_transfer_cost(route)

    def scan_routes(
        self,
        entry: float = 0.05,
        universe_min: float = 0.03,
    ) -> dict[str, list[CrossRoute]]:
        if not self.legs_by_base:
            self.refresh(universe_min=universe_min)
        forward: list[CrossRoute] = []
        reverse: list[CrossRoute] = []
        for base in self.legs_by_base:
            f = self.best_forward(base, entry)
            if f:
                forward.append(f)
            r = self.best_reverse(base, entry)
            if r:
                reverse.append(r)
        forward.sort(key=lambda x: -x.net_edge_all_in_pct)
        reverse.sort(key=lambda x: -x.net_edge_all_in_pct)
        self._backfill_settle_ts(forward + reverse)
        return {"forward": forward, "reverse": reverse}

    def _backfill_settle_ts(self, routes: list[CrossRoute], cap: int = 30) -> None:
        """Backfill next settlement timestamps for routes missing them (e.g. Bitget bulk endpoint doesn't include this field)."""
        missing = [r for r in routes if not r.next_funding_ts][:cap]
        if not missing:
            return

        def _one(route: CrossRoute) -> tuple[str, int]:
            fp = get_funding_provider(route.futures_venue)
            snap = fp.fetch_current(f"{route.base}USDT")
            return f"{route.futures_venue}:{route.base}", int(
                snap.get("next_funding_ts", 0) or 0
            )

        ts_map = run_io_parallel(
            missing, _one, max_workers=self.max_workers, swallow_errors=True
        )
        for r in missing:
            r.next_funding_ts = ts_map.get(f"{r.futures_venue}:{r.base}", 0) or 0

    def funding_spread_matrix(self, base: str) -> list[dict[str, Any]]:
        """Same-asset cross-venue funding spread (for reference only, no spot hedge)."""
        legs = sorted(self.legs_by_base.get(base, []), key=lambda l: -l.rate_pct)
        out: list[dict[str, Any]] = []
        for i, hi in enumerate(legs):
            for lo in legs[i + 1 :]:
                out.append(
                    {
                        "base": base,
                        "high_venue": hi.venue,
                        "low_venue": lo.venue,
                        "high_rate_pct": hi.rate_pct,
                        "low_rate_pct": lo.rate_pct,
                        "spread_pct": round(hi.rate_pct - lo.rate_pct, 4),
                    }
                )
        out.sort(key=lambda x: -x["spread_pct"])
        return out

    # ── Pure Futures Spread ────────────────────────────────────────────

    def best_pure_futures_spread(
        self,
        base: str,
        min_spread_pct: float = 0.10,
    ) -> dict[str, Any] | None:
        """Best same-asset cross-venue pure perp funding spread (perp long + perp short).

        Go long at the venue with higher rate (receive funding), go short at the venue with lower rate (pay less funding).
        Both legs are perps, no spot/borrow/transfer, fully delta-neutral.

        Returns dict with spread details, or None if no profitable pair exists.
        """
        legs = self.legs_by_base.get(base, [])
        if len(legs) < 2:
            return None

        best: dict[str, Any] | None = None
        for i, long_leg in enumerate(legs):
            for short_leg in legs[i + 1 :]:
                # Convention: spread = higher_rate - lower_rate
                # long at lower rate (pays less), short at higher rate (receives more)
                if long_leg.rate_pct >= short_leg.rate_pct:
                    long_l, short_l = short_leg, long_leg
                else:
                    long_l, short_l = long_leg, short_leg

                spread = short_l.rate_pct - long_l.rate_pct
                if spread <= min_spread_pct:
                    continue

                _, _, fee = self._pair_open_fee_pct(long_l, short_l)
                net_edge = spread - fee
                if net_edge <= 0:
                    continue

                interval_h = max(long_l.interval_h, short_l.interval_h)
                annual = (net_edge / 100.0) * (24.0 / interval_h) * 365.0 * 100.0

                if best is None or net_edge > best["net_edge_pct"]:
                    best = {
                        "base": base,
                        "long_venue": long_l.venue,
                        "short_venue": short_l.venue,
                        "long_rate_pct": long_l.rate_pct,
                        "short_rate_pct": short_l.rate_pct,
                        "spread_pct": round(spread, 4),
                        "total_fee_pct": round(fee, 4),
                        "net_edge_pct": round(net_edge, 4),
                        "annual_pct": round(annual, 1),
                        "long_interval_h": long_l.interval_h,
                        "short_interval_h": short_l.interval_h,
                        "long_mark_price": long_l.mark_price,
                        "short_mark_price": short_l.mark_price,
                        "next_funding_ts": max(
                            long_l.next_funding_ts, short_l.next_funding_ts
                        ),
                    }
        return best

    def funding_spread_matrix_pure(self, base: str) -> list[dict[str, Any]]:
        """Build pure perp funding spread matrix (all venue pairs, including net edge).

        Returns list sorted by net_edge descending. Each item has the same
        shape as best_pure_futures_spread().
        """
        legs = self.legs_by_base.get(base, [])
        if len(legs) < 2:
            return []

        pairs: list[dict[str, Any]] = []
        for i, long_leg in enumerate(legs):
            for short_leg in legs[i + 1 :]:
                if long_leg.rate_pct >= short_leg.rate_pct:
                    long_l, short_l = short_leg, long_leg
                else:
                    long_l, short_l = long_leg, short_leg

                spread = short_l.rate_pct - long_l.rate_pct
                _, _, fee = self._pair_open_fee_pct(long_l, short_l)
                net_edge = spread - fee

                if net_edge <= 0:
                    continue

                interval_h = max(long_l.interval_h, short_l.interval_h)
                annual = (net_edge / 100.0) * (24.0 / interval_h) * 365.0 * 100.0

                pairs.append(
                    {
                        "base": base,
                        "long_venue": long_l.venue,
                        "short_venue": short_l.venue,
                        "long_rate_pct": long_l.rate_pct,
                        "short_rate_pct": short_l.rate_pct,
                        "spread_pct": round(spread, 4),
                        "total_fee_pct": round(fee, 4),
                        "net_edge_pct": round(net_edge, 4),
                        "annual_pct": round(annual, 1),
                        "long_interval_h": long_l.interval_h,
                        "short_interval_h": short_l.interval_h,
                        "long_mark_price": long_l.mark_price,
                        "short_mark_price": short_l.mark_price,
                        "next_funding_ts": max(
                            long_leg.next_funding_ts, short_leg.next_funding_ts
                        ),
                    }
                )

        return sorted(pairs, key=lambda x: -x["net_edge_pct"])

    def scan_pure_futures_routes(
        self,
        min_spread_pct: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Scan all assets for best pure perp pairs."""
        if not self.legs_by_base:
            self.refresh()
        results: list[dict[str, Any]] = []
        for base in self.legs_by_base:
            best = self.best_pure_futures_spread(base, min_spread_pct=min_spread_pct)
            if best:
                results.append(best)
        results.sort(key=lambda x: -x["net_edge_pct"])
        return results

    def _best_single_net(
        self, legs: list[VenueLeg], direction: Direction, entry: float
    ) -> dict[str, Any] | None:
        """Best net edge for completing both legs on the same venue."""
        best: dict[str, Any] | None = None
        for leg in legs:
            fee = _spot_fee_pct(leg.venue) + self._leg_futures_fee_pct(leg)
            if direction == "forward":
                if leg.rate_pct < entry or not leg.has_spot:
                    continue
                net = leg.rate_pct - fee
            else:
                if leg.rate_pct > -entry or not leg.borrowable:
                    continue
                borrow = _borrow_interval_pct(
                    leg.borrow_daily_pct,
                    leg.borrow_annual_pct,
                    leg.interval_h,
                    self.borrow_fallback_annual_pct,
                )
                net = abs(leg.rate_pct) - borrow - fee
            if best is None or net > best["net_edge_pct"]:
                best = {"venue": leg.venue, "net_edge_pct": round(net, 4)}
        return best

    def compare_single_vs_cross(self, entry: float = 0.05) -> list[dict[str, Any]]:
        """Compare same-venue vs cross-venue best net edge."""
        rows: list[dict[str, Any]] = []
        for base, legs in self.legs_by_base.items():
            for direction, pick_fn in (
                ("forward", self.best_forward),
                ("reverse", self.best_reverse),
            ):
                cross = pick_fn(base, entry)
                if not cross:
                    continue
                single_best = self._best_single_net(legs, direction, entry)
                if not single_best:
                    continue
                rows.append(
                    {
                        "base": base,
                        "direction": direction,
                        "single_venue": single_best["venue"],
                        "single_net_pct": single_best["net_edge_pct"],
                        "cross_net_pct": cross.net_edge_pct,
                        "improvement_pct": round(
                            cross.net_edge_pct - single_best["net_edge_pct"], 4
                        ),
                        "cross_futures_venue": cross.futures_venue,
                        "cross_spot_venue": cross.spot_venue,
                        "cross_same_venue": cross.same_venue,
                    }
                )
        rows.sort(key=lambda x: -abs(x["improvement_pct"]))
        return rows
