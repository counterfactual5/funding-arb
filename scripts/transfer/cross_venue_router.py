#!/usr/bin/env python3
"""Cross-venue fund routing — match common chains, pick lowest withdrawal fee, generate execution plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from transfer.chain_aliases import common_canonicals, native_chain
from transfer.transfer_providers import (
    DepositAddress,
    WithdrawResult,
    get_transfer_provider,
)


@dataclass
class CrossTransferRoute:
    """A viable cross-venue transfer route."""

    canonical: str
    from_venue: str
    to_venue: str
    coin: str
    amount: float
    from_chain: str
    to_chain: str
    withdraw_fee: float
    fee_pct: float
    total_fee: float
    net_est: float
    min_withdraw: float
    min_deposit: float
    from_label: str
    to_label: str
    deposit_address: str = ""
    deposit_tag: str = ""
    viable: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "from_venue": self.from_venue,
            "to_venue": self.to_venue,
            "coin": self.coin,
            "amount": self.amount,
            "from_chain": self.from_chain,
            "to_chain": self.to_chain,
            "withdraw_fee": self.withdraw_fee,
            "fee_pct": self.fee_pct,
            "total_fee": self.total_fee,
            "net_est": self.net_est,
            "min_withdraw": self.min_withdraw,
            "min_deposit": self.min_deposit,
            "from_label": self.from_label,
            "to_label": self.to_label,
            "deposit_address": self.deposit_address,
            "deposit_tag": self.deposit_tag,
            "viable": self.viable,
            "note": self.note,
        }


@dataclass
class TransferPlan:
    route: CrossTransferRoute
    prep_steps: list[str] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = self.route.to_dict()
        d["prep_steps"] = self.prep_steps
        d["dry_run"] = self.dry_run
        return d


def _index_routes(routes: list, venue: str) -> dict[str, Any]:
    """canonical -> ChainRoute (from one venue)."""
    out: dict[str, Any] = {}
    for r in routes:
        if r.canonical and r.canonical not in out:
            out[r.canonical] = r
    return out


def _calc_total_fee(amount: float, fixed_fee: float, pct_fee: float) -> float:
    return fixed_fee + amount * pct_fee


def find_routes(
    from_venue: str,
    to_venue: str,
    coin: str,
    amount: float,
    *,
    fetch_deposit_address: bool = True,
) -> list[CrossTransferRoute]:
    """List all common-chain routes from→to, sorted by total_fee ascending."""
    src = get_transfer_provider(from_venue)
    dst = get_transfer_provider(to_venue)
    src_routes = _index_routes(src.fetch_chain_routes(coin), from_venue)
    dst_routes = _index_routes(dst.fetch_chain_routes(coin), to_venue)

    canon_list = common_canonicals(from_venue, to_venue)
    # Also try intersecting actual fetch results
    for c in set(src_routes) & set(dst_routes):
        if c not in canon_list:
            canon_list.append(c)

    results: list[CrossTransferRoute] = []
    for canon in canon_list:
        sr = src_routes.get(canon)
        dr = dst_routes.get(canon)
        if sr is None:
            fc = native_chain(canon, from_venue)
            if fc:
                for r in src.fetch_chain_routes(coin):
                    if r.native_chain == fc:
                        sr = r
                        break
        if dr is None:
            tc = native_chain(canon, to_venue)
            if tc:
                for r in dst.fetch_chain_routes(coin):
                    if r.native_chain == tc:
                        dr = r
                        break
        if sr is None or dr is None:
            continue
        if not sr.withdraw_enabled or not dr.deposit_enabled:
            note = []
            if not sr.withdraw_enabled:
                note.append(f"{from_venue} withdraw disabled")
            if not dr.deposit_enabled:
                note.append(f"{to_venue} deposit disabled")
            results.append(
                CrossTransferRoute(
                    canonical=canon,
                    from_venue=from_venue,
                    to_venue=to_venue,
                    coin=coin.upper(),
                    amount=amount,
                    from_chain=sr.native_chain,
                    to_chain=dr.native_chain,
                    withdraw_fee=sr.withdraw_fee,
                    fee_pct=sr.withdraw_fee_pct,
                    total_fee=0,
                    net_est=0,
                    min_withdraw=sr.min_withdraw,
                    min_deposit=dr.min_deposit,
                    from_label=sr.label,
                    to_label=dr.label,
                    viable=False,
                    note="; ".join(note),
                )
            )
            continue

        total_fee = _calc_total_fee(amount, sr.withdraw_fee, sr.withdraw_fee_pct)
        net = amount - total_fee
        viable = amount >= sr.min_withdraw and net >= max(dr.min_deposit, 0)
        note = ""
        if amount < sr.min_withdraw:
            note = f"amount {amount} < min_withdraw {sr.min_withdraw}"
            viable = False
        elif net < dr.min_deposit:
            note = f"net {net:.4f} < min_deposit {dr.min_deposit}"
            viable = False

        addr = ""
        tag = ""
        if fetch_deposit_address and viable:
            try:
                dep = dst.get_deposit_address(coin, dr.native_chain)
                addr = dep.address
                tag = dep.tag
                if not addr:
                    viable = False
                    note = "empty deposit address"
            except Exception as e:
                viable = False
                note = f"deposit address error: {e}"

        results.append(
            CrossTransferRoute(
                canonical=canon,
                from_venue=from_venue,
                to_venue=to_venue,
                coin=coin.upper(),
                amount=amount,
                from_chain=sr.native_chain,
                to_chain=dr.native_chain,
                withdraw_fee=sr.withdraw_fee,
                fee_pct=sr.withdraw_fee_pct,
                total_fee=round(total_fee, 6),
                net_est=round(net, 6),
                min_withdraw=sr.min_withdraw,
                min_deposit=dr.min_deposit,
                from_label=sr.label,
                to_label=dr.label,
                deposit_address=addr,
                deposit_tag=tag,
                viable=viable,
                note=note,
            )
        )

    results.sort(key=lambda x: (not x.viable, x.total_fee))
    return results


def estimate_transfer_fee(
    from_venue: str,
    to_venue: str,
    coin: str,
    amount: float,
) -> tuple[float, float, str]:
    """Estimate cross-venue transfer cost. Returns (fee_usdt, fee_pct, canonical_chain).

    fee_pct is in the same unit as the pool's net_edge: percentage points of notional (0.1 = 0.1%).
    """
    fv, tv = from_venue.lower(), to_venue.lower()
    if fv == tv or amount <= 0:
        return 0.0, 0.0, ""
    route = best_route(fv, tv, coin, amount, fetch_deposit_address=False)
    if route is None:
        return 0.0, 0.0, ""
    fee_usdt = route.total_fee if route.viable else route.withdraw_fee
    fee_pct = (fee_usdt / amount) * 100.0 if amount > 0 else 0.0
    return round(fee_usdt, 6), round(fee_pct, 4), route.canonical or ""


def best_route(
    from_venue: str,
    to_venue: str,
    coin: str,
    amount: float,
    *,
    fetch_deposit_address: bool = True,
) -> CrossTransferRoute | None:
    routes = find_routes(
        from_venue, to_venue, coin, amount, fetch_deposit_address=fetch_deposit_address
    )
    for r in routes:
        if r.viable:
            return r
    return routes[0] if routes else None


def build_plan(
    from_venue: str,
    to_venue: str,
    coin: str,
    amount: float,
    *,
    canonical: str | None = None,
    dry_run: bool = True,
) -> TransferPlan | None:
    routes = find_routes(from_venue, to_venue, coin, amount)
    if not routes:
        return None
    route: CrossTransferRoute | None = None
    if canonical:
        for r in routes:
            if r.canonical == canonical and r.viable:
                route = r
                break
    if route is None:
        route = next((r for r in routes if r.viable), None)
    if route is None:
        route = routes[0]
    if not route.viable:
        return TransferPlan(route=route, dry_run=dry_run)

    src = get_transfer_provider(from_venue)
    prep = src.prepare_for_withdraw(coin, amount) if not dry_run else []
    return TransferPlan(route=route, prep_steps=prep, dry_run=dry_run)


def execute_plan(plan: TransferPlan) -> tuple[list[str], WithdrawResult | None]:
    """Execute transfer plan. Returns (step logs, withdrawal result)."""
    if plan.dry_run:
        return ["dry_run: skipped execution"], None

    route = plan.route
    if not route.viable:
        return [f"aborted: route not viable ({route.note})"], None

    logs: list[str] = list(plan.prep_steps)
    src = get_transfer_provider(route.from_venue)

    prep = src.prepare_for_withdraw(route.coin, route.amount)
    logs.extend(prep)

    # Refresh deposit address
    dst = get_transfer_provider(route.to_venue)
    dep: DepositAddress = dst.get_deposit_address(route.coin, route.to_chain)
    if not dep.address:
        return logs + ["aborted: empty deposit address"], WithdrawResult(
            ok=False, message="empty deposit address"
        )

    bal = src.get_withdrawable_balance(route.coin)
    if bal < route.amount:
        return logs + [
            f"aborted: balance {bal:.4f} < amount {route.amount:.4f}"
        ], WithdrawResult(ok=False, message="insufficient balance")

    result = src.withdraw(
        route.coin,
        route.amount,
        route.from_chain,
        dep.address,
        dep.tag,
    )
    logs.append(
        f"withdraw {route.amount} {route.coin} via {route.from_chain} "
        f"({route.from_venue}→{route.to_venue}) order={result.order_id or 'n/a'}"
    )
    if not result.ok:
        logs.append(f"withdraw FAILED: {result.message}")
    return logs, result
