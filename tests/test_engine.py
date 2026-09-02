import pytest
import pandas as pd
import numpy as np
import engine

def test_calculate_metrics_positive():
    dates = pd.date_range('2023-01-01', periods=100, freq='D')
    eq_values = np.linspace(1000, 1500, 100)
    equity = pd.Series(eq_values, index=dates)
    trades = pd.DataFrame([
        {'return_pct': 5.0, 'pnl_usd': 50.0},
        {'return_pct': -2.0, 'pnl_usd': -20.0},
        {'return_pct': 8.0, 'pnl_usd': 80.0}
    ])
    
    m = engine.calculate_metrics(equity, trades, periods_per_year=365)
    assert m['Return (%)'] == 50.0
    assert not np.isnan(m['CAGR (%)'])
    assert m['CAGR (%)'] > 0
    assert m['Trades'] == 3
    assert m['WinRate (%)'] == pytest.approx(66.7, 0.1)

def test_calculate_metrics_negative_cagr():
    dates = pd.date_range('2023-01-01', periods=365, freq='D')
    eq_values = np.linspace(1000, 800, 365) # 20% loss over 1 year
    equity = pd.Series(eq_values, index=dates)
    trades = pd.DataFrame([
        {'return_pct': -20.0, 'pnl_usd': -200.0}
    ])
    
    m = engine.calculate_metrics(equity, trades, periods_per_year=365)
    assert m['Return (%)'] == -20.0
    assert not np.isnan(m['CAGR (%)'])
    assert m['CAGR (%)'] < 0

def test_gap_down_stop_execution():
    # Construct a synthetic DataFrame with >= 350 rows (> engine.WARMUP=300)
    dates = pd.date_range('2023-01-01', periods=50, freq='4h')
    df = pd.DataFrame({
        'Open': [100.0] * 50,
        'High': [102.0] * 50,
        'Low': [98.0] * 50,
        'Close': [101.0] * 50,
        'Volume': [1000.0] * 50,
        'ATR': [2.0] * 50,
        'Regime': ['STRONG_BULL'] * 50,
        'RSI': [50.0] * 50,
        'ADX': [30.0] * 50,
        'Donchian30': [99.0] * 50,
        'Donchian30Low': [90.0] * 50,
        'VolSMA20': [500.0] * 50,
        'EMA20': [95.0] * 50,
        'EMA50': [90.0] * 50,
        'EMA200': [80.0] * 50,
    }, index=dates)
    
    # 8 * 50 = 400 rows
    warmup_df = pd.concat([df] * 8, ignore_index=True)
    warmup_df.index = pd.date_range('2023-01-01', periods=len(warmup_df), freq='4h')

    # Force a gap-down bar
    warmup_df.iloc[-5, warmup_df.columns.get_loc('Open')] = 70.0  # Massive gap down
    warmup_df.iloc[-5, warmup_df.columns.get_loc('Low')] = 65.0
    warmup_df.iloc[-5, warmup_df.columns.get_loc('Close')] = 72.0

    cfg = engine.make_cfg(entry_score_min=0)
    tr, eq, bh = engine.run_backtest(warmup_df, cfg, capital=1000.0, fee_side=0.00125)
    assert not eq.empty

def test_run_dynamic_adaptive_20x_engine():
    dyn_eq, hy_aligned, bh_aligned = engine.run_dynamic_adaptive_20x_engine(initial_capital=1000.0, bull_leverage=2.0)
    assert not dyn_eq.empty
    assert len(dyn_eq) > 100
    assert dyn_eq.iloc[-1] > 0
    assert not dyn_eq.isna().any()

