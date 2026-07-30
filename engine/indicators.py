"""Technical indicators, computed across the entire universe at once.

Every function here takes date x ticker DataFrames and returns the same shape,
so a 5,600-name universe costs about the same as a hundred. `build_features`
collapses the panels down to one row per ticker holding the latest reading of
each indicator, which is what the rule engine consumes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

TRADING_DAYS = 252


# --------------------------------------------------------------- primitives --
def wilder(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder's smoothing — the EMA variant RSI/ATR/ADX are defined with."""
    return df.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    rs = wilder(gain, n) / wilder(loss, n).replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def true_range(high, low, close) -> pd.DataFrame:
    prev = close.shift(1)
    a = (high - low).to_numpy()
    b = (high - prev).abs().to_numpy()
    c = (low - prev).abs().to_numpy()
    return pd.DataFrame(np.maximum(np.maximum(a, b), c),
                        index=high.index, columns=high.columns)


def atr(high, low, close, n: int = 14) -> pd.DataFrame:
    return wilder(true_range(high, low, close), n)


def adx(high, low, close, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr_n = wilder(true_range(high, low, close), n).replace(0, np.nan)
    plus_di = 100 * wilder(plus_dm, n) / tr_n
    minus_di = 100 * wilder(minus_dm, n) / tr_n
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    return wilder(dx, n)


def macd(close: pd.DataFrame, fast=12, slow=26, sig=9):
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal


def bars_since_flip(flag: pd.DataFrame) -> pd.Series:
    """For each column, how many bars since the boolean last changed value.

    Used to catch *fresh* golden/death crosses rather than ones from months ago.
    """
    as_int = flag.astype("float64")
    changed = as_int.diff().fillna(0) != 0
    # Seed row 0 so a column that never flips still yields a finite (large)
    # age rather than NaN.
    changed.iloc[0] = True
    pos = pd.Series(np.arange(len(flag), dtype="float64"), index=flag.index)
    marked = changed.mul(0.0).add(pos, axis=0).where(changed)
    last = marked.ffill().iloc[-1]
    return (len(flag) - 1) - last


def tail_pct_rank(df: pd.DataFrame, window: int = TRADING_DAYS) -> pd.Series:
    """Percentile of the latest value within its own trailing window (0-1).

    Only the last value is needed, so this beats a full rolling rank by a wide
    margin on a universe this size.
    """
    recent = df.tail(window)
    latest = recent.iloc[-1]
    below = recent.lt(latest, axis=1).sum()
    valid = recent.notna().sum().replace(0, np.nan)
    return below / valid


# ------------------------------------------------------------------ feature --
def build_features(panels: dict[str, pd.DataFrame],
                   bench_close: pd.Series) -> pd.DataFrame:
    """Collapse panels into one row per ticker of latest indicator readings."""
    close = panels["close"].ffill(limit=5)
    high = panels["high"].ffill(limit=5)
    low = panels["low"].ffill(limit=5)
    volume = panels["volume"].fillna(0.0)

    last = close.iloc[-1]
    f = pd.DataFrame(index=close.columns)
    f["price"] = last
    f["prev_close"] = close.iloc[-2]

    # Moving averages
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    f["sma20"] = sma20.iloc[-1]
    f["sma50"] = sma50.iloc[-1]
    f["sma200"] = sma200.iloc[-1]

    # Oscillators / trend strength
    r = rsi(close)
    f["rsi"] = r.iloc[-1]
    f["rsi_prev"] = r.iloc[-2]
    f["adx"] = adx(high, low, close).iloc[-1]

    m_line, m_sig, m_hist = macd(close)
    f["macd"] = m_line.iloc[-1]
    f["macd_signal"] = m_sig.iloc[-1]
    f["macd_hist"] = m_hist.iloc[-1]
    f["macd_hist_prev"] = m_hist.iloc[-5]

    # Volatility
    a = atr(high, low, close)
    f["atr"] = a.iloc[-1]
    f["atr_pct"] = (a.iloc[-1] / last).replace([np.inf, -np.inf], np.nan)

    logret = np.log(close / close.shift(1))
    rv20 = logret.rolling(20).std() * np.sqrt(TRADING_DAYS)
    rv60 = logret.rolling(60).std() * np.sqrt(TRADING_DAYS)
    f["rvol_20"] = rv20.iloc[-1]
    f["rvol_60"] = rv60.iloc[-1]

    # Gap-resistant volatility. A single merger/FDA/earnings gap inflates a
    # plain standard deviation for months afterwards, which then makes normal
    # option prices look artificially cheap. The MAD-based estimator ignores
    # the outlier day and describes the stock's *typical* movement.
    r60 = logret.tail(60)
    mad = (r60 - r60.median()).abs().median()
    f["rvol_robust"] = (1.4826 * mad * np.sqrt(TRADING_DAYS))
    f["vol_ratio"] = (rv20.iloc[-1] / rv60.iloc[-1]).replace([np.inf, -np.inf], np.nan)
    f["rvol_pctile"] = tail_pct_rank(rv20)

    # Bollinger
    std20 = close.rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    width = ((upper - lower) / sma20).replace([np.inf, -np.inf], np.nan)
    f["bb_pctb"] = ((last - lower.iloc[-1]) /
                    (upper.iloc[-1] - lower.iloc[-1]).replace(0, np.nan))
    f["bb_width"] = width.iloc[-1]
    f["bb_width_pctile"] = tail_pct_rank(width)

    # 52-week context
    hi52 = high.rolling(TRADING_DAYS, min_periods=100).max().iloc[-1]
    lo52 = low.rolling(TRADING_DAYS, min_periods=100).min().iloc[-1]
    f["high_52w"] = hi52
    f["low_52w"] = lo52
    f["pct_from_high"] = (last / hi52 - 1) * 100
    f["pct_from_low"] = (last / lo52 - 1) * 100

    # Returns
    for label, n in (("1d", 1), ("5d", 5), ("21d", 21),
                     ("63d", 63), ("126d", 126), ("252d", 252)):
        if len(close) > n:
            f[f"ret_{label}"] = (last / close.iloc[-1 - n] - 1) * 100
        else:
            f[f"ret_{label}"] = np.nan

    # Liquidity
    dollar_vol = (close * volume).rolling(20).mean().iloc[-1]
    f["dollar_vol"] = dollar_vol
    v5 = volume.rolling(5).mean().iloc[-1]
    v60 = volume.rolling(60).mean().iloc[-1].replace(0, np.nan)
    f["vol_surge"] = v5 / v60

    # Structure, expressed in ATR units so it compares across price levels
    atr_last = a.iloc[-1].replace(0, np.nan)
    f["dist_sma20_atr"] = (last - sma20.iloc[-1]) / atr_last
    f["dist_sma50_atr"] = (last - sma50.iloc[-1]) / atr_last
    f["dist_sma200_atr"] = (last - sma200.iloc[-1]) / atr_last
    f["above_sma50"] = last > sma50.iloc[-1]
    f["above_sma200"] = last > sma200.iloc[-1]
    f["uptrend_stack"] = (sma50.iloc[-1] > sma200.iloc[-1])
    f["bars_since_ma_cross"] = bars_since_flip(sma50 > sma200)

    # Relative strength vs the benchmark
    bench = bench_close.reindex(close.index).ffill()
    for label, n in (("21d", 21), ("63d", 63), ("126d", 126)):
        if len(bench) > n and pd.notna(bench.iloc[-1 - n]):
            bret = (bench.iloc[-1] / bench.iloc[-1 - n] - 1) * 100
            f[f"rs_{label}"] = f[f"ret_{label}"] - bret
        else:
            f[f"rs_{label}"] = np.nan

    # Drawdown from the 1-year peak
    f["drawdown"] = (last / hi52 - 1) * 100

    f = f.replace([np.inf, -np.inf], np.nan)
    return f


def apply_liquidity_filter(f: pd.DataFrame) -> pd.DataFrame:
    """Drop names where no realistic execution exists."""
    keep = (
        f["price"].ge(C.MIN_PRICE)
        & f["dollar_vol"].ge(C.MIN_DOLLAR_VOL)
        & f["sma200"].notna()
        & f["atr"].gt(0)
    )
    return f[keep].copy()
