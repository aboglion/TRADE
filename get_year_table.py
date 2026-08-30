import pandas as pd
import numpy as np
from V_BEST import run_best, run_portfolio, BEST_CFGS
from v14 import load_real_data, add_indicators, run_backtest, TRAIL_OVERRIDES_V14

def get_portfolio_yearly():
    files = {'BTC': 'BTC_USD_4h.csv', 'ETH': 'ETH_USD_4h.csv', 'SOL': 'SOL_USD_4h.csv'}
    weights = {'BTC': 0.5, 'ETH': 0.3, 'SOL': 0.2}
    eqs = {}
    bhs = {}
    for name, f in files.items():
        tr, eq, bh = run_best(f, 1000.0 * weights[name])
        eqs[name] = eq.rename(name)
        bhs[name] = bh.rename(name)
    
    comb = pd.concat(eqs.values(), axis=1).ffill()
    comb_bh = pd.concat(bhs.values(), axis=1).ffill()
    for n in weights:
        comb[n] = comb[n].fillna(1000.0 * weights[n])
        comb_bh[n] = comb_bh[n].fillna(1000.0 * weights[n])
        
    port_eq = comb.sum(axis=1)
    port_bh = comb_bh.sum(axis=1)
    
    # Yearly breakdown
    d = pd.DataFrame({'Strategy': port_eq, 'BH': port_bh})
    d['Year'] = d.index.year
    
    rows = []
    for y, g in d.groupby('Year'):
        strat_start, strat_end = g.Strategy.iloc[0], g.Strategy.iloc[-1]
        bh_start, bh_end = g.BH.iloc[0], g.BH.iloc[-1]
        strat_ret = (strat_end / strat_start - 1) * 100
        bh_ret = (bh_end / bh_start - 1) * 100
        strat_dd = ((g.Strategy - g.Strategy.cummax()) / g.Strategy.cummax()).min() * 100
        bh_dd = ((g.BH - g.BH.cummax()) / g.BH.cummax()).min() * 100
        alpha = strat_ret - bh_ret
        rows.append({
            'Year': y,
            'Strat_Ret': round(strat_ret, 1),
            'BH_Ret': round(bh_ret, 1),
            'Alpha': round(alpha, 1),
            'Strat_DD': round(strat_dd, 1),
            'BH_DD': round(bh_dd, 1)
        })
    
    df_years = pd.DataFrame(rows)
    print("--- PORTFOLIO YEARLY BREAKDOWN ---")
    print(df_years.to_string(index=False))

def get_asset_yearly():
    files = {'BTC': 'BTC_USD_4h.csv', 'ETH': 'ETH_USD_4h.csv', 'SOL': 'SOL_USD_4h.csv'}
    for name, f in files.items():
        tr, eq, bh = run_best(f, 1000.0)
        d = pd.DataFrame({'Strategy': eq, 'BH': bh})
        d['Year'] = d.index.year
        rows = []
        for y, g in d.groupby('Year'):
            strat_ret = (g.Strategy.iloc[-1] / g.Strategy.iloc[0] - 1) * 100
            bh_ret = (g.BH.iloc[-1] / g.BH.iloc[0] - 1) * 100
            strat_dd = ((g.Strategy - g.Strategy.cummax()) / g.Strategy.cummax()).min() * 100
            bh_dd = ((g.BH - g.BH.cummax()) / g.BH.cummax()).min() * 100
            rows.append({
                'Asset': name,
                'Year': y,
                'Strat_Ret': round(strat_ret, 1),
                'BH_Ret': round(bh_ret, 1),
                'Alpha': round(strat_ret - bh_ret, 1),
                'Strat_DD': round(strat_dd, 1),
                'BH_DD': round(bh_dd, 1)
            })
        print(f"\n--- {name} YEARLY BREAKDOWN ---")
        print(pd.DataFrame(rows).to_string(index=False))

def get_period_breakdown():
    files = {'BTC': 'BTC_USD_4h.csv', 'ETH': 'ETH_USD_4h.csv', 'SOL': 'SOL_USD_4h.csv'}
    weights = {'BTC': 0.5, 'ETH': 0.3, 'SOL': 0.2}
    
    # Portfolio equity full
    eqs, bhs = {}, {}
    for name, f in files.items():
        _, eq, bh = run_best(f, 1000.0 * weights[name])
        eqs[name] = eq.rename(name)
        bhs[name] = bh.rename(name)
    comb = pd.concat(eqs.values(), axis=1).ffill()
    comb_bh = pd.concat(bhs.values(), axis=1).ffill()
    for n in weights:
        comb[n] = comb[n].fillna(1000.0 * weights[n])
        comb_bh[n] = comb_bh[n].fillna(1000.0 * weights[n])
    port_eq = comb.sum(axis=1)
    port_bh = comb_bh.sum(axis=1)
    
    end_date = port_eq.index[-1]
    periods = {
        '3 Months': 90,
        '6 Months': 180,
        '1 Year (12M)': 365,
        '2 Years (24M)': 730,
        '3 Years (36M)': 1095,
        'Full History': (end_date - port_eq.index[0]).days
    }
    
    p_rows = []
    for p_name, days in periods.items():
        start_dt = end_date - pd.Timedelta(days=days)
        sub_eq = port_eq[port_eq.index >= start_dt]
        sub_bh = port_bh[port_bh.index >= start_dt]
        if len(sub_eq) < 10:
            continue
        s_ret = (sub_eq.iloc[-1] / sub_eq.iloc[0] - 1) * 100
        b_ret = (sub_bh.iloc[-1] / sub_bh.iloc[0] - 1) * 100
        s_dd = ((sub_eq - sub_eq.cummax()) / sub_eq.cummax()).min() * 100
        b_dd = ((sub_bh - sub_bh.cummax()) / sub_bh.cummax()).min() * 100
        p_rows.append({
            'Period': p_name,
            'Strat_Ret': round(s_ret, 1),
            'BH_Ret': round(b_ret, 1),
            'Alpha': round(s_ret - b_ret, 1),
            'Strat_MaxDD': round(s_dd, 1),
            'BH_MaxDD': round(b_dd, 1)
        })
    print("\n--- PORTFOLIO FIXED PERIOD BREAKDOWN ---")
    print(pd.DataFrame(p_rows).to_string(index=False))

if __name__ == '__main__':
    get_portfolio_yearly()
    get_period_breakdown()
    get_asset_yearly()
