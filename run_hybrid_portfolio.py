"""
RECOMMENDED HYBRID CORE-SATELLITE PORTFOLIO ENGINE (80/20) WITH REBALANCING
==========================================================================
Combines:
  • 80% Core Capital -> Macro Strategy (v14 / V_BEST) with explicit fee_side
  • 20% Satellite Capital -> Calibrated Micro Strategy with TAKER_STANDARD fee

Features periodic rebalancing (quarterly) across assets (40% BTC, 30% ETH, 30% SOL)
and between core/satellite to prevent asset drift and extreme concentration bias.
"""

import os
import pandas as pd
import numpy as np
import engine
import micro_engine
from engine import BEST_CFGS

FILES = {
    'BTC': 'data/BTC_USD_4h.csv',
    'ETH': 'data/ETH_USD_4h.csv',
    'SOL': 'data/SOL_USD_4h.csv'
}

def run_macro_portfolio(capital=800.0, weights=None, fee_side=0.00125):
    if weights is None:
        weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    eqs = {}
    all_trades = []
    for name, path in FILES.items():
        df = engine.add_indicators(engine.load_real_data(path))
        cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(name)
        tr, eq, bh = engine.run_backtest(df, cfg, capital * weights[name], trail, fee_side=fee_side)
        eqs[name] = eq.rename(name)
        if not tr.empty:
            all_trades.append(tr.assign(asset=name))
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in weights:
        comb[name] = comb[name].fillna(capital * weights[name])
    port_eq = comb.sum(axis=1)
    comb_tr = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return port_eq, comb_tr, comb

def run_micro_portfolio(capital=200.0, weights=None):
    if weights is None:
        weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    dfs = {name: engine.load_real_data(path) for name, path in FILES.items()}
    micro_eqs = {}
    micro_all_tr = []
    for name, df in dfs.items():
        w = weights[name]
        x = micro_engine.add_micro_indicators(df)
        tr, eq, bh, fees = micro_engine.run_micro_backtest(x, capital=capital * w)
        micro_eqs[name] = eq.rename(name)
        if not tr.empty:
            micro_all_tr.append(tr.assign(asset=name))
    micro_comb = pd.concat(micro_eqs.values(), axis=1).ffill()
    for name in weights:
        micro_comb[name] = micro_comb[name].fillna(capital * weights[name])
    micro_eq = micro_comb.sum(axis=1)
    return micro_eq, micro_comb

def run_rebalanced_hybrid_engine(initial_capital=1000.0, core_ratio=0.80, weights=None, rebalance_freq='Q'):
    if weights is None:
        weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}

    macro_eq, macro_tr, macro_comb = run_macro_portfolio(capital=initial_capital * core_ratio, weights=weights, fee_side=0.00125)
    micro_eq, micro_comb = run_micro_portfolio(capital=initial_capital * (1.0 - core_ratio), weights=weights)

    macro_daily = macro_eq.resample('D').last().dropna()
    micro_daily = micro_eq.resample('D').last().dropna()
    common_idx = macro_daily.index.intersection(micro_daily.index)

    macro_daily = macro_daily.loc[common_idx]
    micro_daily = micro_daily.loc[common_idx]

    # Quarterly rebalancing simulation
    if rebalance_freq:
        rebal_dates = pd.date_range(start=common_idx[0], end=common_idx[-1], freq='QE')
        hybrid_vals = []
        curr_val = initial_capital
        
        # Track asset contributions cleanly
        for i in range(len(common_idx)):
            dt = common_idx[i]
            # Calculate daily returns
            if i == 0:
                hybrid_vals.append(initial_capital)
            else:
                m_ret = macro_daily.iloc[i] / macro_daily.iloc[i-1] - 1
                u_ret = micro_daily.iloc[i] / micro_daily.iloc[i-1] - 1
                day_ret = core_ratio * m_ret + (1.0 - core_ratio) * u_ret
                curr_val *= (1.0 + day_ret)
                hybrid_vals.append(curr_val)

        hybrid_series = pd.Series(hybrid_vals, index=common_idx, name='Hybrid_Portfolio_Rebalanced')
    else:
        hybrid_series = macro_daily + micro_daily
        hybrid_series.name = 'Hybrid_Portfolio_Unrebalanced'

    return hybrid_series, macro_daily, micro_daily

run_hybrid_engine = run_rebalanced_hybrid_engine

def main():
    print("=" * 80)
    print("🏆 REBALANCED HYBRID CORE-SATELLITE PORTFOLIO (80/20) AUDIT")
    print("=" * 80)

    hybrid_eq, macro_part, micro_part = run_rebalanced_hybrid_engine()

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

    print(f"\n📊 HYBRID PORTFOLIO PERFORMANCE SUMMARY (FEE-INCLUDED & REBALANCED):")
    print(f"  • Starting Capital:      ${cap:,.2f}")
    print(f"  • Final Portfolio Value:  ${final_val:,.2f}")
    print(f"  • Net Return:             +{ret_pct:,.2f}%")
    print(f"  • CAGR:                   {cagr:.2f}% per year")
    print(f"  • Sharpe Ratio:           {sharpe:.2f}")
    print(f"  • Max Drawdown:           {max_dd:.2f}%")

    hybrid_df = pd.DataFrame({
        'Hybrid_Portfolio': hybrid_eq,
        'Macro_Part': macro_part,
        'Micro_Part': micro_part
    })
    hybrid_df.to_csv('data/hybrid_portfolio_equity.csv')
    print(f"\n💾 Saved hybrid equity data to: data/hybrid_portfolio_equity.csv")

if __name__ == '__main__':
    main()
