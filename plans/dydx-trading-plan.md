# dYdX v4 Live Trading 实施计划

Last updated: 2026-06-13

## 当前状态

dYdX v4 集成在 `funding-arb` 中 **scan + dry-run 已完成**，live order submission 待实现：

- ✅ Funding rate scan (`venues/dydx_funding.py` — DydxFundingProvider, 1h funding, historical funding, orderbook-mid index proxy)
- ✅ Venue adapter (`venues/dydx.py` — DydxVenue, meta cache, market data, dry-run execution, account gating)
- ✅ Order book depth (`market/futures_depth.py` — _fetch_dydx_depth)
- ✅ Fee integration (`core/fee_providers.py` — maker=0bps, taker=5bps default)
- ✅ VIP fee tier (`core/vip_fee_tiers.py` — default tier only)
- ✅ Settings API (`server/routes/settings.py` — trade_keys, capability check)
- ✅ Integration tests (`tests/test_dydx_funding.py`, `tests/test_dydx_venue.py` — 256 lines)
- ✅ Environment config (`.env.example` — DYDX_MNEMONIC, DYDX_ADDRESS, DYDX_NETWORK, etc.)
- ❌ Live order submission (`execute_trades` live 分支抛出 RuntimeError 占位)

---

## 技术设计：Cosmos Protobuf Order Builder

### 架构

dYdX v4 是 Cosmos SDK 应用链。订单作为链上交易提交：

```
User → Builder (构建未签名 tx) → Wallet (私钥签名) → Node (广播上链)
```

### 关键换算公式

dYdX v4 SDK 内部使用原子单位：

**1. Size → Quantums**

SDK 提供内置转换，无需手动计算。核心公式：

```
quantums = size_base × step_base_quantums / stepSize
```

其中 `step_base_quantums` 由市场的 `atomicResolution` 和 `quantumConversionExponent` 推导。

对于 BTC（atomicResolution=-10, quantumConversionExponent=-9）：
- 0.01 BTC → 约 1,000,000,000 quantums（SDK 自动处理精度）

**2. Price → Subticks**

```
subticks = price / tickSize × subticksPerTick
```

**3. 订单方向映射**

| funding-arb trade type | dYdX side |
|------------------------|-----------|
| `open_long`            | BUY       |
| `close_short`          | BUY       |
| `open_short`           | SELL      |
| `close_long`           | SELL      |

**4. Good-Til-Block**

- 必须 > 当前区块高度 + 1
- 通常设为 `current_height + 20`（约 20 秒成交窗口）
- 通过 node client 查询当前区块高度

---

## 实施步骤

### Step 1: 实现 _build_and_submit_order

在 `venues/dydx.py` 中添加：

```python
def _build_and_submit_order(
    wallet, node, indexer, subaccount: int,
    ticker: str, side: str, size_base: float,
    market_meta: dict, is_market: bool = True,
) -> dict:
    """Build, sign, and submit a dYdX v4 order. Returns order result dict."""
    from dydx_v4_client.order import OrderSide, OrderType, TimeInForce

    # 1. 获取当前区块高度
    # 2. 使用 SDK Builder 构建 order（quantums/subticks 由 SDK 处理）
    # 3. Wallet 签名
    # 4. Node 广播
    # 5. 返回 {order_id, status, latency_ms}
    ...
```

### Step 2: 替换 execute_trades 占位代码

将 `dydx.py` 约第 470-484 行的 `raise RuntimeError(...)` 替换为：

```python
try:
    wallet, node = self._ensure_wallet()
    size = float(trade.get("amount_base", 0))
    if size <= 0:
        raise RuntimeError(f"non-positive size: {size}")

    base = _base_from_pair(symbol)
    meta = self._meta_map().get(base)
    if not meta:
        raise RuntimeError(f"no market metadata for {symbol}")

    side = "BUY" if typ in ("open_long", "close_short") else "SELL"
    t0 = time.time()
    result = _build_and_submit_order(
        wallet, node, self._indexer_lazy(), self._subaccount,
        ticker=meta["ticker"],
        side=side,
        size_base=size,
        market_meta=meta,
    )
    record["status"] = "filled"
    record["order_id"] = result.get("order_id")
    record["exec_qty"] = size
    record["exec_price"] = ref_price
    record["slippage"] = 0.0
    record["latency_ms"] = int((time.time() - t0) * 1000)
    record["error"] = None
except Exception as exc:
    record["status"] = "failed"
    record["order_id"] = None
    record["error"] = str(exc)
```

