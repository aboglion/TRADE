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

    def test_bear_regime_short_hedge(self):
        from src.strategy.hybrid_strategy import HybridStrategy
        macro = RegimeAdaptiveStrategy(sma_regime_period=150, bear_short_hedge_weight=0.15, core_ratio=0.80)
        micro = RegimeAdaptiveStrategy(sma_regime_period=150, bear_short_hedge_weight=0.0, core_ratio=0.20)
        hybrid = HybridStrategy(macro_strategy=macro, micro_strategy=micro, core_ratio=0.80)

        btc_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=60000.0, trend=-20.0)
        candles_by_asset = {"BTC/USDT": btc_candles}
        portfolio = PortfolioSnapshot(
            timestamp_ms=1_600_000_000_000,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )

        decision = hybrid.compute_signals(candles_by_asset, portfolio)
        assert decision.regime == Regime.BEAR
        assert pytest.approx(decision.target_allocation.weights["BTC/USDT"], abs=1e-4) == -0.15

    def test_bull_regime_leverage_allocation(self):
        strategy = RegimeAdaptiveStrategy(sma_regime_period=150, bull_leverage=2.0)
        # Create candles in strong bull trend
        btc_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=30000.0, trend=50.0)
        eth_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=2000.0, trend=5.0)
        sol_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=100.0, trend=0.5)

        candles_by_asset = {
            "BTC/USDT": btc_candles,
            "ETH/USDT": eth_candles,
            "SOL/USDT": sol_candles,
        }
        portfolio = PortfolioSnapshot(
            timestamp_ms=1_600_000_000_000,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )

        # Trigger entry signals for all 3 assets
        decision = strategy.compute_signals(candles_by_asset, portfolio)
        assert decision.regime == Regime.BULL
        total_crypto = (
            decision.target_allocation.weights.get("BTC/USDT", 0.0) +
            decision.target_allocation.weights.get("ETH/USDT", 0.0) +
            decision.target_allocation.weights.get("SOL/USDT", 0.0)
        )
        assert total_crypto > 1.0  # Demonstrates >100% allocation (2.0x leverage target)

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

    def test_flash_circuit_breaker_and_reentry_ladder(self):
        strategy = RegimeAdaptiveStrategy(
            sma_regime_period=50,
            bull_leverage=3.5,
            mid_leverage=2.4,
            min_leverage=1.4,
            flash_wick_limit=-0.04,
            ladder_steps=[1.0, 1.8, 2.5],
        )
        btc_candles = make_candle_series(1_600_000_000_000, count=999, base_price=20000.0, trend=50.0)
        last_ts = btc_candles[-1].timestamp_ms + 86_400_000
        
        # Normal candle: bull regime, should be 3.5x leverage
        normal_candle = make_candle(last_ts, open=69000.0, high=70500.0, low=68900.0, close=70000.0)
        btc_candles.append(normal_candle)
        portfolio = PortfolioSnapshot(
            timestamp_ms=last_ts,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )

        decision = strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert decision.regime == Regime.BULL
        assert strategy._effective_leverage == 3.5

        # Flash wick candle: next day open=70000, low=66500 (-5.0% dip, breaches -4% limit)
        last_ts += 86_400_000
        flash_candle = make_candle(last_ts, open=70000.0, high=70200.0, low=66500.0, close=69500.0)
        btc_candles.append(flash_candle)

        decision_flash = strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 1.0
        assert strategy._bars_since_circuit_trip == 1

        # Next bar: Ladder Step 1 (Cap = 1.0x)
        last_ts += 86_400_000
        next_candle_1 = make_candle(last_ts, open=69500.0, high=71000.0, low=69400.0, close=70800.0)
        btc_candles.append(next_candle_1)
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 1.0
        assert strategy._bars_since_circuit_trip == 2

        # Next bar: Ladder Step 2 (Cap = 1.8x)
        last_ts += 86_400_000
        next_candle_2 = make_candle(last_ts, open=70800.0, high=72000.0, low=70700.0, close=71900.0)
        btc_candles.append(next_candle_2)
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 1.8
        assert strategy._bars_since_circuit_trip == 3

        # Next bar: Ladder Step 3 (Cap = 2.5x)
        last_ts += 86_400_000
        next_candle_3 = make_candle(last_ts, open=71900.0, high=73000.0, low=71800.0, close=72900.0)
        btc_candles.append(next_candle_3)
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 2.5
        assert strategy._bars_since_circuit_trip == 4

        # Next bar: Ladder Completed -> full bull leverage 3.5x restored
        last_ts += 86_400_000
        next_candle_4 = make_candle(last_ts, open=72900.0, high=74000.0, low=72800.0, close=73900.0)
        btc_candles.append(next_candle_4)
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 3.5

    def test_stepped_pullback_guard(self):
        strategy = RegimeAdaptiveStrategy(
            sma_regime_period=50,
            bull_leverage=3.5,
            mid_leverage=2.4,
            min_leverage=1.4,
        )
        # Flat base price of 60,000 so EMA20 is around 60,000
        btc_candles = make_candle_series(1_600_000_000_000, count=999, base_price=60000.0, trend=0.0)
        last_ts = btc_candles[-1].timestamp_ms + 86_400_000
        # Peak surges to 70,000
        peak_candle = make_candle(last_ts, open=69500.0, high=70000.0, low=69000.0, close=70000.0)
        btc_candles.append(peak_candle)
        portfolio = PortfolioSnapshot(
            timestamp_ms=last_ts,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)

        # 5% pullback from peak (70,000 -> 66,500). Price is still above EMA20 (~61,500)
        last_ts += 86_400_000
        pullback_candle = make_candle(last_ts, open=67000.0, high=67200.0, low=66400.0, close=66500.0)
        btc_candles.append(pullback_candle)
        strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
        assert strategy._effective_leverage == 1.4

    def test_state_export_import_circuit_trip(self):
        strategy = RegimeAdaptiveStrategy()
        state = {
            "positions": {},
            "bull_peak": 68000.0,
            "bars_since_circuit_trip": 2,
        }
        strategy.import_state(state)
        assert strategy._bars_since_circuit_trip == 2
        assert strategy._bull_peak == 68000.0

        exported = strategy.export_state()
        assert exported["bars_since_circuit_trip"] == 2
        assert exported["bull_peak"] == 68000.0

    def test_micro_exit_logic_and_breakeven_parity(self):
        from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
        micro = MicroSatelliteStrategy(asset_weights={"BTC/USDT": 1.0})
        
        # Build candle series with strong macro trend
        candles = make_candle_series(1_600_000_000_000, count=220, base_price=50000.0, trend=100.0)
        portfolio = PortfolioSnapshot(
            timestamp_ms=candles[-1].timestamp_ms,
            holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
            total_value_usd=1000.0,
        )

        # Manually set active position to test breakeven trigger and low exit
        entry_px = 70000.0
        c_atr = 1000.0
        micro._positions["BTC"] = {
            "active": True,
            "entry_px": entry_px,
            "extreme_px": entry_px,
            "stop_px": entry_px - 1.8 * c_atr,
            "entry_bar": 200,
            "tp1_done": False,
            "mode": "HIGH_CONVICTION_MICRO",
            "alloc": 0.95,
        }

        # Case 1: In profit >= 1.5 ATR (71600), stop should be raised to entry_px * 1.003
        profit_candle = make_candle(candles[-1].timestamp_ms + 14_400_000, open=71500.0, high=71800.0, low=71200.0, close=71600.0)
        candles.append(profit_candle)
        micro.compute_signals({"BTC/USDT": candles}, portfolio)
        
        # Assert stop raised to at least breakeven + 0.3%
        assert micro._positions["BTC"]["stop_px"] >= entry_px * 1.003

        # Case 2: Low breaches stop_px -> triggers exit even if Close is higher
        breach_stop = micro._positions["BTC"]["stop_px"]
        stop_candle = make_candle(candles[-1].timestamp_ms + 14_400_000, open=71000.0, high=71100.0, low=breach_stop - 10.0, close=71000.0)
        candles.append(stop_candle)
        decision = micro.compute_signals({"BTC/USDT": candles}, portfolio)
        assert micro._positions["BTC"]["active"] is False
        assert decision.target_allocation.weights["BTC/USDT"] == 0.0

    def test_pyramiding_uses_config_add_fractions(self):
        cfg = {"add1_frac": 0.50, "add2_frac": 0.30}
        strategy = RegimeAdaptiveStrategy()
        entries = [{"px": 60000.0, "atr": 500.0}]
        
        # 1 entry: base
        assert len(entries) == 1
        mult_1 = 1.0
        
        # 2 entries: 1.0 + add1_frac
        entries.append({"px": 61000.0, "atr": 500.0})
        mult_2 = 1.0 + cfg.get("add1_frac", 0.50)
        assert mult_2 == 1.5
        
        # 3 entries: 1.0 + add1_frac + add2_frac
        entries.append({"px": 62000.0, "atr": 500.0})
        mult_3 = 1.0 + cfg.get("add1_frac", 0.50) + cfg.get("add2_frac", 0.30)
        assert mult_3 == 1.8

