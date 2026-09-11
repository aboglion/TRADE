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
from typing import Any

import numpy as np
import pandas as pd

from src.core.enums import AssetRegime, PositionAction, Regime
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

_BASE_CFG = {
    "entry_score_min": 0,
    "rsi_overbought_max": 100.0,
    "dip_rsi_max": 0.0,
    "dip_vol_mult": 1.5,
    "vol_filter_mult": 1.0,
    "trend_adx_min": 22.0,
    "trail_base_strong": 8.0,
    "trail_base_trend": 2.5,
    "trail_max_strong": 10.0,
    "trail_max_trend": 4.5,
    "parabolic_r": 3.0,
    "adaptive_trail": True,
    "ema_exit_strong": False,
    "ema_exit_trend": True,
    "tp1_enabled": False,
    "tp1_trigger_atr": 4.5,
    "tp1_fraction": 0.30,
    "tp1_be_floor_atr": 1.0,
    "init_risk_atr": 1.8,
    "init_risk_modes": ("STRONG_BULL_TREND", "TREND"),
    "cooldown_bars": 0,
    "highvol_alloc": 0.0,
    "base_alloc": 0.0,
    "strong_alloc": 1.5,
    "max_add_entries": 2,
    "pyramid_profit_r": 0.6,
    "pyramid_pullback_atr": 1.5,
    "add1_frac": 0.50,
    "add2_frac": 0.30,
}

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

