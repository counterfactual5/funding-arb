#!/usr/bin/env python3
"""dYdX v4 perp-DEX venue adapter for funding-arb pure futures execution.

Implements the CexVenue interface subset used by pure_futures_executor /
pure_futures_watcher. Read path (prices, meta, orderbook, funding) uses
the v4-client SDK IndexerClient (public REST) directly. Read-side
credentials are never required — markets, funding, and orderbook are
public. Write path (balances, positions, orders) needs a wallet: live
orders are gated behind an explicit `DYDX_ENABLE_LIVE=1` opt-in so the
adapter stays safe-by-default in dry-run / CI flows.

Live order submission uses the SDK Market + OrderId + place_order flow:
quantums/subticks/clob_pair_id conversion is handled by the SDK's Market
class. The builder + protobuf signing is wrapped in _submit_market_order.
A 0.5% slippage buffer is applied for IOC market orders.

Symbology: on-chain `BTC-USD` <-> CEX pair `BTCUSDT`. Mark price is the
indexer `oraclePrice` (independent of the off-chain CEX index — see
scripts/core/cross_interval_funding.py for the basis-blend model).

Credentials (live, all required; opt-in via DYDX_ENABLE_LIVE=1):
    DYDX_MNEMONIC           — 24-word BIP-39 mnemonic
    DYDX_ADDRESS            — dYdX bech32 address (dydx1...)
    DYDX_SUBACCOUNT_NUMBER  — subaccount to trade on (default 0)
    DYDX_NETWORK            — "mainnet" (default) | "testnet"
    DYDX_INDEXER_HOST       — override indexer (testnet/local dev)
    DYDX_NODE_HOST          — override node gRPC target
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from decimal import Decimal
from typing import Any

from venues.dydx_funding import DydxFundingProvider

logger = logging.getLogger(__name__)

# Lazy import: the SDK pulls in grpcio / coincurve / pycryptodome; allow
# scan / dry-run code paths to import the adapter without the SDK
# installed (the depth fetcher is the only consumer that gates on
# capability checks).
_indexer_client_cls: type | None = None
_node_client_cls: type | None = None
_network_module: Any | None = None
_wallet_cls: Any | None = None
_market_cls: type | None = None
_order_flags_cls: Any | None = None
_order_type_cls: Any | None = None
_order_pb2_module: Any | None = None


def _ensure_sdk() -> None:
    """Import the dYdX v4 SDK on first use; raise with a clear error."""
    global _indexer_client_cls, _node_client_cls, _network_module, _wallet_cls
    global _market_cls, _order_flags_cls, _order_type_cls, _order_pb2_module
    if (
        _indexer_client_cls is not None
        and _node_client_cls is not None
        and _network_module is not None
        and _market_cls is not None
    ):
        return
    try:
        from dydx_v4_client import OrderFlags as _OF
        from dydx_v4_client import network as _net
        from dydx_v4_client.indexer.rest.constants import OrderType as _OT
        from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
        from dydx_v4_client.node.client import NodeClient
        from dydx_v4_client.node.market import Market as _Mkt
        from dydx_v4_client.wallet import Wallet as _Wallet
        from v4_proto.dydxprotocol.clob import order_pb2 as _order_pb2

        _network_module = _net
        _indexer_client_cls = IndexerClient
        _node_client_cls = NodeClient
        _wallet_cls = _Wallet
        _market_cls = _Mkt
        _order_flags_cls = _OF
        _order_type_cls = _OT
        _order_pb2_module = _order_pb2
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "dydx-v4-client is required for the dYdX v4 venue adapter; "
            "install with `pip install dydx-v4-client>=1.1.5`"
        ) from exc


# Default endpoints; overridable via DYDX_INDEXER_HOST / DYDX_NODE_HOST.
_DEFAULT_INDEXER = "https://indexer.dydx.trade"
_DEFAULT_NODE = "dydx-ops-grpc.public.blastapi.io:443"
_TESTNET_INDEXER = "https://indexer.v4testnet.dydx.exchange"
_TESTNET_NODE = "test-dydx-grpc.kingnodes.com:443"


def _network_config(network: str) -> tuple[str, str]:
    if network.lower() == "testnet":
        return _TESTNET_INDEXER, _TESTNET_NODE
    return _DEFAULT_INDEXER, _DEFAULT_NODE


def _run(coro: Any) -> Any:
    """Run an async SDK call from sync code; use a thread if a loop is up."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _base_from_pair(pair: str) -> str:
    s = pair.upper()
    return s[:-4] if s.endswith("USDT") else s


