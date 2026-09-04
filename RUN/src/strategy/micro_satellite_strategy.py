"""
Micro Satellite Strategy for short-term momentum and trend acceleration trades.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

from src.core.enums import OrderSide, PositionAction
from src.core.models import Candle, PortfolioSnapshot, StrategySignal
from src.strategy.regime_adaptive_strategy import candles_to_dataframe

logger = logging.getLogger("bot.strategy.micro")

DEFAULT_MICRO_CFG = {
    "ema_fast": 9,
    "ema_med": 21,
    "ema_slow": 50,
    "ema_macro": 200,
    "rsi_period": 9,
    "rsi_surge_min": 56.0,
    "vol_surge_mult": 1.6,
    "donchian_micro_bars": 24,
    "init_stop_atr": 1.8,
    "trail_atr": 3.2,
    "tp1_atr": 3.5,
    "tp1_fraction": 0.50,
    "max_hold_bars": 42,
    "base_alloc": 0.85,
    "strong_alloc": 0.95,
}

class MicroSatelliteStrategy:
    """
    Micro Satellite Strategy.
    Executes short-term momentum entries and manages positions with ATR trailing stops.
    """

    def __init__(
        self,
        asset_weights: Optional[Dict[str, float]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._weights = asset_weights or {}
        self._cfg = config or DEFAULT_MICRO_CFG

        # Active position tracking state per asset symbol
        self._positions: Dict[str, Dict[str, Any]] = {}

    def export_state(self) -> Dict[str, Any]:
        return {"positions": self._positions}

    def import_state(self, state_dict: Dict[str, Any]) -> None:
        if not isinstance(state_dict, dict):
            return
        positions = state_dict.get("positions")
        if isinstance(positions, dict):
            self._positions = positions
            logger.info("Imported Micro position state: %s", list(positions.keys()))

    def compute_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        portfolio: PortfolioSnapshot,
    ) -> Any:
        """Compute target weights and signals for the Micro layer."""
        target_weights: Dict[str, float] = {}
        signals: List[StrategySignal] = []

        for symbol, candles in candles_by_asset.items():
            if not candles:
                continue

            # Ensure we only track assets we care about
            if symbol not in self._weights:
                continue

            base = symbol.split("/")[0] if "/" in symbol else symbol
            pos_state = self._positions.setdefault(
                base,
                {
                    "active": False,
                    "entry_px": 0.0,
                    "extreme_px": 0.0,
                    "entry_bar": 0,
                    "tp1_done": False,
                    "mode": "MICRO_NEUTRAL",
                    "alloc": 0.0,
                },
            )

            df = self._add_indicators(candles_to_dataframe(candles))
            if df.empty:
                continue

            latest = df.iloc[-1]
            current_bar = len(df)
            c_close = latest.Close
            c_atr = latest.ATR
            regime = latest.MicroRegime

            # Position active?
            if not pos_state["active"]:
                # Check for entry
                if regime in ("HIGH_CONVICTION_MICRO", "MICRO_TREND_ACCELERATION"):
                    alloc = self._cfg["strong_alloc"] if regime == "HIGH_CONVICTION_MICRO" else self._cfg["base_alloc"]
                    alloc = float(np.clip(alloc, 0.10, 0.98))
                    
                    pos_state["active"] = True
                    pos_state["entry_px"] = c_close
                    pos_state["extreme_px"] = c_close
                    pos_state["entry_bar"] = current_bar
                    pos_state["tp1_done"] = False
                    pos_state["mode"] = regime
                    pos_state["alloc"] = alloc

                    target_weights[symbol] = alloc * self._weights.get(symbol, 0.0)
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.ENTER,
                        asset_regime=regime,
                        target_weight=target_weights[symbol],
                        reason=f"Micro Entry: {regime}",
                    ))
                else:
                    target_weights[symbol] = 0.0
            else:
                # Manage active position
                bars_held = current_bar - pos_state["entry_bar"]
                prev_extreme = pos_state["extreme_px"]
                entry_px = pos_state["entry_px"]
                alloc = pos_state["alloc"]

                pos_state["extreme_px"] = max(prev_extreme, c_close)
                open_profit_atr = (c_close - entry_px) / max(c_atr, 1e-6)
                
                raw_trail = prev_extreme - self._cfg["trail_atr"] * c_atr
                stop_px = max(entry_px - self._cfg["init_stop_atr"] * c_atr, raw_trail)
                
                # Check TP1
                tp1_signal = False
                if not pos_state["tp1_done"] and open_profit_atr >= self._cfg["tp1_atr"]:
                    pos_state["tp1_done"] = True
                    alloc *= (1.0 - self._cfg["tp1_fraction"])
                    pos_state["alloc"] = alloc
                    tp1_signal = True

                # Check Exit Conditions
                exit_reason = None
                if c_close <= stop_px:
                    exit_reason = f"Micro Stop Hit (Hold {bars_held}b, PnL {open_profit_atr:.1f} ATR)"
                elif bars_held >= self._cfg["max_hold_bars"]:
                    exit_reason = f"Micro Time Stop (Hold {bars_held}b)"
                elif latest.EMA9 < latest.EMA21 and open_profit_atr > 0:
                    exit_reason = f"Micro Momentum Loss (EMA9 < EMA21, Hold {bars_held}b)"

                if exit_reason:
                    pos_state["active"] = False
                    target_weights[symbol] = 0.0
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.CLOSE,
                        asset_regime=pos_state["mode"],
                        target_weight=0.0,
                        reason=exit_reason,
                    ))
                else:
                    # Maintain (or reduce via TP1)
                    target_weights[symbol] = alloc * self._weights.get(symbol, 0.0)
                    if tp1_signal:
                        signals.append(StrategySignal(
                            symbol=symbol,
                            action=PositionAction.REDUCE,
                            asset_regime=pos_state["mode"],
                            target_weight=target_weights[symbol],
                            reason=f"Micro TP1 Hit (+{open_profit_atr:.1f} ATR)",
                        ))

        # Residual USDT weight for Micro Layer
        total_crypto = sum(abs(w) for w in target_weights.values())
        target_weights["USDT"] = max(0.0, 1.0 - total_crypto)
        
        from src.core.models import StrategyDecision, TargetAllocation, AssetRegime, Regime
        
        return StrategyDecision(
            regime=Regime.BULL,  # Micro doesn't have a global macro regime, just assume Bull or leave neutral
            target_allocation=TargetAllocation(
                weights=target_weights,
                regime=Regime.BULL,
                timestamp_ms=portfolio.timestamp_ms,
            ),
            signals=signals,
            timestamp_ms=portfolio.timestamp_ms,
        )

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < self._cfg["ema_macro"]:
            return df
            
        x = df.copy()
        x["EMA9"] = x.Close.ewm(span=self._cfg["ema_fast"], adjust=False).mean()
        x["EMA21"] = x.Close.ewm(span=self._cfg["ema_med"], adjust=False).mean()
        x["EMA50"] = x.Close.ewm(span=self._cfg["ema_slow"], adjust=False).mean()
        x["EMA200"] = x.Close.ewm(span=self._cfg["ema_macro"], adjust=False).mean()
        x["VolSMA20"] = x.Volume.rolling(20).mean()

        prev = x.Close.shift()
        tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
        x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

        delta = x.Close.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/9, min_periods=9).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/9, min_periods=9).mean()
        loss_clean = loss.replace(0, np.nan)
        rs = gain / loss_clean
        rsi = 100 - (100 / (1 + rs))
        fallback = pd.Series(np.where(gain > 0, 100.0, 50.0), index=gain.index)
        x["RSI"] = rsi.fillna(fallback)

        d_bars = self._cfg["donchian_micro_bars"]
        x["DonchianMicroHigh"] = x.High.rolling(d_bars).max().shift(1)
        x["Ret30D"] = (x.Close - x.Close.shift(180)) / x.Close.shift(180)

        regimes = []
        close_v = x.Close.values
        open_v = x.Open.values
        ema9_v, ema21_v, ema50_v, ema200_v = x.EMA9.values, x.EMA21.values, x.EMA50.values, x.EMA200.values
        rsi_v = x.RSI.values
        vol_v, volsma_v = x.Volume.values, x.VolSMA20.values
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
            if macro_strong and c >= donch_hi[i] and rsi_val >= self._cfg["rsi_surge_min"] and vol > volsma * self._cfg["vol_surge_mult"]:
                regimes.append("HIGH_CONVICTION_MICRO")
            elif macro_strong and (c > e9 > e21) and (c > o) and rsi_val >= 52.0 and vol > volsma * 1.3:
                regimes.append("MICRO_TREND_ACCELERATION")
            else:
                regimes.append("MICRO_NEUTRAL")

        x["MicroRegime"] = regimes
        return x.dropna(subset=["ATR", "RSI", "EMA200"])
