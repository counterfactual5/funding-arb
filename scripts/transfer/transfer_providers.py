#!/usr/bin/env python3
"""各交易所充提 / 链信息抽象层。"""
from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from transfer.chain_aliases import native_chain, to_canonical


@dataclass
class ChainRoute:
    """单所、单链的充提能力。"""

    venue: str
    coin: str
    native_chain: str
    canonical: str | None
    withdraw_fee: float
    withdraw_fee_pct: float  # 百分比手续费（Bybit 等）
    min_withdraw: float
    min_deposit: float
    withdraw_enabled: bool
    deposit_enabled: bool
    label: str = ""

    @property
    def effective_withdraw_fee(self) -> float:
        return self.withdraw_fee  # 百分比费由 router 按 amount 另算


@dataclass
class DepositAddress:
    venue: str
    coin: str
    chain: str
    address: str
    tag: str = ""
    url: str = ""


@dataclass
class WithdrawResult:
    ok: bool
    order_id: str = ""
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class TransferProvider:
    venue_id: str = "unknown"

    def fetch_chain_routes(self, coin: str) -> list[ChainRoute]:
        raise NotImplementedError

    def get_deposit_address(self, coin: str, native_chain_name: str) -> DepositAddress:
        raise NotImplementedError

    def get_withdrawable_balance(self, coin: str) -> float:
        raise NotImplementedError

    def prepare_for_withdraw(self, coin: str, amount: float) -> list[str]:
        """将资金归集到可提现账户（如 futures→spot）。返回执行步骤描述。"""
        return []

    def withdraw(
        self,
        coin: str,
        amount: float,
        native_chain_name: str,
        address: str,
        tag: str = "",
    ) -> WithdrawResult:
        raise NotImplementedError

    def fetch_deposit_records(
        self,
        coin: str,
        since_ms: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """最近充值记录（各所子类实现）。"""
        return []


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


class BitgetTransferProvider(TransferProvider):
    venue_id = "bitget"

    def fetch_chain_routes(self, coin: str) -> list[ChainRoute]:
        from venues.http_util import http_get_json

        coin_u = coin.upper()
        url = f"https://api.bitget.com/api/v2/spot/public/coins?coin={coin_u}"
        data = http_get_json(url, timeout=20)
        rows = data.get("data") or []
        if not rows:
            return []
        out: list[ChainRoute] = []
        for ch in rows[0].get("chains") or []:
            native = str(ch.get("chain", ""))
            canon = to_canonical(native)
            out.append(
                ChainRoute(
                    venue=self.venue_id,
                    coin=coin_u,
                    native_chain=native,
                    canonical=canon,
                    withdraw_fee=_safe_float(ch.get("withdrawFee")),
                    withdraw_fee_pct=_safe_float(ch.get("extraWithdrawFee")),
                    min_withdraw=_safe_float(ch.get("minWithdrawAmount")),
                    min_deposit=_safe_float(ch.get("minDepositAmount")),
                    withdraw_enabled=str(ch.get("withdrawable", "")).lower() == "true",
                    deposit_enabled=str(ch.get("rechargeable", "")).lower() == "true",
                    label=native,
                )
            )
        return out

    def get_deposit_address(self, coin: str, native_chain_name: str) -> DepositAddress:
        from venues.bitget import _api_call

        data = _api_call(
            "GET",
            "/api/v2/spot/wallet/deposit-address",
            params={"coin": coin.upper(), "chain": native_chain_name},
        )
        row = data.get("data") or {}
        return DepositAddress(
            venue=self.venue_id,
            coin=coin.upper(),
            chain=str(row.get("chain", native_chain_name)),
            address=str(row.get("address", "")),
            tag=str(row.get("tag") or ""),
            url=str(row.get("url") or ""),
        )

    def get_withdrawable_balance(self, coin: str) -> float:
        from venues.bitget import BitgetSpotVenue

        v = BitgetSpotVenue()
        bals = v.fetch_usdt_account_balances() if coin.upper() == "USDT" else {}
        if coin.upper() == "USDT":
            return _safe_float(bals.get("spot"))
        data = v.fetch_balances([coin.upper()])
        return _safe_float(data.get(coin.upper()))

    def prepare_for_withdraw(self, coin: str, amount: float) -> list[str]:
        from venues.bitget import BitgetSpotVenue

        v = BitgetSpotVenue()
        steps: list[str] = []
        if coin.upper() != "USDT":
            return steps
        bals = v.fetch_usdt_account_balances()
        spot = _safe_float(bals.get("spot"))
        fut = _safe_float(bals.get("futures"))
        if spot >= amount:
            return steps
        need = amount - spot
        if fut <= 0:
            return steps
        xfer = min(fut, need) * 1.01
        if hasattr(v, "transfer_asset") and v.transfer_asset("USDT", xfer, "futures", "spot"):
            steps.append(f"bitget: futures→spot USDT {xfer:.4f}")
        else:
            steps.append(f"bitget: futures→spot USDT {xfer:.4f} FAILED")
        return steps

    def withdraw(
        self,
        coin: str,
        amount: float,
        native_chain_name: str,
        address: str,
        tag: str = "",
    ) -> WithdrawResult:
        from venues.bitget import _api_call

        body: dict[str, Any] = {
            "coin": coin.upper(),
            "chain": native_chain_name,
            "address": address,
            "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
            "clientOid": f"w{int(time.time())}{uuid.uuid4().hex[:8]}",
        }
        if tag:
            body["tag"] = tag
        try:
            data = _api_call("POST", "/api/v2/spot/wallet/withdrawal", body=body)
            if str(data.get("code")) == "00000":
                oid = str((data.get("data") or {}).get("orderId", ""))
                return WithdrawResult(ok=True, order_id=oid, message="submitted", raw=data)
            return WithdrawResult(ok=False, message=str(data.get("msg", data)), raw=data)
        except Exception as e:
            return WithdrawResult(ok=False, message=str(e))

    def fetch_deposit_records(
        self,
        coin: str,
        since_ms: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from venues.bitget import _api_call

        params: dict[str, Any] = {"coin": coin.upper(), "limit": str(limit)}
        if since_ms > 0:
            params["startTime"] = str(since_ms)
        try:
            data = _api_call("GET", "/api/v2/spot/wallet/deposit-records", params=params)
            rows = data.get("data") or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            ts = int(row.get("cTime", 0) or 0)
            if since_ms > 0 and ts < since_ms:
                continue
            out.append(
                {
                    "venue": self.venue_id,
                    "coin": coin.upper(),
                    "amount": _safe_float(row.get("size")),
                    "chain": str(row.get("chain", "")),
                    "status": str(row.get("status", "")),
                    "tx_id": str(row.get("tradeId", "") or row.get("txId", "")),
                    "ts_ms": ts,
                }
            )
        return out


class BybitTransferProvider(TransferProvider):
    venue_id = "bybit"

    def fetch_chain_routes(self, coin: str) -> list[ChainRoute]:
        from venues.bybit import _api_call

        data = _api_call("GET", "/v5/asset/coin/query-info", params={"coin": coin.upper()})
        rows = data.get("result", {}).get("rows") or []
        if not rows:
            return []
        out: list[ChainRoute] = []
        for ch in rows[0].get("chains") or []:
            native = str(ch.get("chain", ""))
            canon = to_canonical(native) or to_canonical(str(ch.get("chainType", "")))
            out.append(
                ChainRoute(
                    venue=self.venue_id,
                    coin=coin.upper(),
                    native_chain=native,
                    canonical=canon,
                    withdraw_fee=_safe_float(ch.get("withdrawFee")),
                    withdraw_fee_pct=_safe_float(ch.get("withdrawPercentageFee")),
                    min_withdraw=_safe_float(ch.get("withdrawMin")),
                    min_deposit=_safe_float(ch.get("depositMin")),
                    withdraw_enabled=str(ch.get("chainWithdraw")) == "1",
                    deposit_enabled=str(ch.get("chainDeposit")) == "1",
                    label=str(ch.get("chainType", native)),
                )
            )
        return out

    def get_deposit_address(self, coin: str, native_chain_name: str) -> DepositAddress:
        from venues.bybit import _api_call

        data = _api_call(
            "GET",
            "/v5/asset/deposit/query-address",
            params={"coin": coin.upper(), "chainType": native_chain_name},
        )
        chains = (data.get("result") or {}).get("chains") or []
        if isinstance(chains, dict):
            chains = [chains]
        row = chains[0] if chains else {}
        return DepositAddress(
            venue=self.venue_id,
            coin=coin.upper(),
            chain=str(row.get("chain", native_chain_name)),
            address=str(row.get("addressDeposit", "")),
            tag=str(row.get("tagDeposit") or ""),
        )

    def get_withdrawable_balance(self, coin: str) -> float:
        from venues.bybit import _api_call

        try:
            data = _api_call(
                "GET",
                "/v5/asset/withdraw/withdrawable-amount",
                params={"coin": coin.upper()},
            )
            w = (data.get("result") or {}).get("withdrawableAmount") or {}
            for key in ("UTA", "FUND", "SPOT"):
                if key in w:
                    amt = _safe_float(w[key].get("withdrawableAmount"))
                    if amt > 0:
                        return amt
        except Exception:
            pass
        from venues.bybit import BybitSpotVenue

        bals = BybitSpotVenue().fetch_balances([coin.upper()])
        return _safe_float(bals.get(coin.upper()))

    def prepare_for_withdraw(self, coin: str, amount: float) -> list[str]:
        # Bybit UTA 通常可直接提；若不足则尝试 UNIFIED 内划转（多数情况不需要）
        return []

    def withdraw(
        self,
        coin: str,
        amount: float,
        native_chain_name: str,
        address: str,
        tag: str = "",
    ) -> WithdrawResult:
        from venues.bybit import _api_call

        body: dict[str, Any] = {
            "coin": coin.upper(),
            "chain": native_chain_name,
            "address": address,
            "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
            "timestamp": int(time.time() * 1000),
            "forceChain": 0,
            "accountType": "UTA",
        }
        if tag:
            body["tag"] = tag
        try:
            data = _api_call("POST", "/v5/asset/withdraw/create", body=body)
            if int(data.get("retCode", -1)) == 0:
                oid = str((data.get("result") or {}).get("id", ""))
                return WithdrawResult(ok=True, order_id=oid, message="submitted", raw=data)
            return WithdrawResult(ok=False, message=str(data.get("retMsg", data)), raw=data)
        except Exception as e:
            return WithdrawResult(ok=False, message=str(e))

    def fetch_deposit_records(
        self,
        coin: str,
        since_ms: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from venues.bybit import _api_call

        params: dict[str, Any] = {"coin": coin.upper(), "limit": limit}
        if since_ms > 0:
            params["startTime"] = since_ms
        try:
            data = _api_call("GET", "/v5/asset/deposit/query-record", params=params)
            rows = (data.get("result") or {}).get("rows") or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            ts = int(row.get("successAt", 0) or row.get("createTime", 0) or 0)
            if since_ms > 0 and ts < since_ms:
                continue
            out.append(
                {
                    "venue": self.venue_id,
                    "coin": coin.upper(),
                    "amount": _safe_float(row.get("amount")),
                    "chain": str(row.get("chain", "")),
                    "status": str(row.get("status", "")),
                    "tx_id": str(row.get("txID", "")),
                    "ts_ms": ts,
                }
            )
        return out


class OkxTransferProvider(TransferProvider):
    venue_id = "okx"

    def fetch_chain_routes(self, coin: str) -> list[ChainRoute]:
        from venues.okx import _api_call

        try:
            data = _api_call("GET", "/api/v5/asset/currencies", params={"ccy": coin.upper()})
        except Exception as e:
            print(f"[okx] fetch currencies failed: {e}", file=sys.stderr)
            return []
        out: list[ChainRoute] = []
        for ch in data.get("data") or []:
            native = str(ch.get("chain", ""))
            canon = to_canonical(native)
            fee = _safe_float(ch.get("minFee"))
            if fee <= 0:
                fee = _safe_float(ch.get("maxFee"))
            out.append(
                ChainRoute(
                    venue=self.venue_id,
                    coin=coin.upper(),
                    native_chain=native,
                    canonical=canon,
                    withdraw_fee=fee,
                    withdraw_fee_pct=0.0,
                    min_withdraw=_safe_float(ch.get("minWd")),
                    min_deposit=_safe_float(ch.get("minDep")),
                    withdraw_enabled=str(ch.get("canWd", "")).lower() == "true",
                    deposit_enabled=str(ch.get("canDep", "")).lower() == "true",
                    label=native,
                )
            )
        return out

    def get_deposit_address(self, coin: str, native_chain_name: str) -> DepositAddress:
        from venues.okx import _api_call

        data = _api_call(
            "GET",
            "/api/v5/asset/deposit-address",
            params={"ccy": coin.upper(), "chain": native_chain_name},
        )
        rows = data.get("data") or []
        row = rows[0] if rows else {}
        return DepositAddress(
            venue=self.venue_id,
            coin=coin.upper(),
            chain=str(row.get("chain", native_chain_name)),
            address=str(row.get("addr", "")),
            tag=str(row.get("tag") or ""),
        )

    def get_withdrawable_balance(self, coin: str) -> float:
        from venues.okx import _api_call

        try:
            data = _api_call("GET", "/api/v5/account/balance", params={"ccy": coin.upper()})
            for row in (data.get("data") or [{}])[0].get("details") or []:
                if str(row.get("ccy", "")).upper() == coin.upper():
                    return _safe_float(row.get("availBal"))
        except Exception:
            pass
        from venues.okx import OkxSpotVenue

        bals = OkxSpotVenue().fetch_balances([coin.upper()])
        return _safe_float(bals.get(coin.upper()))

    def prepare_for_withdraw(self, coin: str, amount: float) -> list[str]:
        from venues.okx import _api_call

        steps: list[str] = []
        try:
            bal = _api_call("GET", "/api/v5/account/balance", params={"ccy": coin.upper()})
            details = (bal.get("data") or [{}])[0].get("details") or []
            trading_avail = 0.0
            for d in details:
                if str(d.get("ccy", "")).upper() == coin.upper():
                    trading_avail = _safe_float(d.get("availBal"))
                    break
            if trading_avail >= amount:
                return steps
            # 交易账户 -> 资金账户（18=资金, 6=交易 等，OKX 内部划转）
            xfer = amount - trading_avail + 0.01
            _api_call(
                "POST",
                "/api/v5/asset/transfer",
                body={
                    "from": "18",
                    "to": "18",
                    "type": "0",
                    "ccy": coin.upper(),
                    "amt": f"{xfer:.8f}".rstrip("0").rstrip("."),
                },
            )
            steps.append(f"okx: internal transfer {coin} {xfer:.4f} for withdraw")
        except Exception as e:
            steps.append(f"okx: prepare_for_withdraw skipped ({e})")
        return steps

    def withdraw(
        self,
        coin: str,
        amount: float,
        native_chain_name: str,
        address: str,
        tag: str = "",
    ) -> WithdrawResult:
        from venues.okx import _api_call

        body: dict[str, Any] = {
            "ccy": coin.upper(),
            "amt": f"{amount:.8f}".rstrip("0").rstrip("."),
            "dest": "4",  # on-chain
            "toAddr": address,
            "chain": native_chain_name,
            "fee": "",  # 让交易所自动计算
        }
        if tag:
            body["tag"] = tag
        try:
            data = _api_call("POST", "/api/v5/asset/withdrawal", body=body)
            if str(data.get("code")) == "0":
                oid = str((data.get("data") or [{}])[0].get("wdId", ""))
                return WithdrawResult(ok=True, order_id=oid, message="submitted", raw=data)
            return WithdrawResult(ok=False, message=str(data.get("msg", data)), raw=data)
        except Exception as e:
            return WithdrawResult(ok=False, message=str(e))

    def fetch_deposit_records(
        self,
        coin: str,
        since_ms: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from venues.okx import _api_call

        params: dict[str, Any] = {"ccy": coin.upper(), "limit": str(limit)}
        if since_ms > 0:
            params["after"] = str(since_ms)
        try:
            data = _api_call("GET", "/api/v5/asset/deposit-history", params=params)
            rows = data.get("data") or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            ts = int(row.get("ts", 0) or 0)
            if since_ms > 0 and ts < since_ms:
                continue
            out.append(
                {
                    "venue": self.venue_id,
                    "coin": coin.upper(),
                    "amount": _safe_float(row.get("amt")),
                    "chain": str(row.get("chain", "")),
                    "status": str(row.get("state", "")),
                    "tx_id": str(row.get("txId", "")),
                    "ts_ms": ts,
                }
            )
        return out


class BinanceTransferProvider(TransferProvider):
    venue_id = "binance"

    def fetch_chain_routes(self, coin: str) -> list[ChainRoute]:
        from venues.binance import _api_call

        data = _api_call("GET", "/sapi/v1/capital/config/getall", signed=True)
        out: list[ChainRoute] = []
        for row in data if isinstance(data, list) else []:
            if str(row.get("coin", "")).upper() != coin.upper():
                continue
            for ch in row.get("networkList") or []:
                native = str(ch.get("network", ""))
                canon = to_canonical(native)
                out.append(
                    ChainRoute(
                        venue=self.venue_id,
                        coin=coin.upper(),
                        native_chain=native,
                        canonical=canon,
                        withdraw_fee=_safe_float(ch.get("withdrawFee")),
                        withdraw_fee_pct=0.0,
                        min_withdraw=_safe_float(ch.get("withdrawMin")),
                        min_deposit=_safe_float(ch.get("depositDust")),
                        withdraw_enabled=bool(ch.get("withdrawEnable")),
                        deposit_enabled=bool(ch.get("depositEnable")),
                        label=native,
                    )
                )
        return out

    def get_deposit_address(self, coin: str, native_chain_name: str) -> DepositAddress:
        from venues.binance import _api_call

        data = _api_call(
            "GET",
            "/sapi/v1/capital/deposit/address",
            params={"coin": coin.upper(), "network": native_chain_name},
            signed=True,
        )
        return DepositAddress(
            venue=self.venue_id,
            coin=coin.upper(),
            chain=native_chain_name,
            address=str(data.get("address", "")),
            tag=str(data.get("tag") or ""),
        )

    def get_withdrawable_balance(self, coin: str) -> float:
        from venues.binance import BinanceSpotVenue

        v = BinanceSpotVenue()
        if coin.upper() == "USDT":
            bals = v.fetch_usdt_account_balances()
            return _safe_float(bals.get("spot"))
        return _safe_float(v.fetch_balances([coin.upper()]).get(coin.upper()))

    def prepare_for_withdraw(self, coin: str, amount: float) -> list[str]:
        from venues.binance import BinanceSpotVenue

        steps: list[str] = []
        if coin.upper() != "USDT":
            return steps
        v = BinanceSpotVenue()
        bals = v.fetch_usdt_account_balances()
        spot = _safe_float(bals.get("spot"))
        fut = _safe_float(bals.get("futures"))
        if spot >= amount or fut <= 0:
            return steps
        need = amount - spot
        xfer = min(fut, need) * 1.01
        if v.transfer_asset("USDT", xfer, "futures", "spot"):
            steps.append(f"binance: futures→spot USDT {xfer:.4f}")
        return steps

    def withdraw(
        self,
        coin: str,
        amount: float,
        native_chain_name: str,
        address: str,
        tag: str = "",
    ) -> WithdrawResult:
        from venues.binance import _api_call

        params: dict[str, Any] = {
            "coin": coin.upper(),
            "network": native_chain_name,
            "address": address,
            "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
        }
        if tag:
            params["addressTag"] = tag
        try:
            data = _api_call("POST", "/sapi/v1/capital/withdraw/apply", params=params, signed=True)
            oid = str(data.get("id", ""))
            return WithdrawResult(ok=True, order_id=oid, message="submitted", raw=data)
        except Exception as e:
            return WithdrawResult(ok=False, message=str(e))


_PROVIDERS: dict[str, TransferProvider] = {
    "bitget": BitgetTransferProvider(),
    "bybit": BybitTransferProvider(),
    "okx": OkxTransferProvider(),
    "binance": BinanceTransferProvider(),
}


def get_transfer_provider(venue: str) -> TransferProvider:
    v = str(venue or "").strip().lower()
    p = _PROVIDERS.get(v)
    if p is None:
        raise ValueError(f"不支持的 transfer venue={v!r}，可选: {', '.join(sorted(_PROVIDERS))}")
    return p


def resolve_native_chain(canon: str, venue: str) -> str | None:
    return native_chain(canon, venue)


_DEPOSIT_OK_STATUS = frozenset(
    {
        "success",
        "1",
        "2",
        "completed",
        "complete",
        "confirmed",
    }
)


def _deposit_confirmed(row: dict[str, Any]) -> bool:
    st = str(row.get("status", "")).lower()
    if st in _DEPOSIT_OK_STATUS:
        return True
    if st.isdigit() and int(st) >= 1:
        return True
    return row.get("amount", 0) > 0 and not st


def poll_deposit_until(
    venue: str,
    coin: str,
    expected_amount: float,
    since_ms: int,
    *,
    timeout_s: int = 600,
    poll_interval_s: int = 15,
    tolerance: float = 0.02,
) -> tuple[bool, list[dict[str, Any]]]:
    """轮询充值到账。返回 (matched, records)。"""
    provider = get_transfer_provider(venue)
    deadline = time.time() + timeout_s
    seen: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    min_amt = max(0.0, expected_amount * (1.0 - tolerance))
    while time.time() < deadline:
        rows = provider.fetch_deposit_records(coin, since_ms=since_ms)
        for row in rows:
            rid = str(row.get("tx_id") or row.get("ts_ms") or "")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            seen.append(row)
            if not _deposit_confirmed(row):
                continue
            if _safe_float(row.get("amount")) >= min_amt:
                return True, seen
        time.sleep(poll_interval_s)
    return False, seen
