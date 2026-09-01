"""
OUT-OF-SAMPLE (OOS) HYBRID VS BUY & HOLD BACKTEST RUNNER
======================================================
Tests the Recommended Hybrid Core-Satellite Strategy (80/20) against
Buy & Hold (B&H) on any specified date window, including full transaction fees.
"""

import os
import pandas as pd
import numpy as np
import engine
from calibrate_exact_micro_proportional import run_exact_proportional_backtest
from test_fractal_scaling import add_calibrated_indicators
from main import BEST_CFGS

FILES = {
    'BTC': 'data/BTC_USD_4h.csv',
    'ETH': 'data/ETH_USD_4h.csv',
    'SOL': 'data/SOL_USD_4h.csv'
}
WEIGHTS = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}

def get_full_hybrid_and_bh_curves(fee_side=0.00125, initial_capital=1000.0):
    # Core Macro 80%
    orig_fee = engine.FEE_SLIP
    engine.FEE_SLIP = fee_side
    try:
        macro_eqs = {}
        for name, path in FILES.items():
            df = engine.load_real_data(path)
            df = engine.add_indicators(df)
            cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
            trail = (7.5, 5.0) if name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(name)
            tr, eq, bh = engine.run_backtest(df, cfg, initial_capital * 0.80 * WEIGHTS[name], trail)
            macro_eqs[name] = eq.rename(name)
        comb_m = pd.concat(macro_eqs.values(), axis=1).ffill()
        for name in WEIGHTS:
            comb_m[name] = comb_m[name].fillna(initial_capital * 0.80 * WEIGHTS[name])
        macro_eq = comb_m.sum(axis=1)
    finally:
        engine.FEE_SLIP = orig_fee

    # Satellite Micro 20%
    sf, rsi_t, tm = 4.0, 58.0, 4.5
    micro_eqs = {}
    for name, path in FILES.items():
        df = engine.load_real_data(path)
        x = add_calibrated_indicators(df, int(9*sf), int(21*sf), int(50*sf), int(200*sf), int(12*sf), int(7*sf))
        tr, eq, fees = run_exact_proportional_backtest(x, sf, rsi_t, tm, 'TAKER_STANDARD')
        micro_eqs[name] = (eq * initial_capital * 0.20 * WEIGHTS[name] / 1000.0).rename(name)
    comb_u = pd.concat(micro_eqs.values(), axis=1).ffill()
    for name in WEIGHTS:
        comb_u[name] = comb_u[name].fillna(initial_capital * 0.20 * WEIGHTS[name])
    micro_eq = comb_u.sum(axis=1)

    macro_daily = macro_eq.resample('D').last().dropna()
    micro_daily = micro_eq.resample('D').last().dropna()
    idx = macro_daily.index.intersection(micro_daily.index)

    hybrid_daily = macro_daily.loc[idx] + micro_daily.loc[idx]

    # Buy & Hold calculation
    bh_eqs = {}
    for name, path in FILES.items():
        df = engine.load_real_data(path)
        init_px = df['Close'].iloc[0]
        bh_asset = (initial_capital * WEIGHTS[name] * (1.0 - fee_side)) * (df['Close'] / init_px)
        bh_eqs[name] = bh_asset.rename(name)

    comb_bh = pd.concat(bh_eqs.values(), axis=1).ffill()
    bh_daily = comb_bh.sum(axis=1).resample('D').last().dropna()
    bh_daily = bh_daily.loc[idx]

    return hybrid_daily, bh_daily

def run_hybrid_oos(start_date=None, end_date=None, initial_capital=1000.0):
    hy_full, bh_full = get_full_hybrid_and_bh_curves(initial_capital=initial_capital)
    
    if start_date:
        hy_full = hy_full[hy_full.index >= start_date]
        bh_full = bh_full[bh_full.index >= start_date]
    if end_date:
        hy_full = hy_full[hy_full.index <= end_date]
        bh_full = bh_full[bh_full.index <= end_date]

    # Re-base starting equity to $1,000 for period measurement
    if not hy_full.empty:
        hy_sliced = (hy_full / hy_full.iloc[0]) * initial_capital
        bh_sliced = (bh_full / bh_full.iloc[0]) * initial_capital
    else:
        hy_sliced, bh_sliced = hy_full, bh_full

    return hy_sliced, bh_sliced

def compute_metrics(s, bh_s, cap=1000.0):
    if s.empty or bh_s.empty:
        return {'Error': 'No data in range'}
    ret = (s.iloc[-1] / cap - 1) * 100
    bh_ret = (bh_s.iloc[-1] / cap - 1) * 100
    days = (s.index[-1] - s.index[0]).days
    years = max(days / 365.25, 0.001)
    
    cagr = ((s.iloc[-1] / cap) ** (1.0 / years) - 1) * 100 if s.iloc[-1] > 0 else -100
    bh_cagr = ((bh_s.iloc[-1] / cap) ** (1.0 / years) - 1) * 100 if bh_s.iloc[-1] > 0 else -100
    
    cummax = s.cummax()
    dd = (s - cummax) / cummax
    max_dd = dd.min() * 100

    bh_cummax = bh_s.cummax()
    bh_dd = (bh_s - bh_cummax) / bh_cummax
    bh_max_dd = bh_dd.min() * 100

    d_ret = s.pct_change().dropna()
    sharpe = (d_ret.mean() / d_ret.std()) * np.sqrt(365) if (not d_ret.empty and d_ret.std() > 0) else 0
    sortino = (d_ret.mean() / d_ret[d_ret < 0].std()) * np.sqrt(365) if (not d_ret.empty and (d_ret[d_ret < 0].std() > 0)) else 0

    bh_d_ret = bh_s.pct_change().dropna()
    bh_sharpe = (bh_d_ret.mean() / bh_d_ret.std()) * np.sqrt(365) if (not bh_d_ret.empty and bh_d_ret.std() > 0) else 0

    return {
        'Period Start': str(s.index[0].date()),
        'Period End': str(s.index[-1].date()),
        'Days': days,
        'Hybrid Final ($)': float(round(s.iloc[-1], 2)),
        'Hybrid Ret (%)': float(round(ret, 2)),
        'Hybrid CAGR (%)': float(round(cagr, 2)),
        'Hybrid MaxDD (%)': float(round(max_dd, 2)),
        'Hybrid Sharpe': float(round(sharpe, 2)),
        'Hybrid Sortino': float(round(sortino, 2)),
        'B&H Final ($)': float(round(bh_s.iloc[-1], 2)),
        'B&H Ret (%)': float(round(bh_ret, 2)),
        'B&H CAGR (%)': float(round(bh_cagr, 2)),
        'B&H MaxDD (%)': float(round(bh_max_dd, 2)),
        'B&H Sharpe': float(round(bh_sharpe, 2)),
        'Alpha vs B&H (%)': float(round(ret - bh_ret, 2))
    }

if __name__ == '__main__':
    print("=" * 80)
    print("🧪 4-MONTH OUT-OF-SAMPLE (OOS) HYBRID STRATEGY AUDIT")
    print("=" * 80)
    
    # 4-month recent window (2026-04-24 to 2026-08-24)
    hy_4m, bh_4m = run_hybrid_oos(start_date='2026-04-24', end_date='2026-08-24')
    m = compute_metrics(hy_4m, bh_4m)
    for k, v in m.items():
        print(f"  • {k:<20}: {v}")
