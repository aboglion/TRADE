"""
Dynamic Regime-Adaptive 2.0x Strategy — Live Adapter.

Wraps the backtest logic from engine.py into the IStrategy interface.
The strategy:
1. Determines macro regime (Bull/Bear) from BTC SMA-150
2. In Bull: targets 70% B&H + 30% Active (per-asset macro signals)
3. In Bear: holds USDT (spot-safe — no shorting)
4. Per-asset signals determine entry/exit within the macro framework

This module does NOT touch the exchange.  It receives candle data and
portfolio state, and outputs a StrategyDecision with target allocations.
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
    OrderIntent,
    PortfolioSnapshot,
    StrategyDecision,
    StrategySignal,
    TargetAllocation,
)
from src.strategy.indicators import add_indicators, add_micro_indicators, candles_to_dataframe
from src.utils.time_utils import utc_to_ms

logger = logging.getLogger("bot.strategy")

# ── Default per-asset configs (from engine.py lines 40-91) ───

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
    dip_tp_atr=1.8,
    dip_sl_atr=2.0,
    highvol_atr_pct=0.08,
    vol_q=0.70,
    highvol_alloc=0.0,
    base_alloc=0.0,
    strong_alloc=3.2,
    max_add_entries=2,
    pyramid_profit_r=0.6,
    pyramid_pullback_atr=1.5,
    add1_frac=0.50,
    add2_frac=0.30,
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

# Default portfolio weights
DEFAULT_WEIGHTS = {"BTC": 0.40, "ETH": 0.30, "SOL": 0.30}

WARMUP = 300


class RegimeAdaptiveStrategy(IStrategy):
    """
    Dynamic Regime-Adaptive 2.0x Strategy for live trading.

    Adapted for Spot-only operation:
    - Bull regime: Allocate to BTC/ETH/SOL per weight targets
    - Bear regime: Hold USDT (no shorting on spot)
    """

    def __init__(
        self,
        asset_weights: Optional[Dict[str, float]] = None,
        sma_regime_period: int = 150,
        bull_leverage: float = 2.0,
        asset_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._weights = asset_weights or DEFAULT_WEIGHTS
        self._sma_period = sma_regime_period
        self._bull_leverage = bull_leverage
        self._asset_configs = asset_configs or ASSET_CONFIGS

    def compute_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        portfolio: PortfolioSnapshot,
    ) -> StrategyDecision:
        """
        Compute trading signals from candle data and portfolio state.

        Args:
            candles_by_asset: {"BTC/USDT": [candle1, ...], ...}
            portfolio: Current portfolio snapshot.

        Returns:
            StrategyDecision with regime, target allocations, and signals.
        """
        now_ms = max(
            candles[-1].timestamp_ms
            for candles in candles_by_asset.values()
            if candles
        )

        # 1. Determine macro regime from BTC
        btc_key = self._find_btc_key(candles_by_asset)
        regime = self._determine_regime(candles_by_asset[btc_key])

        # 2. Compute target allocation based on regime
        target_weights = self._compute_target_weights(regime, candles_by_asset)

        target_alloc = TargetAllocation(
            weights=target_weights,
            regime=regime,
            timestamp_ms=now_ms,
        )

        # 3. Per-asset signal analysis
        signals = self._compute_asset_signals(
            candles_by_asset, regime, target_weights
        )

        decision = StrategyDecision(
            regime=regime,
            target_allocation=target_alloc,
            signals=signals,
            timestamp_ms=now_ms,
            metadata={
                "sma_period": self._sma_period,
                "bull_leverage": self._bull_leverage,
            },
        )

        logger.info(
            "Strategy decision: regime=%s, targets=%s",
            regime.value,
            {k: f"{v:.2%}" for k, v in target_weights.items()},
        )

        return decision

    def _find_btc_key(self, candles_by_asset: Dict[str, List[Candle]]) -> str:
        """Find the key for BTC candles (could be 'BTC/USDT' or 'BTC')."""
        for key in candles_by_asset:
            if "BTC" in key.upper():
                return key
        raise ValueError("BTC candles not found in candles_by_asset")

    def _determine_regime(self, btc_candles: List[Candle]) -> Regime:
        """
        Determine Bull/Bear regime from BTC SMA-150.

        Uses daily resampling of 4H candles, matching engine.py logic
        (lines 757-761).
        """
        df = candles_to_dataframe(btc_candles)

        # Resample to daily for SMA calculation (matches engine.py)
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
            "Regime detection: BTC=%.2f, SMA%d=%.2f → %s",
            latest_close,
            self._sma_period,
            latest_sma,
            regime.value,
        )

        return regime

    def _compute_target_weights(
        self,
        regime: Regime,
        candles_by_asset: Dict[str, List[Candle]],
    ) -> Dict[str, float]:
        """
        Compute target portfolio weights based on regime.

        Bull: Allocate to crypto assets per defined weights
        Bear: Hold USDT (spot-safe, no shorting)
        """
        if regime == Regime.BULL:
            # Check for pullback risk guard (engine.py lines 790-795)
            btc_key = self._find_btc_key(candles_by_asset)
            df = candles_to_dataframe(candles_by_asset[btc_key])
            daily_close = df["Close"].resample("D").last().dropna()
            ema20 = daily_close.ewm(span=20, adjust=False).mean()

            latest_close = daily_close.iloc[-1]
            latest_ema20 = ema20.iloc[-1]

            # Dynamic leverage reduction (from engine.py lines 790-795)
            under_ema = latest_close < latest_ema20

            # Check pullback from peak
            peak = daily_close.max()
            pullback = (latest_close - peak) / peak if peak > 0 else 0.0
            risk_guard = pullback < -0.08 or under_ema

            if risk_guard:
                effective_leverage = 1.0
                logger.info(
                    "Risk guard active: pullback=%.2f%%, under_ema=%s, leverage=1.0x",
                    pullback * 100, under_ema,
                )
            else:
                effective_leverage = self._bull_leverage
                logger.info("Full bull allocation: leverage=%.1fx", effective_leverage)

            # In bull: allocate to assets
            # For spot trading, leverage > 1 means allocating more aggressively
            # but we can't exceed 100% on spot, so we cap at 1.0
            total_crypto_weight = min(1.0, 0.70 * effective_leverage / 2.0 + 0.30)

            weights = {}
            for asset, base_weight in self._weights.items():
                pair = f"{asset}/USDT"
                weights[pair] = base_weight * total_crypto_weight
            weights["USDT"] = max(0.0, 1.0 - sum(weights.values()))

            return weights

        else:  # BEAR
            # Hold USDT entirely (spot-safe bear mode)
            weights = {"USDT": 1.0}
            for asset in self._weights:
                weights[f"{asset}/USDT"] = 0.0

            logger.info("Bear regime: holding 100%% USDT")
            return weights

    def _compute_asset_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        regime: Regime,
        target_weights: Dict[str, float],
    ) -> List[StrategySignal]:
        """Generate per-asset signals with regime context."""
        signals: List[StrategySignal] = []

        for symbol, candles in candles_by_asset.items():
            if not candles:
                continue

            # Get base asset name (BTC from BTC/USDT)
            base_asset = symbol.split("/")[0] if "/" in symbol else symbol
            target_weight = target_weights.get(symbol, 0.0)

            # Compute indicators for the asset
            df = candles_to_dataframe(candles)
            if len(df) < WARMUP:
                logger.warning(
                    "Insufficient data for %s (%d < %d warmup), skipping",
                    symbol, len(df), WARMUP,
                )
                continue

            df_ind = add_indicators(df)
            if df_ind.empty:
                continue

            # Current regime for this asset
            latest_regime_str = df_ind["Regime"].iloc[-1]
            asset_regime = AssetRegime(latest_regime_str)

            # Determine action
            if regime == Regime.BEAR:
                action = PositionAction.CLOSE if target_weight == 0.0 else PositionAction.HOLD
                reason = "Bear regime — exit to USDT"
            elif asset_regime in (AssetRegime.STRONG_BULL_TREND, AssetRegime.TREND):
                action = PositionAction.OPEN
                reason = f"Bull regime, asset in {asset_regime.value}"
            else:
                action = PositionAction.HOLD
                reason = f"Bull regime but asset in {asset_regime.value}"

            signals.append(StrategySignal(
                symbol=symbol,
                action=action,
                asset_regime=asset_regime,
                target_weight=target_weight,
                reason=reason,
            ))

        return signals