def _side_from_type(typ: str) -> str:
    """Map funding-arb trade type to dYdX order side (BUY/SELL)."""
    return "BUY" if typ in ("open_long", "close_short") else "SELL"


def _has_live_creds() -> bool:
    return bool(
        os.environ.get("DYDX_MNEMONIC", "").strip()
        and os.environ.get("DYDX_ADDRESS", "").strip()
    )


def _live_enabled() -> bool:
    return os.environ.get("DYDX_ENABLE_LIVE", "").strip() in ("1", "true", "yes")


def _market_meta(meta_row: dict[str, Any]) -> dict[str, Any]:
    """Translate an indexer market row into a per-base metadata dict."""
    if not meta_row:
        return {}
    try:
        step = float(meta_row.get("stepSize", 0) or 0)
    except (TypeError, ValueError):
        step = 0.0
    try:
        tick = float(meta_row.get("tickSize", 0) or 0)
    except (TypeError, ValueError):
        tick = 0.0

    def _decimals_from_step(step: float) -> int:
        if step <= 0:
            return 0
        exp = Decimal(str(step)).normalize().as_tuple().exponent
        if not isinstance(exp, int) or exp >= 0:
            return 0
        return -exp

    return {
        "symbol": meta_row.get("ticker", ""),
        "step_size": step,
        "tick_size": tick,
        "min_trade_base": 0.0,  # dYdX has no min size beyond stepSize
        "min_trade_usdt": 0.0,
        "quantity_precision": _decimals_from_step(step),
        "quote_precision": _decimals_from_step(tick),
        "initial_margin_fraction": float(meta_row.get("initialMarginFraction", 0) or 0),
        "maintenance_margin_fraction": float(
            meta_row.get("maintenanceMarginFraction", 0) or 0
        ),
        "atomic_resolution": int(meta_row.get("atomicResolution", -10) or -10),
        "quantum_conversion_exponent": int(
            meta_row.get("quantumConversionExponent", -9) or -9
        ),
        "subticks_per_tick": int(meta_row.get("subticksPerTick", 1) or 1),
        "clob_pair_id": int(meta_row.get("clobPairId", 0) or 0),
        "step_base_quantums": int(meta_row.get("stepBaseQuantums", 1) or 1),
    }


