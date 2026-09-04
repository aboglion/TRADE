"""
Technical indicator computation.

EXACT PORT of the indicator logic from BACK_TEST/engine.py lines 144-209
and 456-507.  Pure functions with no side effects — they transform
DataFrames and nothing else.

WARNING: Do not modify the indicator math.  Any change will break
strategy parity with the backtest.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from src.core.models import Candle


def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
    """Convert a list of Candle objects to a DataFrame matching engine.py format."""
    records = []
    for c in candles:
        records.append({
            "Date": pd.Timestamp(c.timestamp_ms, unit="ms", tz="UTC"),
            "Open": c.open,
            "High": c.high,
            "Low": c.low,
            "Close": c.close,
            "Volume": c.volume if c.volume > 0 else 1e6,
        })
    df = pd.DataFrame(records)
    df = df.set_index("Date").sort_index()
    return df


def add_indicators(df: pd.DataFrame, vol_q: float = 0.70) -> pd.DataFrame:
    """
    Add macro technical indicators and regime classification.

    EXACT PORT of engine.py add_indicators() (lines 144-209).
    """
    x = df.copy()
    x["EMA20"] = x.Close.ewm(span=20, adjust=False).mean()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    x["Donchian30Low"] = x.Low.rolling(30).min().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()

    prev = x.Close.shift()
    tr = pd.concat(
        [x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()],
        axis=1,
    ).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / 14, min_periods=14).mean()
    loss_clean = loss.replace(0, np.nan)
    rs = gain / loss_clean
    rsi = 100 - (100 / (1 + rs))
    fallback = pd.Series(
        np.where(gain > 0, 100.0, 50.0), index=gain.index
    )
    x["RSI"] = rsi.fillna(fallback)

    high_diff = x.High.diff()
    low_diff = -x.Low.diff()
    pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
    atr_safe = x.ATR.replace(0, np.nan)
    pos_di = (
        100
        * pd.Series(pos_dm, index=x.index)
        .ewm(alpha=1 / 14, min_periods=14)
        .mean()
        / atr_safe
    ).fillna(0.0)
    neg_di = (
        100
        * pd.Series(neg_dm, index=x.index)
        .ewm(alpha=1 / 14, min_periods=14)
        .mean()
        / atr_safe
    ).fillna(0.0)
    denom = (pos_di + neg_di).replace(0, np.nan)
    dx = (100 * (pos_di - neg_di).abs() / denom).fillna(0.0)
    x["ADX"] = dx.ewm(alpha=1 / 14, min_periods=14).mean()

    vol_thresh = x.Volume.rolling(500, min_periods=100).quantile(vol_q)
    x["HighVol"] = x.Volume > vol_thresh

    # Regime classification
    regimes = []
    close_v = x.Close.values
    ema20_v, ema50_v, ema200_v = x.EMA20.values, x.EMA50.values, x.EMA200.values
    rsi_v = x.RSI.values
    adx_v = x.ADX.values

    for i in range(len(x)):
        c = close_v[i]
        e20, e50, e200 = ema20_v[i], ema50_v[i], ema200_v[i]
        rsi_val = rsi_v[i]
        adx_val = adx_v[i] if not np.isnan(adx_v[i]) else 0.0

        if (c > e20 > e50 > e200) and (rsi_val > 50) and (adx_val >= 22.0):
            regimes.append("STRONG_BULL_TREND")
        elif (c > e50 > e200) and (c > e20) and (adx_val >= 20.0):
            regimes.append("TREND")
        elif c < e50 < e200:
            regimes.append("BEAR")
        else:
            regimes.append("SIDEWAYS")

    x["Regime"] = regimes
    return x.dropna(subset=["ATR", "RSI", "ADX", "EMA200"])


def add_micro_indicators(
    df: pd.DataFrame,
    cfg: Optional[Dict] = None,
) -> pd.DataFrame:
    """
    Add micro satellite indicators and regime classification.

    EXACT PORT of engine.py add_micro_indicators() (lines 456-507).
    """
    if cfg is None:
        cfg = DEFAULT_MICRO_CFG

    x = df.copy()
    x["EMA9"] = x.Close.ewm(span=cfg["ema_fast"], adjust=False).mean()
    x["EMA21"] = x.Close.ewm(span=cfg["ema_med"], adjust=False).mean()
    x["EMA50"] = x.Close.ewm(span=cfg["ema_slow"], adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=cfg["ema_macro"], adjust=False).mean()
    x["VolSMA20"] = x.Volume.rolling(20).mean()

    prev = x.Close.shift()
    tr = pd.concat(
        [x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()],
        axis=1,
    ).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / 9, min_periods=9).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / 9, min_periods=9).mean()
    loss_clean = loss.replace(0, np.nan)
    rs = gain / loss_clean
    rsi = 100 - (100 / (1 + rs))
    fallback = pd.Series(
        np.where(gain > 0, 100.0, 50.0), index=gain.index
    )
    x["RSI"] = rsi.fillna(fallback)

    d_bars = cfg["donchian_micro_bars"]
    x["DonchianMicroHigh"] = x.High.rolling(d_bars).max().shift(1)
    x["Ret30D"] = (x.Close - x.Close.shift(180)) / x.Close.shift(180)

    # Micro regime classification
    regimes = []
    close_v = x.Close.values
    open_v = x.Open.values
    ema9_v = x.EMA9.values
    ema21_v = x.EMA21.values
    ema50_v = x.EMA50.values
    ema200_v = x.EMA200.values
    rsi_v = x.RSI.values
    vol_v = x.Volume.values
    volsma_v = x.VolSMA20.values
    donch_hi = x.DonchianMicroHigh.values
    ret30_v = x.Ret30D.fillna(0).values

    for i in range(len(x)):
        c, o = close_v[i], open_v[i]
        e9, e21, e50, e200 = ema9_v[i], ema21_v[i], ema50_v[i], ema200_v[i]
        rsi_val = rsi_v[i]
        vol = vol_v[i]
        volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
        ret30 = ret30_v[i]

        macro_strong = (c > e50 > e200) and (ret30 > 0.05)
        donch_val = donch_hi[i] if not np.isnan(donch_hi[i]) else float("inf")

        if (
            macro_strong
            and c >= donch_val
            and rsi_val >= cfg["rsi_surge_min"]
            and vol > volsma * cfg["vol_surge_mult"]
        ):
            regimes.append("HIGH_CONVICTION_MICRO")
        elif (
            macro_strong
            and (c > e9 > e21)
            and (c > o)
            and rsi_val >= 52.0
            and vol > volsma * 1.3
        ):
            regimes.append("MICRO_TREND_ACCELERATION")
        else:
            regimes.append("MICRO_NEUTRAL")

    x["MicroRegime"] = regimes
    return x.dropna(subset=["ATR", "RSI", "EMA200"])


# ── Default micro config (from engine.py lines 94-112) ───────

from typing import Optional

DEFAULT_MICRO_CFG = dict(
    ema_fast=9,
    ema_med=21,
    ema_slow=50,
    ema_macro=200,
    rsi_period=9,
    rsi_surge_min=56.0,
    vol_surge_mult=1.6,
    donchian_micro_bars=24,
    min_edge_to_fee_ratio=6.0,
    init_stop_atr=1.8,
    trail_atr=3.2,
    tp1_atr=3.5,
    tp1_fraction=0.50,
    be_trigger_atr=1.5,
    max_hold_bars=42,
    base_alloc=0.85,
    strong_alloc=0.95,
)
