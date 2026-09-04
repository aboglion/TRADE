"""
Tests for strategy — indicator computation, regime detection, position tracking, and exits.
"""

import pytest
from tests import make_candle_series, make_candle
from src.core.enums import PositionAction, Regime
from src.core.models import AssetHolding, PortfolioSnapshot
from src.strategy.indicators import add_indicators, candles_to_dataframe
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


class TestIndicatorComputation:
    def test_indicators_produce_required_columns(self):
        candles = make_candle_series(
            start_ts_ms=1_600_000_000_000,
            count=400,
            base_price=50000.0,
            trend=10.0,
        )
        df = candles_to_dataframe(candles)
        df_ind = add_indicators(df)

        required = ["EMA20", "EMA50", "EMA200", "ATR", "RSI", "ADX", "Regime", "Donchian30"]
        for col in required:
            assert col in df_ind.columns, f"Missing column: {col}"

    def test_rsi_no_nan_after_warmup(self):
        candles = make_candle_series(
            start_ts_ms=1_600_000_000_000,
            count=400,
            base_price=50000.0,
            trend=10.0,
        )
        df = candles_to_dataframe(candles)
        df_ind = add_indicators(df)

        assert df_ind["RSI"].isna().sum() == 0

    def test_regime_classification_values(self):
        candles = make_candle_series(
            start_ts_ms=1_600_000_000_000,
            count=400,
            base_price=50000.0,
            trend=10.0,
        )
        df = candles_to_dataframe(candles)
        df_ind = add_indicators(df)

        valid_regimes = {"STRONG_BULL_TREND", "TREND", "BEAR", "SIDEWAYS"}
        actual_regimes = set(df_ind["Regime"].unique())
        assert actual_regimes.issubset(valid_regimes)


class TestCanDlesToDataframe:
    def test_correct_shape(self):
        candles = make_candle_series(1_600_000_000_000, count=50)
        df = candles_to_dataframe(candles)

        assert len(df) == 50
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    def test_sorted_chronologically(self):
        candles = make_candle_series(1_600_000_000_000, count=50)
        df = candles_to_dataframe(candles)

        assert df.index.is_monotonic_increasing


class TestRegimeAdaptiveStrategyParity:
    def test_bear_regime_allocates_100_percent_usdt(self):
        strategy = RegimeAdaptiveStrategy(sma_regime_period=150, bear_short_hedge_weight=0.0)
        # Create candles where price is dropping (BEAR regime)
        btc_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=60000.0, trend=-20.0)
        candles_by_asset = {"BTC/USDT": btc_candles}
        portfolio = PortfolioSnapshot(
            timestamp_ms=1_600_000_000_000,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )

        decision = strategy.compute_signals(candles_by_asset, portfolio)
        assert decision.regime == Regime.BEAR
        assert decision.target_allocation.weights["USDT"] == 1.0
        assert decision.target_allocation.weights["BTC/USDT"] == 0.0

    def test_state_export_and_import(self):
        strategy = RegimeAdaptiveStrategy()
        initial_state = {
            "positions": {
                "BTC": {
                    "active": True,
                    "entry_px": 50000.0,
                    "atr_at_entry": 1000.0,
                    "high_water": 55000.0,
                    "mode": "STRONG_BULL_TREND",
                }
            }
        }
        strategy.import_state(initial_state)
        exported = strategy.export_state()

        assert "positions" in exported
        assert exported["positions"]["BTC"]["active"] is True
        assert exported["positions"]["BTC"]["high_water"] == 55000.0

    def test_atr_trailing_stop_trigger(self):
        strategy = RegimeAdaptiveStrategy(sma_regime_period=10)
        # 1000 candles with strong uptrend (base 20000 to 70000)
        btc_candles = make_candle_series(1_600_000_000_000, count=999, base_price=20000.0, trend=50.0)
        # Low wicks down to 68,000 (below trail stop 68093.21) while close stays high at 69,950
        last_ts = btc_candles[-1].timestamp_ms + 14_400_000
        crash_candle = make_candle(last_ts, open=69900.0, high=70000.0, low=68000.0, close=69950.0)
        btc_candles.append(crash_candle)

        # Inject active position at 68,000 with high water at 70,000
        strategy.import_state({
            "positions": {
                "BTC": {
                    "active": True,
                    "entry_px": 68000.0,
                    "atr_at_entry": 1000.0,
                    "high_water": 70000.0,
                    "mode": "STRONG_BULL_TREND",
                }
            }
        })

        portfolio = PortfolioSnapshot(
            timestamp_ms=last_ts,
            holdings={
                "USDT": AssetHolding("USDT", 200.0, 0.0, 200.0, 200.0),
                "BTC": AssetHolding("BTC", 0.01, 0.0, 0.01, 700.0),
            },
            total_value_usd=900.0,
        )

        decision = strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert decision.regime == Regime.BULL

        btc_signal = next(s for s in decision.signals if "BTC" in s.symbol)
        
        # Position should trigger CLOSE exit signal due to ATR trailing stop
        assert btc_signal.action == PositionAction.CLOSE
        assert "atr_trailing_stop" in btc_signal.reason
        assert decision.target_allocation.weights["BTC/USDT"] == 0.0

    def test_hybrid_strategy_legacy_state_import(self):
        from src.strategy.hybrid_strategy import HybridStrategy
        macro = RegimeAdaptiveStrategy()
        micro = RegimeAdaptiveStrategy()
        hybrid = HybridStrategy(macro, micro, core_ratio=0.8)

        legacy_state = {
            "positions": {
                "BTC": {
                    "active": True,
                    "entry_px": 60000.0,
                    "atr_at_entry": 800.0,
                    "high_water": 65000.0,
                    "mode": "STRONG_BULL_TREND",
                }
            },
            "bull_peak": 65000.0,
        }

        # Import legacy state where macro parameters were at the root of strategy_state
        hybrid.import_state(legacy_state)
        exported = hybrid.export_state()

        assert "macro_state" in exported
        assert exported["macro_state"]["positions"]["BTC"]["active"] is True
        assert exported["macro_state"]["bull_peak"] == 65000.0

