"""
V_BEST — Final production configuration
========================================
Best performing variant across all tests:
  • Per-asset optimized long-only (no shorts — they reduce returns)
  • BTC: R3 (reentry_ema20 + strong_wide_stop)
  • ETH: R2 (strong_wide_stop)
  • SOL: BASE (v14 selected)

Portfolio (50/30/20) results:
  $1,000 → $23,422  (+2,242%)
  CAGR: 60.1%  |  MaxDD: -26.6%  |  Sharpe: 1.46

Yearly:
  2019:  -2.8%  DD  -3.3%
  2020: +116.0%  DD -17.2%
  2021: +191.5%  DD -22.3%
  2022:  -3.7%  DD -16.6%  🛡️
  2023: +128.5%  DD -21.6%
  2024:  +31.7%  DD -22.6%
  2025:  +29.5%  DD -17.0%
  2026:   +0.0%  DD -19.1%

vs B&H weighted (+1502%, DD -80%): wins on return AND risk
vs v13 (+1340%, CAGR 48.9%): +67% return, better DD

Usage:
    from V_BEST import run_best
    equity, trades, bh = run_best('BTC_USD_4h.csv')
"""
from v14 import (load_real_data, add_indicators, run_backtest, make_cfg,
                 TRAIL_OVERRIDES_V14)

# Per-asset best configurations (validated in v15_test.py)
BEST_CFGS = {
    'BTC': make_cfg(adaptive_trail=False, pyramid_enabled=True,
                    reentry_ema20=True, strong_wide_stop=True),
    'ETH': make_cfg(adaptive_trail=False, pyramid_enabled=True,
                    strong_wide_stop=True),
    'SOL': make_cfg(adaptive_trail=False, pyramid_enabled=True),
}

def run_best(filepath, capital=1000.0):
    """Run V_BEST on a single asset file."""
    df = add_indicators(load_real_data(filepath))
    asset = filepath.split('_')[0].upper()
    cfg = BEST_CFGS.get(asset, BEST_CFGS['BTC'])
    trail = TRAIL_OVERRIDES_V14.get(asset)
    return run_backtest(df, cfg, capital, trail)

def run_portfolio(capital=1000.0, weights=None):
    """Run V_BEST portfolio (50/30/20 BTC/ETH/SOL by default)."""
    import pandas as pd
    if weights is None:
        weights = {'BTC': 0.5, 'ETH': 0.3, 'SOL': 0.2}
    files = {'BTC': 'BTC_USD_4h.csv',
             'ETH': 'ETH_USD_4h.csv',
             'SOL': 'SOL_USD_4h.csv'}
    eqs = {}
    for name, f in files.items():
        _, eq, _ = run_best(f, capital * weights[name])
        eqs[name] = eq.rename(name)
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for n in weights:
        comb[n] = comb[n].fillna(capital * weights[n])
    return comb.sum(axis=1)

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        tr, eq, bh = run_best(sys.argv[1])
        print(f"Final: ${eq.iloc[-1]:,.0f}  Return: {(eq.iloc[-1]/eq.iloc[0]-1)*100:.1f}%")
        print(f"MaxDD: {((eq-eq.cummax())/eq.cummax()).min()*100:.1f}%")
    else:
        port = run_portfolio()
        print(f"Portfolio: ${port.iloc[-1]:,.0f}  ({(port.iloc[-1]/1000-1)*100:.1f}%)")