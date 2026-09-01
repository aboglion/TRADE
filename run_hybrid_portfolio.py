"""
RECOMMENDED HYBRID CORE-SATELLITE PORTFOLIO ENGINE (80/20)
==========================================================
Combines:
  • 80% Core Capital ($800) -> Macro Strategy (v14 / V_BEST) with TAKER_STANDARD fees
  • 20% Satellite Capital ($200) -> Calibrated Micro Strategy with TAKER_STANDARD fees

This hybrid architecture captures multi-month parabolic bull runs with pyramiding
while micro trading generates continuous yield and risk control during sideways/bear regimes.
"""

import os
import pandas as pd
import numpy as np
import engine
from calibrate_exact_micro_proportional import run_exact_proportional_backtest
from test_fractal_scaling import add_calibrated_indicators

def run_macro_with_fee(fee_side=0.00125, capital=800.0, weights=None):
    if weights is None:
        weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    orig_fee_slip = engine.FEE_SLIP
    engine.FEE_SLIP = fee_side
    try:
        from main import BEST_CFGS
        files = {'BTC': 'data/BTC_USD_4h.csv', 'ETH': 'data/ETH_USD_4h.csv', 'SOL': 'data/SOL_USD_4h.csv'}
        eqs = {}
        all_trades = []
        for name, path in files.items():
            df = engine.add_indicators(engine.load_real_data(path))
            cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
            trail = (7.5, 5.0) if name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(name)
            tr, eq, bh = engine.run_backtest(df, cfg, capital * weights[name], trail)
            eqs[name] = eq.rename(name)
            if not tr.empty:
                all_trades.append(tr.assign(asset=name))
        comb = pd.concat(eqs.values(), axis=1).ffill()
        for name in weights:
            comb[name] = comb[name].fillna(capital * weights[name])
        port_eq = comb.sum(axis=1)
        comb_tr = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        return port_eq, comb_tr
    finally:
        engine.FEE_SLIP = orig_fee_slip

def run_hybrid_engine(initial_capital=1000.0, core_ratio=0.80, weights=None):
    if weights is None:
        weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
        
    core_capital = initial_capital * core_ratio
    sat_capital = initial_capital * (1.0 - core_ratio)
    
    # 1. MACRO CORE (80% Capital) under TAKER_STANDARD fee (0.125% per side = 0.25% RT)
    macro_eq, macro_tr = run_macro_with_fee(fee_side=0.00125, capital=core_capital, weights=weights)
    
    # 2. MICRO SATELLITE (20% Capital) under TAKER_STANDARD fee (0.10% fee + 0.025% slip = 0.20% RT)
    files = {
        'BTC': 'data/BTC_USD_4h.csv',
        'ETH': 'data/ETH_USD_4h.csv',
        'SOL': 'data/SOL_USD_4h.csv'
    }
    dfs = {name: engine.load_real_data(path) for name, path in files.items()}
    
    sf = 4.0
    rsi_t = 58.0
    tm = 4.5
    
    micro_eqs = {}
    micro_all_tr = []
    
    for name, df in dfs.items():
        w = weights[name]
        x = add_calibrated_indicators(
            df,
            ema_fast=int(9 * sf),
            ema_med=int(21 * sf),
            ema_slow=int(50 * sf),
            ema_macro=int(200 * sf),
            donch_bars=int(12 * sf),
            rsi_period=int(7 * sf)
        )
        tr, eq, fees = run_exact_proportional_backtest(
            x, scale_factor=sf, rsi_sens=rsi_t, trail_mult=tm, fee_preset='TAKER_STANDARD'
        )
        micro_eqs[name] = (eq * sat_capital * w / 1000.0).rename(name)
        if not tr.empty:
            micro_all_tr.append(tr.assign(asset=name))
            
    micro_comb = pd.concat(micro_eqs.values(), axis=1).ffill()
    for name in weights:
        micro_comb[name] = micro_comb[name].fillna(sat_capital * weights[name])
    micro_eq = micro_comb.sum(axis=1)
    
    # Resample both daily and align
    macro_daily = macro_eq.resample('D').last().dropna()
    micro_daily = micro_eq.resample('D').last().dropna()
    
    common_idx = macro_daily.index.intersection(micro_daily.index)
    macro_daily = macro_daily.loc[common_idx]
    micro_daily = micro_daily.loc[common_idx]
    
    hybrid_equity = macro_daily + micro_daily
    hybrid_equity.name = 'Hybrid_Portfolio_80_20'
    
    return hybrid_equity, macro_daily, micro_daily

def main():
    print("=" * 80)
    print("🏆 RECOMMENDED HYBRID CORE-SATELLITE PORTFOLIO (80/20) AUDIT")
    print("=" * 80)
    
    hybrid_eq, macro_part, micro_part = run_hybrid_engine()
    
    cap = 1000.0
    final_val = hybrid_eq.iloc[-1]
    ret_pct = (final_val / cap - 1) * 100
    days = (hybrid_eq.index[-1] - hybrid_eq.index[0]).days
    years = days / 365.25
    cagr = ((final_val / cap) ** (1 / years) - 1) * 100
    
    cummax = hybrid_eq.cummax()
    dd = (hybrid_eq - cummax) / cummax
    max_dd = dd.min() * 100
    
    daily_ret = hybrid_eq.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(365) if daily_ret.std() > 0 else 0
    
    print(f"\n📊 HYBRID PORTFOLIO PERFORMANCE SUMMARY (FEE-INCLUDED):")
    print(f"  • Starting Capital:     ${cap:,.2f}")
    print(f"  • Final Portfolio Value: ${final_val:,.2f}")
    print(f"  • Net Return:            +{ret_pct:,.2f}%")
    print(f"  • CAGR:                  {cagr:.2f}% per year")
    print(f"  • Sharpe Ratio:          {sharpe:.2f}")
    print(f"  • Max Drawdown:          {max_dd:.2f}%")
    
    # Save hybrid equity series
    hybrid_df = pd.DataFrame({
        'Hybrid_Portfolio': hybrid_eq,
        'Macro_Part': macro_part,
        'Micro_Part': micro_part
    })
    hybrid_df.to_csv('data/hybrid_portfolio_equity.csv')
    print(f"\n💾 Saved hybrid equity data to: data/hybrid_portfolio_equity.csv")

if __name__ == '__main__':
    main()