ASSET_CONFIGS: dict[str, dict[str, Any]] = {
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
        asset_weights: dict[str, float] | None = None,
        sma_regime_period: int = 150,
        conviction_leverage: float | None = None,
        bull_leverage: float = 10.0,
        mid_leverage: float = 5.0,
        base_leverage: float = 2.5,
        min_leverage: float = 1.0,
        momentum_cutoff_pct: float | None = None,
        safe_cash_weight: float = 0.60,
        safe_spot_weight: float = 0.30,
        safe_micro_weight: float = 0.10,
        flash_wick_limit: float = -0.038,
        ladder_steps: list[float] | None = None,
        bear_short_hedge_weight: float = 0.35,
        short_leverage: float | None = None,
        asset_configs: dict[str, dict[str, Any]] | None = None,
        core_ratio: float = 0.80,
    ):
        self._weights = asset_weights or DEFAULT_WEIGHTS
        self._sma_period = sma_regime_period
        self._bull_leverage = bull_leverage
        self._conviction_leverage = conviction_leverage if conviction_leverage is not None else bull_leverage
        self._mid_leverage = mid_leverage
        self._base_leverage = base_leverage
        self._min_leverage = min_leverage
        self._momentum_cutoff_pct = momentum_cutoff_pct
        self._safe_cash_weight = safe_cash_weight
        self._safe_spot_weight = safe_spot_weight
        self._safe_micro_weight = safe_micro_weight
        self._flash_wick_limit = flash_wick_limit
        self._ladder_steps = ladder_steps if ladder_steps is not None else [1.0, 2.0, 4.0, 10.0]
        self._bear_short_hedge = bear_short_hedge_weight
        self._short_leverage = short_leverage if short_leverage is not None else 1.0
        self._asset_configs = asset_configs or ASSET_CONFIGS
        self._trail_overrides = TRAIL_OVERRIDES
        self._core_ratio = max(0.01, min(1.0, core_ratio))

        # Active position tracking state per asset symbol
        # {"BTC": {"active": bool, "entry_px": float, "atr_at_entry": float, "high_water": float, "mode": str}}
        self._positions: dict[str, dict[str, Any]] = {}
        self._bull_peak: float = 0.0
        self._bars_since_circuit_trip: int = 999
        self._effective_leverage: float = 1.0
        self._in_momentum: bool = False
        self._safe_haven_active: bool = False
        self._dist_from_5d_high_pct: float = 0.0
        self._prev_regime: Regime | None = None
        self._prev_safe_haven: bool | None = None

    def export_state(self) -> dict[str, Any]:
        """Export state for persistence in BotState.strategy_state."""
        return {
            "positions": self._positions,
            "bull_peak": self._bull_peak,
            "bars_since_circuit_trip": self._bars_since_circuit_trip,
            "effective_leverage": self._effective_leverage,
            "in_momentum": self._in_momentum,
            "safe_haven_active": self._safe_haven_active,
            "dist_from_5d_high_pct": self._dist_from_5d_high_pct,
        }

    def import_state(self, state_dict: dict[str, Any]) -> None:
        """Import position tracking state from BotState.strategy_state."""
        if not isinstance(state_dict, dict):
            return
        positions = state_dict.get("positions")
        if isinstance(positions, dict):
            self._positions = positions
            logger.debug("Imported active position tracking state: %s", list(positions.keys()))
            
        val_bull_peak = state_dict.get("bull_peak")
        self._bull_peak = float(val_bull_peak) if val_bull_peak is not None else 0.0

        val_bars = state_dict.get("bars_since_circuit_trip")
        self._bars_since_circuit_trip = int(val_bars) if val_bars is not None else 999

        val_lev = state_dict.get("effective_leverage")
        self._effective_leverage = float(val_lev) if val_lev is not None else 1.0

        self._in_momentum = bool(state_dict.get("in_momentum", False))
        self._safe_haven_active = bool(state_dict.get("safe_haven_active", False))

        val_dist = state_dict.get("dist_from_5d_high_pct")
        self._dist_from_5d_high_pct = float(val_dist) if val_dist is not None else 0.0

    def compute_signals(
        self,
        candles_by_asset: dict[str, list[Candle]],
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
        target_weights, signals, effective_regime = self._evaluate_strategy(
            candles_by_asset=candles_by_asset,
            regime=regime,
            portfolio=portfolio,
        )

        target_alloc = TargetAllocation(
            weights=target_weights,
            regime=effective_regime,
            timestamp_ms=now_ms,
            leverage=self._effective_leverage,
            metadata={
                "short_leverage": self._short_leverage,
                "effective_leverage": self._effective_leverage,
            },
        )

        decision = StrategyDecision(
            regime=effective_regime,
            target_allocation=target_alloc,
            signals=signals,
            timestamp_ms=now_ms,
            metadata={
                "sma_period": self._sma_period,
                "conviction_leverage": self._conviction_leverage,
                "bull_leverage": self._bull_leverage,
                "mid_leverage": self._mid_leverage,
                "base_leverage": self._base_leverage,
                "min_leverage": self._min_leverage,
                "effective_leverage": self._effective_leverage,
                "in_momentum": self._in_momentum,
                "safe_haven_active": self._safe_haven_active,
                "dist_from_5d_high_pct": self._dist_from_5d_high_pct,
                "momentum_cutoff_pct": self._momentum_cutoff_pct,
                "safe_cash_weight": self._safe_cash_weight,
                "safe_spot_weight": self._safe_spot_weight,
                "bars_since_circuit_trip": self._bars_since_circuit_trip,
                "short_leverage": self._short_leverage,
                "active_positions": {k: v for k, v in self._positions.items() if v.get("active")},
            },
        )

        logger.debug(
            "Strategy decision: regime=%s | Lev: %.1fx | Momentum: %s | SafeHaven: %s (5d_PB: %.2f%%) | targets=%s",
            regime.value,
            self._effective_leverage,
            self._in_momentum,
            self._safe_haven_active,
            self._dist_from_5d_high_pct,
            {k: f"{v:.2%}" for k, v in target_weights.items()},
        )

        return decision

    def _find_btc_key(self, candles_by_asset: dict[str, list[Candle]]) -> str:
        for key in candles_by_asset:
            if "BTC" in key.upper():
                return key
        raise ValueError("BTC candles not found in candles_by_asset")

    def _determine_regime(self, btc_candles: list[Candle]) -> Regime:
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

        if self._prev_regime is not None and self._prev_regime != regime:
            logger.info(
                "🔄 REGIME TRANSITION: %s → %s (BTC Daily Close=%.2f vs SMA%d=%.2f)",
                self._prev_regime.value.upper(),
                regime.value.upper(),
                latest_close,
                self._sma_period,
                latest_sma,
            )
        else:
            logger.debug(
                "Regime detection: BTC Daily Close=%.2f, SMA%d=%.2f → %s",
                latest_close,
                self._sma_period,
                latest_sma,
                regime.value,
            )
        self._prev_regime = regime

        return regime

    def _evaluate_strategy(
        self,
        candles_by_asset: dict[str, list[Candle]],
        regime: Regime,
        portfolio: PortfolioSnapshot,
    ) -> tuple[dict[str, float], list[StrategySignal], Regime]:
        """
        Evaluate full per-asset strategy matching BACK_TEST/engine.py:
        - Bullish Risk Guard (BTC daily close vs EMA20 & pullback > 8%)
        - Entry gating (Donchian30 breakout + ADX >= trend_adx_min + EMA20 re-entry)
        - Exits (ATR Trailing Stop, Initial Risk Stop, EMA50/200 breakdown)
        """
        target_weights: dict[str, float] = {}
        signals: list[StrategySignal] = []

        # 1. Indicators for Macro / Tactical Regime & Crash Shield
        btc_key = self._find_btc_key(candles_by_asset)
        df_btc = candles_to_dataframe(candles_by_asset[btc_key])
        btc_daily = df_btc["Close"].resample("D").last().dropna()
        btc_high = df_btc["High"].resample("D").max().dropna()
        btc_low = df_btc["Low"].resample("D").min().dropna()
        btc_open = df_btc["Open"].resample("D").first().dropna()

        ema9_daily = btc_daily.ewm(span=9, adjust=False).mean()
        ema20_daily = btc_daily.ewm(span=20, adjust=False).mean()
        ema50_daily = btc_daily.ewm(span=50, adjust=False).mean()
        sma150_daily = btc_daily.rolling(self._sma_period).mean()

        if btc_daily.empty:
            logger.error("No daily BTC candles available for strategy evaluation")
            for symbol in candles_by_asset:
                target_weights[symbol] = 0.0
            target_weights["USDT"] = 1.0
            return target_weights, signals, Regime.BEAR

        latest_btc = btc_daily.iloc[-1]
        latest_ema9 = ema9_daily.dropna().iloc[-1] if not ema9_daily.dropna().empty else latest_btc
        latest_ema20 = ema20_daily.dropna().iloc[-1] if not ema20_daily.dropna().empty else latest_btc
        latest_ema50 = ema50_daily.dropna().iloc[-1] if not ema50_daily.dropna().empty else latest_btc
        latest_sma150 = sma150_daily.dropna().iloc[-1] if not sma150_daily.dropna().empty else latest_btc

        # 5-day rolling high of BTC
        btc_5d_high = btc_high.iloc[-5:].max() if len(btc_high) >= 5 else latest_btc
        btc_pb_from_5d = (latest_btc - btc_5d_high) / btc_5d_high if btc_5d_high > 0 else 0.0
        self._dist_from_5d_high_pct = btc_pb_from_5d * 100.0

        # Daily ATR% for volatility-scaled leverage
        tr1 = btc_high - btc_low
        tr2 = (btc_high - btc_daily.shift(1)).abs()
        tr3 = (btc_low - btc_daily.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(window=14).mean()
        atr_pct = atr14 / btc_daily
        latest_atr_pct = atr_pct.dropna().iloc[-1] if not atr_pct.dropna().empty else 0.035

        # Intraday drop from open (flash dip)
        intraday_max_dip = (btc_low - btc_open) / btc_open
        latest_dip = intraday_max_dip.dropna().iloc[-1] if not intraday_max_dip.dropna().empty else 0.0

        # Daily ADX on BTC for trend strength conviction
        high_diff = btc_high.diff()
        low_diff = -btc_low.diff()
        pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
        neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
        atr_safe = atr14.replace(0, np.nan)
        pos_di = (100 * pd.Series(pos_dm, index=btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
        neg_di = (100 * pd.Series(neg_dm, index=btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
        denom = (pos_di + neg_di).replace(0, np.nan)
        dx = (100 * (pos_di - neg_di).abs() / denom).fillna(0.0)
        adx_daily = dx.ewm(alpha=1/14, min_periods=14).mean()
        latest_adx = adx_daily.dropna().iloc[-1] if not adx_daily.dropna().empty else 20.0

        # Macro & Tactical Bear Detection (Parity with engine.py lines 827-828):
        # Bear regime triggers if BTC < SMA150 OR fast breakdown (BTC < EMA20 and EMA20 < EMA50)
        is_fast_bear_breakdown = (latest_btc < latest_ema20 < latest_ema50)
        is_bear = (latest_btc < latest_sma150) or is_fast_bear_breakdown or (regime == Regime.BEAR)
        is_bullish = (latest_btc >= latest_ema20) and not is_bear

        # ── 1. BEAR REGIME (Systematic Short Hedge) ───────────
        if is_bear:
            if self._prev_regime != Regime.BEAR:
                logger.info(
                    "🐻 BEAR REGIME ACTIVATED: %s. %.0f%% @ %.1fx Short BTC hedge active.",
                    "Fast Breakdown (BTC < EMA20 & EMA20 < EMA50)" if is_fast_bear_breakdown else f"Macro Bear (BTC < SMA{self._sma_period})",
                    self._bear_short_hedge * 100,
                    self._short_leverage,
                )
            else:
                logger.debug(
                    "🐻 BEAR REGIME ACTIVE: %s. %.0f%% @ %.1fx Short BTC hedge active.",
                    "Fast Breakdown (BTC < EMA20 & EMA20 < EMA50)" if is_fast_bear_breakdown else f"Macro Bear (BTC < SMA{self._sma_period})",
                    self._bear_short_hedge * 100,
                    self._short_leverage,
                )
            self._prev_regime = Regime.BEAR
            self._bull_peak = 0.0
            self._bars_since_circuit_trip = 999
            self._effective_leverage = 0.0
            self._in_momentum = False
            self._safe_haven_active = False
            for base in list(self._positions.keys()):
                self._positions[base]["active"] = False

            # Assign 35% margin @ 2.0x short = 70% notional short hedge on BTC
            target_short = -(self._bear_short_hedge * self._short_leverage)
            
            for symbol in candles_by_asset:
                if symbol == btc_key and self._bear_short_hedge > 0:
                    target_weights[symbol] = target_short
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.OPEN,
                        asset_regime=AssetRegime.BEAR,
                        target_weight=target_short,
                        reason=f"Bear regime — {self._bear_short_hedge*100:.0f}% @ {self._short_leverage:.1f}x short hedge on BTC",
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
            
            target_weights["USDT"] = max(0.0, 1.0 - self._bear_short_hedge if self._bear_short_hedge > 0 else 1.0)
            return target_weights, signals, Regime.BEAR

        # ── 2. BULL / PULLBACK REGIME ────────────────────────
        # Update persistent bull peak
        self._bull_peak = max(self._bull_peak, latest_btc)
            
        peak_btc = self._bull_peak if self._bull_peak > 0 else latest_btc

        # Pullback from persistent ATH
        pullback_from_peak = (latest_btc - peak_btc) / peak_btc if peak_btc > 0 else 0.0

        # MOMENTUM VALIDATION GATE (Institutional Crash Shield)
        lev_reason = "Default"
        if self._momentum_cutoff_pct is not None:
            in_momentum = bool(is_bullish and (btc_pb_from_5d >= self._momentum_cutoff_pct) and (latest_btc >= latest_ema9))
            self._in_momentum = in_momentum

            if not in_momentum:
                # 🛡️ SAFE HAVEN CRASH SHIELD ACTIVE!
                self._safe_haven_active = True
                selected_lev = 1.0
                lev_reason = f"🛡️ Crash Shield Safe Haven (5d_pb={btc_pb_from_5d*100:.2f}%, BTC=${latest_btc:,.0f} vs EMA9=${latest_ema9:,.0f})"
                if self._prev_safe_haven is not True:
                    logger.info(
                        "🛡️ CRASH SHIELD ACTIVATED: BTC pullback %.2f%% (cutoff=%.2f%%) or Close < EMA9 ($%.2f < $%.2f). De-leveraging to Safe Haven (60%% Cash, 30%% Spot, 10%% Micro). Capital 100%% protected!",
                        btc_pb_from_5d * 100, self._momentum_cutoff_pct * 100, latest_btc, latest_ema9,
                    )
                else:
                    logger.debug(
                        "🛡️ CRASH SHIELD ACTIVE: BTC pullback %.2f%% (cutoff=%.2f%%) or Close < EMA9 ($%.2f < $%.2f). De-leveraging to Safe Haven.",
                        btc_pb_from_5d * 100, self._momentum_cutoff_pct * 100, latest_btc, latest_ema9,
                    )
                self._prev_safe_haven = True
                total_crypto_weight = self._safe_spot_weight
            else:
                # 🚀 FULL CONVICTION LEVERAGE ENGINE ACTIVE!
                if self._prev_safe_haven is True:
                    logger.info(
                        "🚀 CRASH SHIELD DEACTIVATED: Momentum recovered (5d_pb=%.2f%%, BTC=$%s vs EMA9=$%s). Resuming dynamic leverage.",
                        btc_pb_from_5d * 100, f"{latest_btc:,.0f}", f"{latest_ema9:,.0f}",
                    )
                self._safe_haven_active = False
                self._prev_safe_haven = False
                if latest_atr_pct < 0.022 and latest_adx >= 24.0:
                    selected_lev = self._conviction_leverage # 10.0x Conviction Rocket!
                    lev_reason = f"🚀 Conviction Rocket 10x (ATR%={latest_atr_pct*100:.2f}%, ADX={latest_adx:.1f})"
                elif latest_atr_pct < 0.028:
                    selected_lev = self._mid_leverage # 5.0x
                    lev_reason = f"Mid Volatility Tier (ATR%={latest_atr_pct*100:.2f}%)"
                else:
                    selected_lev = self._base_leverage # 2.5x
                    lev_reason = f"Base Bull Tier (ATR%={latest_atr_pct*100:.2f}%)"
                total_crypto_weight = 0.70 * selected_lev + 0.30
        else:
            # Legacy stepped guard if momentum cutoff not configured
            hard_risk = (pullback_from_peak < -0.08 or latest_btc < latest_ema20)
            stepped_pullback = (-0.08 <= pullback_from_peak < -0.04)
            if hard_risk:
                self._safe_haven_active = True
                selected_lev = 1.0
                lev_reason = f"Hard Risk Guard (1.0x, pb={pullback_from_peak*100:.1f}%)"
                total_crypto_weight = self._safe_spot_weight
            elif stepped_pullback:
                self._safe_haven_active = False
                selected_lev = self._min_leverage
                lev_reason = f"Stepped Pullback Guard ({selected_lev:.1f}x, pb={pullback_from_peak*100:.1f}%)"
                total_crypto_weight = 0.70 * selected_lev + 0.30
            else:
                self._safe_haven_active = False
                if latest_atr_pct < 0.024:
                    selected_lev = self._bull_leverage
                    lev_reason = f"Low Vol Tier ({selected_lev:.1f}x)"
                elif latest_atr_pct < 0.036:
                    selected_lev = self._mid_leverage
                    lev_reason = f"Mid Vol Tier ({selected_lev:.1f}x)"
                else:
                    selected_lev = self._min_leverage
                    lev_reason = f"High Vol Tier ({selected_lev:.1f}x)"
                total_crypto_weight = 0.70 * selected_lev + 0.30
            self._in_momentum = (selected_lev > 1.0)

        # Re-entry ladder capping after circuit trip
        if (
            self._ladder_steps
            and not self._safe_haven_active
            and 1 <= self._bars_since_circuit_trip <= len(self._ladder_steps)
        ):
            ladder_cap = self._ladder_steps[self._bars_since_circuit_trip - 1]
            if selected_lev > ladder_cap:
                selected_lev = ladder_cap
                lev_reason += f" | Re-Entry Ladder step {self._bars_since_circuit_trip} (cap={ladder_cap:.1f}x)"

        # Flash circuit breaker override
        if not self._safe_haven_active:
            if selected_lev > 1.0 and latest_dip < self._flash_wick_limit:
                self._bars_since_circuit_trip = 1
                selected_lev = 1.0
                logger.warning(
                    "⚡ FLASH CIRCUIT BREAKER TRIGGERED: intraday dip=%.2f%% < limit=%.2f%% → Cut to 1.0x Spot",
                    latest_dip * 100, self._flash_wick_limit * 100,
                )
                lev_reason = f"Flash Circuit Breaker (dip={latest_dip*100:.1f}%)"
                # Flash CB should actually de-lever to spot-only (same as safe_haven)
                total_crypto_weight = self._safe_spot_weight
            else:
                self._bars_since_circuit_trip += 1
                total_crypto_weight = 0.70 * selected_lev + 0.30

        effective_leverage = selected_lev
        self._effective_leverage = effective_leverage
        logger.debug(
            "Tactical leverage evaluation: effective=%.1fx | safe_haven=%s | in_momentum=%s (%s)",
            effective_leverage, self._safe_haven_active, self._in_momentum, lev_reason,
        )

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
                holding_obj = portfolio.holdings.get(base)
                real_entry_px = holding_obj.entry_price if (holding_obj and holding_obj.entry_price > 0) else c_close
                logger.info("Cold-start adoption: adopting existing holding for %s (weight=%.2f%%, entry_px=%.2f)", symbol, current_weight * 100, real_entry_px)
                pos = {
                    "active": True,
                    "entry_px": real_entry_px,
                    "atr_at_entry": c_atr if not np.isnan(c_atr) else 1.0,
                    "high_water": max(c_high, real_entry_px),
                    "mode": asset_regime_str,
                    "entries": [{"px": real_entry_px, "atr": c_atr if not np.isnan(c_atr) else 1.0}],
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

                # Check Pyramiding (Adding to position in STRONG_BULL_TREND)
                entries = pos.setdefault("entries", [{"px": entry_px, "atr": atr_entry}])
                max_adds = cfg.get("max_add_entries", 2)
                pyramid_profit_r = cfg.get("pyramid_profit_r", 0.6)
                pyramid_pullback_atr = cfg.get("pyramid_pullback_atr", 1.5)

                if (
                    not self._safe_haven_active
                    and len(entries) < max_adds + 1
                    and entry_mode == "STRONG_BULL_TREND"
                    and open_r >= pyramid_profit_r
                ):
                    # Pullback from the last entry price (matching engine.py line 365-366)
                    last_px = entries[-1]["px"]
                    pullback = (last_px - c_low) / max(c_atr, 1e-6)
                    if pullback >= pyramid_pullback_atr and c_close > r_last["EMA20"]:
                        entries.append({"px": c_close, "atr": c_atr})
                        pos["entries"] = entries
                        logger.info(
                            "PYRAMID ADD TRIGGERED for %s (entry #%d @ %.2f, pullback=%.1f ATR)",
                            symbol, len(entries), c_close, pullback
                        )

                # Pyramiding weight multiplier using config add1_frac and add2_frac
                add1_frac = cfg.get("add1_frac", 0.50)
                add2_frac = cfg.get("add2_frac", 0.30)
                pyramid_mult = 1.0
                if not self._safe_haven_active:
                    if len(entries) == 2:
                        pyramid_mult = 1.0 + add1_frac
                    elif len(entries) >= 3:
                        pyramid_mult = 1.0 + add1_frac + add2_frac

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
                    pos["entries"] = []
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
                    weight = base_weight * total_crypto_weight * pyramid_mult
                    target_weights[symbol] = weight
                    assigned_crypto_weight += weight
                    signals.append(StrategySignal(
                        symbol=symbol,
                        action=PositionAction.HOLD,
                        asset_regime=asset_regime,
                        target_weight=weight,
                        reason=f"Active position held (entries={len(entries)}, trail_stop={trail_stop:.2f})",
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
                        "entries": [{"px": c_close, "atr": c_atr if not np.isnan(c_atr) else 1.0}],
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
        return target_weights, signals, Regime.BULL
