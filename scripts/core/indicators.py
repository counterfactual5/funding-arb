#!/usr/bin/env python3
"""Technical indicators — pure functions, no IO."""
from __future__ import annotations

from typing import Optional


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_ema(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1)
    # 标准递推：用最早 period 点的 SMA 初始化，再遍历其后所有点。
    # 截断到最近 period*4 个点（EMA 指数衰减，超出部分权重<0.03%），
    # 兼顾正确性与性能（避免回测中每根 bar 全序列重算导致 O(n^2)）。
    window = closes[-(period * 4):] if len(closes) > period * 4 else closes
    ema = sum(window[:period]) / period
    for price in window[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_atr(klines: list[dict], period: int = 14) -> Optional[float]:
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        prev_close = klines[i - 1]["close"]
        h = klines[i]["high"]
        l = klines[i]["low"]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def derive_drawdown(price: float, closes: list[float], highs: list[float], bars: int | None = None) -> dict:
    """Drawdown metrics from daily closes/highs.

    bars: 回看窗口（1d K线数）。回撤"峰值"取最近 bars 根内的最高点，而非诞生以来的 ATH，
    使老币不会因 6 年前的上古高点而永久处于"极端回撤"。None=无界(遗留行为)。"""
    if bars is not None and bars > 0:
        closes = closes[-bars:]
        highs = highs[-bars:]
    dd_series: list[float] = []
    peak = 0.0
    for i, c in enumerate(closes):
        peak = max(peak, highs[i] if i < len(highs) else c, c)
        if peak > 0:
            dd_series.append((peak - c) / peak)
    anchor = max(highs) if highs else (max(closes) if closes else price)
    drawdown = (anchor - price) / anchor if anchor > 0 else 0.0
    pct = sum(1 for d in dd_series if d <= drawdown) / len(dd_series) if dd_series else 0.0
    diffs = [dd_series[i] - dd_series[i - 1] for i in range(1, len(dd_series))]
    sigma_dd = _std([abs(d) for d in diffs]) if diffs else 0.0
    return {
        "drawdown": drawdown,
        "pct_drawdown": pct,
        "sigma_dd": sigma_dd,
    }


def detect_pinbar(kline: dict, prev_kline: dict) -> bool:
    body = abs(kline["close"] - kline["open"])
    lower_shadow = kline["low"] - min(kline["open"], kline["close"])
    upper_shadow = max(kline["open"], kline["close"]) - kline["high"]
    return lower_shadow >= body * 2 and lower_shadow > upper_shadow and body > 0


def detect_engulfing(kline: dict, prev_kline: dict) -> bool:
    prev_bear = prev_kline["close"] < prev_kline["open"]
    curr_bull = kline["close"] > kline["open"]
    if not (prev_bear and curr_bull):
        return False
    prev_body = abs(prev_kline["close"] - prev_kline["open"])
    curr_body = abs(kline["close"] - kline["open"])
    return (
        curr_body > prev_body * 1.2
        and kline["close"] > prev_kline["open"]
        and kline["open"] < prev_kline["close"]
    )


def detect_death_cross(closes: list[float], ema_20: Optional[float], ema_50: Optional[float]) -> bool:
    if ema_20 is None or ema_50 is None or len(closes) < 2:
        return False
    if ema_20 >= ema_50 or closes[-1] >= ema_20:
        return False
    prev_closes = closes[:-1]
    prev_ema20 = calc_ema(prev_closes, 20)
    return prev_ema20 is not None and prev_ema20 > ema_50


def calc_hurst(closes: list[float], max_lag: int = 20) -> Optional[float]:
    """
    Calculate the Hurst exponent using the variance of increments approximation.
    H < 0.5: Mean-reverting
    H ~ 0.5: Random walk
    H > 0.5: Trending
    """
    import math
    if len(closes) < max_lag * 2:
        return None
        
    # 性能排雷: 截断到最近的 100 根线，避免在长回测中因全序列遍历导致 O(n^2) 的性能塌方
    lookback = max(100, max_lag * 4)
    if len(closes) > lookback:
        closes = closes[-lookback:]
        
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        diffs = [closes[i] - closes[i - lag] for i in range(lag, len(closes))]
        if not diffs:
            continue
        std = _std(diffs)
        tau.append(std)
    
    if len(tau) < 2 or any(t <= 0 for t in tau):
        return None
        
    # Linear regression on log-log scale
    log_lags = [math.log(l) for l in lags]
    log_tau = [math.log(t) for t in tau]
    
    n = len(log_lags)
    sum_x = sum(log_lags)
    sum_y = sum(log_tau)
    sum_xx = sum(x * x for x in log_lags)
    sum_xy = sum(x * y for x, y in zip(log_lags, log_tau))
    
    denominator = n * sum_xx - sum_x ** 2
    if denominator == 0:
        return None
        
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return slope  # For variance of increments, Hurst = slope


def calc_bollinger_bands(closes: list[float], period: int = 20, num_std: float = 2.0) -> Optional[tuple[float, float, float]]:
    """Returns (SMA, Upper Band, Lower Band)"""
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    std = _std(window)
    return sma, sma + num_std * std, sma - num_std * std
