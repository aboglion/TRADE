"""
UNIT & REGRESSION TEST SUITE FOR TRADING ENGINE
================================================
Tests data loading, RSI edge cases, indicator calculations, backtesting engine,
and dynamic 2.0x regime allocation with risk management guardrails.
"""

import pytest
import numpy as np
import pandas as pd
import engine

def test_load_real_data():
    """Verify historical data loads cleanly with proper numeric columns and sorting."""
    df = engine.load_real_data('data/BTC_USD_4h.csv')
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert all(col in df.columns for col in ['Open', 'High', 'Low', 'Close', 'Volume'])
    assert df.index.is_monotonic_increasing

def test_add_indicators_rsi_non_null():
    """Verify RSI calculation handles continuous green candles without returning NaNs."""
    df = engine.load_real_data('data/BTC_USD_4h.csv')
    x = engine.add_indicators(df)
    assert 'RSI' in x.columns
    # After warmup periods, RSI should be completely free of NaNs
    assert x['RSI'].iloc[30:].isna().sum() == 0
    assert 'Regime' in x.columns

def test_add_micro_indicators_rsi_non_null():
    """Verify micro indicators RSI handles zero-loss edge cases cleanly."""
    df = engine.load_real_data('data/BTC_USD_4h.csv')
    x = engine.add_micro_indicators(df)
    assert 'RSI' in x.columns
    assert x['RSI'].iloc[30:].isna().sum() == 0
    assert 'MicroRegime' in x.columns

def test_macro_backtest_execution():
    """Verify macro backtest runs and produces valid equity series and trade log."""
    df = engine.add_indicators(engine.load_real_data('data/BTC_USD_4h.csv'))
    cfg = engine.BEST_CFGS['BTC']
    trades_df, equity, bh = engine.run_backtest(df, cfg, capital=1000.0)
    assert isinstance(equity, pd.Series)
    assert not equity.empty
    assert equity.iloc[0] == 1000.0
    assert equity.iloc[-1] > 0
    assert isinstance(trades_df, pd.DataFrame)

def test_dynamic_adaptive_20x_engine_with_risk_guard():
    """Verify dynamic 2.0x regime engine executes with Risk Guard and yields valid metrics."""
    dyn_eq, hy_aligned, bh_aligned = engine.run_dynamic_adaptive_20x_engine(bull_leverage=2.0)
    assert isinstance(dyn_eq, pd.Series)
    assert not dyn_eq.empty
    metrics = engine.calculate_metrics(dyn_eq, pd.DataFrame(), bh_aligned)
    assert 'Return (%)' in metrics
    assert 'MaxDD (%)' in metrics
    assert metrics['Return (%)'] > 0
    assert metrics['MaxDD (%)'] < 0

def test_no_phantom_trades():
    """Regression: ensure no zero-allocation phantom trades are recorded."""
    df = engine.add_indicators(engine.load_real_data('data/BTC_USD_4h.csv'))
    cfg = engine.BEST_CFGS['BTC']
    trades_df, equity, bh = engine.run_backtest(df, cfg, capital=1000.0)
    if not trades_df.empty:
        # Every trade must have a positive allocation
        assert (trades_df['alloc'] > 0).all(), \
            f"Found {(trades_df['alloc'] <= 0).sum()} phantom trades with alloc <= 0"
        # Every trade must have an exit (except possibly the last one)
        incomplete = trades_df[trades_df['exit_date'].isna()]
        assert len(incomplete) <= 1, \
            f"Found {len(incomplete)} incomplete trades (expected at most 1 for end-of-backtest)"

def test_roundtrip_fee_accounting():
    """Verify that fees are deducted on both entry and exit for every completed trade."""
    df = engine.add_indicators(engine.load_real_data('data/BTC_USD_4h.csv'))
    cfg = engine.BEST_CFGS['BTC']
    trades_df, equity, bh = engine.run_backtest(df, cfg, capital=1000.0)
    complete = trades_df[trades_df['exit_date'].notna()]
    if not complete.empty:
        # Every completed trade must have positive fees paid
        assert (complete['fees_paid'] > 0).all(), "Some completed trades have zero fees"
        # Gross PnL should differ from Net PnL (fees are deducted)
        assert not np.allclose(complete['gross_pnl'].values, complete['net_pnl'].values), \
            "Gross and Net PnL are identical — fees not being deducted"
