"""
Dynamic Regime-Adaptive 2.0x Strategy — Live Adapter.

Wraps the backtest logic from BACK_TEST/engine.py into the IStrategy interface.
The strategy:
1. Determines macro regime (Bull/Bear) from BTC daily SMA-150.
2. In Bull Mode:
   - Evaluates Dynamic Bullish Risk Guard (BTC daily close < EMA20 or pullback > 8% de-leverages).
   - Evaluates per-asset entry signals (Donchian30 breakout + ADX chop gate + EMA20 re-entry).
   - Evaluates per-asset dynamic exits (ATR Trailing Stop, Initial Risk Stop, EMA50/200 breakdown).
   - Tracks per-asset position state (high_water, entry_px, atr_at_entry) across cycles.
3. In Bear Mode:
   - Holds 100% USDT (spot-safe).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.core.enums import AssetRegime, OrderSide, OrderType, PositionAction, Regime
from src.core.interfaces import IStrategy
from src.core.models import (
    Candle,
    PortfolioSnapshot,
    StrategyDecision,
    StrategySignal,
    TargetAllocation,
)
from src.strategy.indicators import add_indicators, candles_to_dataframe

logger = logging.getLogger("bot.strategy")

# ── Default per-asset configs (matching BACK_TEST/engine.py) ───

_BASE_CFG = dict(
    entry_score_min=0,
    rsi_overbought_max=100.0,
    dip_rsi_max=0.0,
    dip_vol_mult=1.5,
    vol_filter_mult=1.0,
    trend_adx_min=22.0,
    trail_base_strong=8.0,
    trail_base_trend=2.5,
    trail_max_strong=10.0,
    trail_max_trend=4.5,
    parabolic_r=3.0,
    adaptive_trail=True,
    ema_exit_strong=False,
    ema_exit_trend=True,
    tp1_enabled=False,
    tp1_trigger_atr=4.5,
    tp1_fraction=0.30,
    tp1_be_floor_atr=1.0,
    init_risk_atr=1.8,
    init_risk_modes=("STRONG_BULL_TREND", "TREND"),
    cooldown_bars=0,
    highvol_alloc=0.0,
    base_alloc=0.0,
    strong_alloc=1.5,
    max_add_entries=2,
)

_CFG_BTC = dict(
    _BASE_CFG,
    strong_alloc=1.5, trail_base_trend=5.0, trail_base_strong=10.0,
    ema20_reentry=True, reentry_lookback=16, trend_adx_min=20.0, init_risk_atr=4.0,
)
_CFG_ETH = dict(
    _BASE_CFG,
    strong_alloc=1.5, trail_base_trend=4.5, trail_base_strong=9.0,
    trend_adx_min=22.0, init_risk_atr=4.0,
)
_CFG_SOL = dict(
    _BASE_CFG,
    strong_alloc=1.5, trail_base_trend=5.0, trail_base_strong=10.0,
    trend_adx_min=24.0, init_risk_atr=3.0,
)

ASSET_CONFIGS: Dict[str, Dict[str, Any]] = {
    "BTC": _CFG_BTC,
    "ETH": _CFG_ETH,
    "SOL": _CFG_SOL,
}

TRAIL_OVERRIDES = {
    "BTC": (10.0, 5.0),
    "ETH": (9.0, 4.5),
    "SOL": (7.5, 5.0),
}

DEFAULT_WEIGHTS = {"BTC": 0.40, "ETH": 0.30, "SOL": 0.30}
MIN_REQUIRED_CANDLES = 200


class RegimeAdaptiveStrategy(IStrategy):
    """
    Dynamic Regime-Adaptive 2.0x Strategy with full BACK_TEST parity.
    Tracks active positions, per-asset entry/exit triggers, ATR trailing stops,
    and daily 150-SMA regime detection.
    """

    def __init__(
        self,
        asset_weights: Optional[Dict[str, float]] = None,
        sma_regime_period: int = 150,
        bull_leverage: float = 2.0,
        bear_short_hedge_weight: float = 0.15,
        asset_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._weights = asset_weights or DEFAULT_WEIGHTS
        self._sma_period = sma_regime_period
        self._bull_leverage = bull_leverage
        self._bear_short_hedge = bear_short_hedge_weight
        self._asset_configs = asset_configs or ASSET_CONFIGS
        self._trail_overrides = TRAIL_OVERRIDES

        # Active position tracking state per asset symbol
        # {"BTC": {"active": bool, "entry_px": float, "atr_at_entry": float, "high_water": float, "mode": str}}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._bull_peak: float = 0.0

    def export_state(self) -> Dict[str, Any]:
        """Export state for persistence in BotState.strategy_state."""
        return {
            "positions": self._positions,
            "bull_peak": self._bull_peak,
        }

    def import_state(self, state_dict: Dict[str, Any]) -> None:
        """Import position tracking state from BotState.strategy_state."""
        if not isinstance(state_dict, dict):
            return
        positions = state_dict.get("positions")
        if isinstance(positions, dict):
            self._positions = positions
            logger.info("Imported active position tracking state: %s", list(positions.keys()))
            
        self._bull_peak = state_dict.get("bull_peak", 0.0)

    def compute_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        portfolio: PortfolioSnapshot,
    ) -> StrategyDecision:
        """
        Compute trading signals from candle data and portfolio state.
        """
        valid_candles = [c for candles in candles_by_asset.values() if candles for c in candles]
        now_ms = max(c.timestamp_ms for c in valid_candles) if valid_candles else 0

        # 1. Determine macro regime from BTC daily SMA-150
        btc_key = self._find_btc_key(candles_by_asset)
        regime = self._determine_regime(candles_by_asset[btc_key])

        # 2. Compute target allocation & signals with per-asset entry/exit triggers
        target_weights, signals = self._evaluate_strategy(
            candles_by_asset=candles_by_asset,
            regime=regime,
            portfolio=portfolio,
        )

        target_alloc = TargetAllocation(
            weights=target_weights,
            regime=regime,
            timestamp_ms=now_ms,
        )

        decision = StrategyDecision(
            regime=regime,
            target_allocation=target_alloc,
            signals=signals,
            timestamp_ms=now_ms,
            metadata={
                "sma_period": self._sma_period,
                "bull_leverage": self._bull_leverage,
                "active_positions": {k: v for k, v in self._positions.items() if v.get("active")},
            },
        )

        logger.info(
            "Strategy decision: regime=%s, targets=%s",
            regime.value,
            {k: f"{v:.2%}" for k, v in target_weights.items()},
        )

        return decision

    def _find_btc_key(self, candles_by_asset: Dict[str, List[Candle]]) -> str:
        for key in candles_by_asset:
            if "BTC" in key.upper():
                return key
        raise ValueError("BTC candles not found in candles_by_asset")

    def _determine_regime(self, btc_candles: List[Candle]) -> Regime:
        """
        Determine Bull/Bear regime from BTC SMA-150.
        Uses daily resampling of 4H candles, matching engine.py (lines 757-761).
        """
        df = candles_to_dataframe(btc_candles)
        daily_close = df["Close"].resample("D").last().dropna()

        if len(daily_close) < self._sma_period:
            logger.warning(
                "Not enough daily data for SMA-%d (%d available), defaulting to BEAR",
                self._sma_period,
                len(daily_close),
            )
            return Regime.BEAR

        sma = daily_close.rolling(self._sma_period).mean()
        latest_close = daily_close.iloc[-1]
        latest_sma = sma.iloc[-1]

        if np.isnan(latest_sma):
            logger.warning("SMA is NaN, defaulting to BEAR")
            return Regime.BEAR

        regime = Regime.BULL if latest_close > latest_sma else Regime.BEAR

        logger.info(
            "Regime detection: BTC Daily Close=%.2f, SMA%d=%.2f → %s",
            latest_close,
            self._sma_period,
            latest_sma,
            regime.value,
        )

        return regime

    def _evaluate_strategy(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        regime: Regime,
        portfolio: PortfolioSnapshot,
    ) -> tuple[Dict[str, float], List[StrategySignal]]:
        """
        Evaluate full per-asset strategy matching BACK_TEST/engine.py:
        - Bullish Risk Guard (BTC daily close vs EMA20 & pullback > 8%)
        - Entry gating (Donchian30 breakout + ADX >= trend_adx_min + EMA20 re-entry)
        - Exits (ATR Trailing Stop, Initial Risk Stop, EMA50/200 breakdown)
        """
        target_weights: Dict[str, float] = {}
        signals: List[StrategySignal] = []

        if regime == Regime.BEAR:
            # Bear mode: Short hedge + Cash protection
            logger.info("Bear regime active: resetting all long positions to inactive")
            for base in list(self._positions.keys()):
                self._positions[base]["active"] = False

            # Assign short hedge to BTC (or default btc_key)
            btc_key = self._find_btc_key(candles_by_asset)
            
            for symbol in candles_by_asset:
                if symbol == btc_key and self._bear_short_hedge > 0:
                    target_weights[symbol] = -self._bear_short_hedge
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.OPEN,
                        asset_regime=AssetRegime.BEAR,
                        target_weight=-self._bear_short_hedge,
                        reason=f"Bear regime — {self._bear_short_hedge * 100:.0f}% short hedge on BTC",
                    ))
                else:
                    target_weights[symbol] = 0.0
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.CLOSE,
                        asset_regime=AssetRegime.BEAR,
                        target_weight=0.0,
                        reason="Bear regime — 100% USDT protection",
                    ))
            
            # Residual USDT weight
            total_short = sum(abs(w) for w in target_weights.values() if w < 0)
            target_weights["USDT"] = 1.0  # USDT margin handles collateral
            
            return target_weights, signals

        # ── BULL REGIME ───────────────────────────────────────
        # Check Bullish Risk Guard (engine.py lines 790-795)
        btc_key = self._find_btc_key(candles_by_asset)
        df_btc = candles_to_dataframe(candles_by_asset[btc_key])
        btc_daily = df_btc["Close"].resample("D").last().dropna()
        ema20_daily = btc_daily.ewm(span=20, adjust=False).mean()

        latest_btc = btc_daily.iloc[-1]
        latest_ema20 = ema20_daily.iloc[-1]
        
        # Update persistent bull peak
        if regime == Regime.BULL and latest_btc > self._bull_peak:
            self._bull_peak = latest_btc
            
        peak_btc = self._bull_peak if self._bull_peak > 0 else btc_daily.max()
        btc_pullback = (latest_btc - peak_btc) / peak_btc if peak_btc > 0 else 0.0
        under_ema = latest_btc < latest_ema20

        risk_guard_active = btc_pullback < -0.08 or under_ema
        effective_leverage = 1.0 if risk_guard_active else self._bull_leverage

        if risk_guard_active:
            logger.info(
                "Bullish Risk Guard ACTIVE (pullback=%.2f%%, under_ema=%s) → Effective Leverage=1.0x",
                btc_pullback * 100, under_ema,
            )
        else:
            logger.info("Full Bull Leverage active: %.1fx", effective_leverage)

        total_crypto_weight = min(1.0, 0.70 * effective_leverage / 2.0 + 0.30)

        assigned_crypto_weight = 0.0

        for symbol, candles in candles_by_asset.items():
            if not candles:
                continue

            base = symbol.split("/")[0] if "/" in symbol else symbol
            base_weight = self._weights.get(base, 0.0)
            cfg = self._asset_configs.get(base, _BASE_CFG)
            trail_override = self._trail_overrides.get(base, (10.0, 5.0))

            df = candles_to_dataframe(candles)
            if len(df) < MIN_REQUIRED_CANDLES:
                logger.warning("Insufficient candles for %s (%d < %d), skipping", symbol, len(df), MIN_REQUIRED_CANDLES)
                target_weights[symbol] = 0.0
                continue

            df_ind = add_indicators(df)
            if df_ind.empty:
                target_weights[symbol] = 0.0
                continue

            r_last = df_ind.iloc[-1]
            c_close = r_last["Close"]
            c_high = r_last["High"]
            c_low = r_last["Low"]
            c_atr = r_last["ATR"]
            c_adx = r_last["ADX"] if not np.isnan(r_last["ADX"]) else 0.0
            asset_regime_str = r_last["Regime"]
            asset_regime = AssetRegime(asset_regime_str)

            # Get current position state for this asset
            pos = self._positions.get(base, {"active": False})

            # Check cold-start adoption: if account holds asset but pos is inactive and regime is strong/trend
            current_weight = portfolio.get_weight(base)
            if not pos.get("active") and current_weight > 0.02 and asset_regime_str in ("STRONG_BULL_TREND", "TREND"):
                logger.info("Cold-start adoption: adopting existing holding for %s (weight=%.2f%%)", symbol, current_weight * 100)
                pos = {
                    "active": True,
                    "entry_px": c_close,
                    "atr_at_entry": c_atr if not np.isnan(c_atr) else 1.0,
                    "high_water": c_high,
                    "mode": asset_regime_str,
                }
                self._positions[base] = pos

            # ── ACTIVE POSITION MANAGEMENT ───────────────────
            if pos.get("active"):
                entry_px = pos["entry_px"]
                atr_entry = pos["atr_at_entry"]
                entry_mode = pos.get("mode", "STRONG_BULL_TREND")
                
                # Fetch current high water mark (do not update with c_high yet to avoid lookahead bias)
                current_high_water = pos.get("high_water", entry_px)

                open_r = (c_close - entry_px) / max(atr_entry, 1e-6)

                # Compute dynamic trailing stop
                tb = trail_override[0] if entry_mode == "STRONG_BULL_TREND" else trail_override[1]
                trail_atr = tb
                if cfg.get("adaptive_trail", True) and open_r > cfg.get("parabolic_r", 3.0):
                    tm = cfg.get("trail_max_strong", 10.0) if entry_mode == "STRONG_BULL_TREND" else cfg.get("trail_max_trend", 4.5)
                    extra = min(tm - tb, (open_r - cfg.get("parabolic_r", 3.0)) * 0.4)
                    trail_atr = tb + extra

                trail_stop = current_high_water - trail_atr * c_atr
                init_stop = entry_px - cfg.get("init_risk_atr", 1.8) * atr_entry

                # Exits evaluation
                exit_now = False
                reason = ""

                # 1. Initial Risk Stop
                if entry_mode in cfg.get("init_risk_modes", ("STRONG_BULL_TREND", "TREND")) and c_low <= init_stop:
                    exit_now = True
                    reason = f"initial_risk_stop (low {c_low:.2f} <= {init_stop:.2f})"
                # 2. ATR Trailing Stop
                elif c_low <= trail_stop:
                    exit_now = True
                    reason = f"atr_trailing_stop (low {c_low:.2f} <= {trail_stop:.2f})"
                # 3. EMA Breakdown Exits
                elif cfg.get("ema_exit_strong", False) and entry_mode == "STRONG_BULL_TREND" and c_close < r_last["EMA200"]:
                    exit_now = True
                    reason = f"ema200_bear_exit (close {c_close:.2f} < EMA200 {r_last['EMA200']:.2f})"
                elif cfg.get("ema_exit_trend", True) and entry_mode == "TREND" and c_close < r_last["EMA50"]:
                    exit_now = True
                    reason = f"ema50_trend_exit (close {c_close:.2f} < EMA50 {r_last['EMA50']:.2f})"

                if exit_now:
                    logger.info("EXIT SIGNAL triggered for %s: %s", symbol, reason)
                    pos["active"] = False
                    self._positions[base] = pos
                    target_weights[symbol] = 0.0
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.CLOSE,
                        asset_regime=asset_regime,
                        target_weight=0.0,
                        reason=reason,
                    ))
                else:
                    # Update high_water only if the position is held
                    pos["high_water"] = max(current_high_water, c_high)
                    self._positions[base] = pos
                    weight = base_weight * total_crypto_weight
                    target_weights[symbol] = weight
                    assigned_crypto_weight += weight
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.HOLD,
                        asset_regime=asset_regime,
                        target_weight=weight,
                        reason=f"Active position held (trail_stop={trail_stop:.2f})",
                    ))

            # ── NEW ENTRY EVALUATION ─────────────────────────
            else:
                entered = False
                entry_mode = None

                trend_adx_min = cfg.get("trend_adx_min", 22.0)
                donchian30 = r_last["Donchian30"]

                if asset_regime_str in ("STRONG_BULL_TREND", "TREND") and c_close >= donchian30 and c_adx >= trend_adx_min:
                    entered = True
                    entry_mode = asset_regime_str
                elif cfg.get("ema20_reentry", False) and asset_regime_str in ("STRONG_BULL_TREND", "TREND"):
                    lookback = cfg.get("reentry_lookback", 16)
                    sub = df_ind.iloc[max(0, len(df_ind) - lookback - 1) : -1]
                    if (sub["Low"] <= sub["EMA20"]).any() and c_close > r_last["EMA20"]:
                        entered = True
                        entry_mode = "EMA20_REENTRY"

                if entered:
                    logger.info(
                        "ENTRY SIGNAL triggered for %s in %s (close=%.2f >= donchian=%.2f, ADX=%.1f)",
                        symbol, entry_mode, c_close, donchian30, c_adx,
                    )
                    pos = {
                        "active": True,
                        "entry_px": c_close,
                        "atr_at_entry": c_atr if not np.isnan(c_atr) else 1.0,
                        "high_water": c_high,
                        "mode": entry_mode,
                    }
                    self._positions[base] = pos
                    weight = base_weight * total_crypto_weight
                    target_weights[symbol] = weight
                    assigned_crypto_weight += weight
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.OPEN,
                        asset_regime=asset_regime,
                        target_weight=weight,
                        reason=f"Entry trigger: {entry_mode} breakout",
                    ))
                else:
                    target_weights[symbol] = 0.0
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.HOLD,
                        asset_regime=asset_regime,
                        target_weight=0.0,
                        reason=f"No entry trigger (donchian30={donchian30:.2f}, ADX={c_adx:.1f})",
                    ))

        # Remainder held in USDT
        target_weights["USDT"] = max(0.0, 1.0 - assigned_crypto_weight)
        return target_weights, signals