### Step 3: 添加区块高度查询

```python
def _current_block_height(node) -> int:
    """Query latest block height from the dYdX node."""
    # node.client 或者通过 SDK 的 get_latest_blockheight
    ...
```

---

## 测试计划

### 单元测试（mocked SDK）

在 `tests/test_dydx_venue.py` 中添加：

```python
class TestLiveExecution:
    """Test the live order path with mocked SDK components."""

    def test_build_order_buy_side(self, monkeypatch):
        """Verify open_long maps to BUY side."""
        ...

    def test_build_order_sell_side(self, monkeypatch):
        """Verify open_short maps to SELL side."""
        ...

    def test_live_order_success(self, monkeypatch):
        """Full happy path: wallet → build → sign → broadcast."""
        ...

    def test_live_order_sdk_error(self, monkeypatch):
        """SDK raises on broadcast → record.status == 'failed'."""
        ...
```

### Testnet 验证

```bash
export DYDX_NETWORK=testnet
export DYDX_INDEXER_HOST=https://indexer.v4testnet.dydx.exchange
export DYDX_NODE_HOST=test-dydx-grpc.kingnodes.com:443
export DYDX_MNEMONIC="your testnet 24-word mnemonic"
export DYDX_ADDRESS="dydx1..."
export DYDX_ENABLE_LIVE=1

# Step 1: 验证账户读取
.venv/bin/python -c "
from venues.dydx import DydxVenue
v = DydxVenue()
print(v.fetch_usdt_account_balances())
print(v.fetch_futures_positions())
"

# Step 2: 开小仓位
.venv/bin/python scripts/cli/pure_futures_trade.py open BTC \
  --long-venue dydx --short-venue bybit --trade-usd 50 --live

# Step 3: 验证持仓
.venv/bin/python scripts/cli/pure_futures_trade.py list

# Step 4: 平仓
.venv/bin/python scripts/cli/pure_futures_trade.py close <id> --live
```

### Mainnet 验证（最小仓位）

- 与 testnet 流程相同，但 `DYDX_NETWORK=mainnet`
- 使用最小交易量（确认 stepSize 最低要求）
- 监控订单成交确认

---

## SDK 参考

`dydx-v4-client` 包（>=1.1.5）提供：

| 模块 | 用途 |
|------|------|
| `IndexerClient` | REST API — 市场数据、账户信息、订单簿 |
| `NodeClient` | gRPC — 链上操作（广播交易） |
| `Wallet` | 密钥管理、交易签名（`from_mnemonic()`） |
| `Order` / `Builder` | 订单构建，类型安全的 protobuf |

关键模块路径：
```
dydx_v4_client/indexer/rest/indexer_client.py   — IndexerClient
dydx_v4_client/node/client.py                    — NodeClient
dydx_v4_client/wallet.py                         — Wallet.from_mnemonic()
```

需要通过阅读 SDK 源码确认的 API：
- Order 构建方式（`place_order` 方法签名）
- quantums 换算辅助函数
- 广播后的交易回执解析

---

## 风险考量

| 风险 | 缓解措施 |
|------|----------|
| Protobuf 精度 | quantums 是整数；使用 SDK 内置转换避免浮点误差 |
| 区块时间 | good_til_block 设为 current+20；过期后订单自动取消 |
| USDC vs USDT | dYdX 用 USDC 作为抵押；记账时按 1:1 处理，但需注明差异 |
| Cross-margin | dYdX v4 默认全仓模式；开仓影响整个子账户净值 |
| Gas fees | Cosmos 交易费极低（~$0.01），但应在 P&L 中计入 |
| 订单部分成交 | IOC 市价单通常全成或全不成交；需处理边界情况 |

---

## 完成清单

- [ ] 研究 dydx-v4-client SDK Order/Builder API
- [ ] 实现 `_build_and_submit_order` 辅助函数
- [ ] 替换 `execute_trades` 中的 RuntimeError 占位
- [ ] 添加 live order 路径的 mocked 单元测试
- [ ] Testnet: 账户读取验证
- [ ] Testnet: 开仓（小仓位）
- [ ] Testnet: 平仓
- [ ] Mainnet: 最小仓位端到端测试
- [ ] 更新 `ROADMAP.md` 状态
- [ ] 更新 `SKILL.md` 添加 live trading 说明
