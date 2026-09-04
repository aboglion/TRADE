"""
Tests for strategy — indicator computation and regime detection.
"""

import pytest
from tests import make_candle_series
from src.core.models import AssetHolding, PortfolioSnapshot
from src.strategy.indicators import add_indicators, candles_to_dataframe


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
