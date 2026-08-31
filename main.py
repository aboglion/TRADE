"""
Strategy Execution — Production Portfolio Runner
=================================================
Best performing variant across all tests:
  • Per-asset optimized long-only (no shorts)
  • BTC: R3 (reentry_ema20 + strong_wide_stop)
  • ETH: R2 (strong_wide_stop)
  • SOL: BASE (pyramiding enabled)

Portfolio (50/30/20) results:
  $1,000 → $23,422  (+2,242%)
  CAGR: 60.1%  |  MaxDD: -26.6%  |  Sharpe: 1.46

Usage:
    from main import run_best, run_portfolio
    portfolio_equity = run_portfolio()
"""
from engine import (load_real_data, add_indicators, run_backtest, make_cfg,
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
        try:
            from generate_dashboard import build_dashboard_data, generate_html_dashboard
            print("⏳ Generating interactive dashboard...")
            payload = build_dashboard_data()
            generate_html_dashboard(payload, 'dashboard.html')
        except Exception as e:
            print(f"Notice: Dashboard generation skipped ({e})")