def _market_info_from_meta(base: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Build the SDK Market .market dict from our cached per-base metadata."""
    return {
        "clobPairId": meta.get("clob_pair_id", 0),
        "atomicResolution": meta.get("atomic_resolution", -10),
        "quantumConversionExponent": meta.get("quantum_conversion_exponent", -9),
        "stepBaseQuantums": meta.get("step_base_quantums", 1_000_000_000),
        "subticksPerTick": meta.get("subticks_per_tick", 100_000),
        "tickSize": meta.get("tick_size", 1.0),
        "oraclePrice": str(meta.get("oracle_price", 0)),
    }


def _submit_market_order(
    wallet: Any,
    node: Any,
    base: str,
    ticker: str,
    side: str,
    size_base: float,
    meta: dict[str, Any],
    subaccount: int,
) -> dict[str, Any]:
    """Build, sign, and broadcast a dYdX v4 IOC market order.

    Returns {order_id, tx_hash, latency_ms} on success.
    Raises on any failure so the caller can set record["status"] = "failed".
    """
    assert _market_cls is not None
    assert _order_flags_cls is not None
    assert _order_type_cls is not None
    assert _order_pb2_module is not None

    # Build SDK Market from our metadata
    market_info = _market_info_from_meta(base, meta)
    # Fetch oracle price from indexer for the market order price (with slippage)
    from venues.dydx_funding import DydxFundingProvider

    funding = DydxFundingProvider()
    cur = funding.fetch_current(f"{base}USDT")
    oracle_price = float(cur.get("mark_price", 0) or 0)
    if oracle_price <= 0:
        raise RuntimeError(f"no oracle price for {ticker}")
    market_info["oraclePrice"] = str(oracle_price)

    mkt = _market_cls(market=market_info)

    # Side mapping
    if side == "BUY":
        order_side = _order_pb2_module.Order.SIDE_BUY
        price = oracle_price * 1.005  # 0.5% slippage buffer for market order
    else:
        order_side = _order_pb2_module.Order.SIDE_SELL
        price = oracle_price * 0.995

    # Build order ID (SHORT_TERM with auto client_id from time)
    client_id = int(time.time() * 1000) % 2**31
    oid = mkt.order_id(
        wallet.address, subaccount, client_id, _order_flags_cls.SHORT_TERM
    )

    # Get current block height for good_til_block
    current_height: int = _run(node.latest_block_height())

    # Build the order
    new_order = mkt.order(
        order_id=oid,
        order_type=_order_type_cls.MARKET,
        time_in_force=None,  # SDK handles IOC for MARKET orders
        side=order_side,
        size=size_base,
        price=price,
        reduce_only=False,
        good_til_block=current_height + 20,  # ~20 blocks ≈ 20s
    )

    # Sign and broadcast
    t0 = time.time()
    result = _run(node.place_order(wallet, new_order))
    latency = int((time.time() - t0) * 1000)

    # Parse result
    tx_hash = ""
    if hasattr(result, "tx_response") and result.tx_response:
        tx_hash = getattr(result.tx_response, "txhash", "") or ""
    elif isinstance(result, dict):
        tx_hash = result.get("tx_response", {}).get("txhash", "")

    return {
        "order_id": f"{ticker}-{client_id}",
        "tx_hash": tx_hash,
        "latency_ms": latency,
    }


class DydxVenue:
    """dYdX v4 perp-DEX adapter — pure futures only (USDC-collateral, 1:1 USDT)."""

    venue_id: str = "dydx"

    def __init__(self) -> None:
        # Read-side provider is always available (public REST, no creds).
        self._funding = DydxFundingProvider()
        # Meta cache: base -> {stepSize, tickSize, ...}; refreshed every 5 min.
        self._meta_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._meta_ttl_sec = 300.0
        # Lazy async client (avoid constructing at __init__ so dry-run paths
        # that never touch the SDK stay cheap).
        self._indexer: Any | None = None
        self._network: str = os.environ.get("DYDX_NETWORK", "mainnet").lower()
        self._indexer_host, self._node_host = _network_config(self._network)
        self._indexer_host = os.environ.get("DYDX_INDEXER_HOST", self._indexer_host)
        self._node_host = os.environ.get("DYDX_NODE_HOST", self._node_host)
        self._subaccount = int(os.environ.get("DYDX_SUBACCOUNT_NUMBER", "0") or 0)
        # Live wallet/node (None if creds missing or not enabled).
        self._wallet: Any | None = None
        self._node: Any | None = None

    # ------------------------------------------------------------------
    # Meta cache (per-base step / tick / margin)
    # ------------------------------------------------------------------

    def _meta_map(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        if self._meta_cache and now - self._meta_cache[0] < self._meta_ttl_sec:
            return self._meta_cache[1]
        _ensure_sdk()
        assert _indexer_client_cls is not None
        ic = _indexer_client_cls(host=self._indexer_host)
        out = _run(ic.markets.get_perpetual_markets(market=None))
        # IndexerClient 1.1.x has no .close(); best-effort shutdown of
        # the underlying httpx client if it exists.
        for attr in ("_client", "client", "_session"):
            obj = getattr(ic, attr, None)
            if obj and hasattr(obj, "aclose"):
                try:
                    _run(obj.aclose())
                except Exception:  # noqa: BLE001
                    pass
        markets = (out or {}).get("markets", {}) if isinstance(out, dict) else {}
        out_map: dict[str, dict[str, Any]] = {}
        for ticker, row in markets.items():
            if not isinstance(row, dict):
                continue
            if str(row.get("status", "")).upper() not in ("ACTIVE",):
                continue
            base = ticker.split("-")[0].upper()
            meta = _market_meta(row)
            meta["ticker"] = ticker
            out_map[base] = meta
        self._meta_cache = (now, out_map)
        return out_map

    def contract_meta_for_base(self, base: str) -> dict[str, Any] | None:
        """Public — also used by market/futures_depth.py."""
        return self._meta_map().get(base.upper())

    def contract_meta_map(self) -> dict[str, dict[str, Any]]:
        return self._meta_map()

    # ------------------------------------------------------------------
    # Market data (ticker / rules)
    # ------------------------------------------------------------------

    def _indexer_lazy(self) -> Any:
        if self._indexer is None:
            _ensure_sdk()
            assert _indexer_client_cls is not None
            self._indexer = _indexer_client_cls(host=self._indexer_host)
        return self._indexer

    def get_futures_ticker(self, pair: str) -> float:
        """Last oracle price (mark) for a CEX-style pair."""
        cur = self._funding.fetch_current(pair)
        return float(cur.get("mark_price", 0) or 0)

    def get_ticker(self, pair: str) -> float:
        return self.get_futures_ticker(pair)

    def fetch_futures_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        base = _base_from_pair(pair)
        meta = self._meta_map().get(base)
        if not meta:
            return None
        return {
            "symbol": pair,
            "quantity_precision": meta["quantity_precision"],
            "quote_precision": meta["quote_precision"],
            "min_trade_usdt": 0.0,
            "min_trade_base": 0.0,
            "step_size": meta["step_size"],
            "tick_size": meta["tick_size"],
            "atomic_resolution": meta["atomic_resolution"],
            "quantum_conversion_exponent": meta["quantum_conversion_exponent"],
            "subticks_per_tick": meta["subticks_per_tick"],
        }

    def fetch_symbol_rules(
        self, pair: str, cache_sec: int = 3600
    ) -> dict[str, Any] | None:
        return self.fetch_futures_symbol_rules(pair, cache_sec)

    # ------------------------------------------------------------------
    # Account / positions (subaccount #0 by default; requires wallet)
    # ------------------------------------------------------------------

    def _ensure_wallet(self) -> tuple[Any, Any]:
        """Build the live wallet+node client from env (raises on missing creds)."""
        if self._wallet is not None and self._node is not None:
            return self._wallet, self._node
        _ensure_sdk()
        assert _network_module is not None
        assert _node_client_cls is not None
        assert _wallet_cls is not None
        mnemonic = os.environ.get("DYDX_MNEMONIC", "").strip()
        address = os.environ.get("DYDX_ADDRESS", "").strip()
        if not mnemonic or not address:
            raise RuntimeError(
                "dYdX live orders require DYDX_MNEMONIC and DYDX_ADDRESS; "
                "missing env vars — use dry-run for simulation."
            )
        if self._network == "testnet":
            net = _network_module.make_testnet(
                self._indexer_host, "wss://", self._node_host
            )
        else:
            net = _network_module.make_mainnet(
                self._indexer_host, "wss://", self._node_host
            )
        node = _node_client_cls(node=net.node)
        wallet = _run(_wallet_cls.from_mnemonic(node, mnemonic, address))
        self._node = node
        self._wallet = wallet
        return wallet, node

    def fetch_usdt_account_balances(self) -> dict[str, float]:
        """Read free collateral (USDC ≈ USDT 1:1 on dYdX v4)."""
        if not (_has_live_creds() and _live_enabled()):
            return {"spot": 0.0, "futures": 0.0}
        try:
            wallet, node = self._ensure_wallet()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dYdX wallet setup failed (%s); returning 0", exc)
            return {"spot": 0.0, "futures": 0.0}
        try:
            sub = _run(node.account.get_subaccount(wallet.address, self._subaccount))
        except Exception as exc:  # noqa: BLE001
            logger.warning("dYdX balance fetch failed (%s); returning 0", exc)
            return {"spot": 0.0, "futures": 0.0}
        sub_dict = (sub or {}).get("subaccount", {}) if isinstance(sub, dict) else {}
        try:
            free = float(sub_dict.get("freeCollateral", 0) or 0)
        except (TypeError, ValueError):
            free = 0.0
        try:
            equity = float(sub_dict.get("equity", 0) or 0)
        except (TypeError, ValueError):
            equity = 0.0
        return {"spot": 0.0, "futures": free or equity}

    def fetch_futures_positions(self, quote: str = "USDT") -> list[dict[str, Any]]:
        """Read open perpetual positions from subaccount."""
        if not (_has_live_creds() and _live_enabled()):
            return []
        try:
            wallet, _node = self._ensure_wallet()
            ic = self._indexer_lazy()
            out = _run(
                ic.account.get_subaccount_perpetual_positions(
                    wallet.address, self._subaccount, status="OPEN"
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dYdX positions fetch failed (%s); returning []", exc)
            return []
        rows = (out or {}).get("positions", []) if isinstance(out, dict) else []
        if not isinstance(rows, list):
            return []
        positions: list[dict[str, Any]] = []
        for p in rows:
            if not isinstance(p, dict):
                continue
            market = str(p.get("market", ""))
            base = market.split("-")[0].upper() if market else ""
            try:
                size = float(p.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size == 0:
                continue
            try:
                entry = float(p.get("entryPrice", 0) or 0)
            except (TypeError, ValueError):
                entry = 0.0
            try:
                liq = float(p.get("liquidationPrice", 0) or 0)
            except (TypeError, ValueError):
                liq = 0.0
            try:
                unrealized = float(p.get("unrealizedPnl", 0) or 0)
            except (TypeError, ValueError):
                unrealized = 0.0
            try:
                leverage = float(p.get("leverage", 1) or 1)
            except (TypeError, ValueError):
                leverage = 1.0
            positions.append(
                {
                    "symbol": f"{base}{quote.upper()}" if base else market,
                    "side": "long" if size > 0 else "short",
                    "qty": abs(size),
                    "entry_price": entry,
                    "liq_price": liq,
                    "leverage": leverage,
                    "unrealized_pnl": unrealized,
                }
            )
        return positions

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def initialize_futures_symbol(self, pair: str) -> None:
        """No-op: dYdX v4 is cross-margin by default and there's no per-pair
        leverage toggle on the trading side (leverage is set via subaccount
        equity / margin mode, not per market).
        """

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_trades(
        self,
        trades: list[dict[str, Any]],
        market: dict[str, dict[str, Any]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        """Pure-futures execution.

        Trade type mapping (mirrors hyperliquid / edgex):
            open_long  / close_short -> BUY
            open_short / close_long  -> SELL

        dry_run=True: simulate with ref_price; record shape matches HL.
        dry_run=False: requires DYDX_ENABLE_LIVE=1; live path is gated
        behind an explicit opt-in because dYdX v4's order-placement flow
        involves signing a Cosmos protobuf message with quantums /
        subticks / clob_pair_id — a sufficiently nuanced builder to land
        here without end-to-end testnet coverage. We surface the
        failure mode as a "failed" record rather than swallow it, so
        the executor / watcher can detect and stop.
        """
        results: list[dict[str, Any]] = []
        for trade in trades:
            symbol = trade["symbol"]
            typ = trade["type"]
            mkt = market.get(symbol, {})
            ref_price = float(mkt.get("price", 0))
            record = dict(trade)
            record["dry_run"] = dry_run
            record["venue"] = self.venue_id
            record["ref_price"] = ref_price

            if typ not in ("open_long", "open_short", "close_long", "close_short"):
                record["status"] = "failed"
                record["error"] = f"Unknown trade type: {typ}"
                record["order_id"] = None
                results.append(record)
                continue

            if dry_run:
                record["status"] = "simulated"
                record["order_id"] = None
                record["exec_qty"] = trade.get("amount_base", 0)
                record["exec_price"] = ref_price
                record["slippage"] = 0.0
                record["latency_ms"] = 0
                record["error"] = None
                results.append(record)
                continue

            # Live path: explicit opt-in required.
            if not _live_enabled():
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = (
                    "dYdX live orders require DYDX_ENABLE_LIVE=1 (dry-run "
                    "default). Live placement uses the Cosmos protobuf "
                    "place_order message (Builder + quantums/subticks/"
                    "clob_pair_id) — needs testnet rehearsal before "
                    "mainnet deployment. See plans/dydx-trading.md."
                )
                results.append(record)
                continue

            # Live branch: validate creds, build and submit order.
            try:
                wallet, node = self._ensure_wallet()
                size = float(trade.get("amount_base", 0))
                if size <= 0:
                    raise RuntimeError(f"non-positive size: {size}")

                base = _base_from_pair(symbol)
                meta = self._meta_map().get(base)
                if not meta:
                    raise RuntimeError(f"no market metadata for {symbol}")

                side = _side_from_type(typ)
                ticker = meta.get("ticker", f"{base}-USD")

                t0 = time.time()
                result = _submit_market_order(
                    wallet=wallet,
                    node=node,
                    base=base,
                    ticker=ticker,
                    side=side,
                    size_base=size,
                    meta=meta,
                    subaccount=self._subaccount,
                )
                record["status"] = "submitted"
                record["order_id"] = result.get("order_id")
                record["tx_hash"] = result.get("tx_hash", "")
                record["exec_qty"] = size
                record["exec_price"] = ref_price
                record["slippage"] = 0.0
                record["latency_ms"] = result.get("latency_ms", 0)
                record["error"] = None
            except Exception as exc:  # noqa: BLE001
                record["status"] = "failed"
                record["order_id"] = None
                record["error"] = str(exc)
            results.append(record)
        return results
