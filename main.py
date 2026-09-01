"""
Production Hybrid Core-Satellite (80/20) Portfolio Runner
==========================================================
Recommended Production System:
  • 80% Core Capital ($800)  -> Regime-Aware Macro Engine (v14 / V_BEST)
  • 20% Satellite Capital ($200) -> Calibrated Micro Engine
  • Full TAKER_STANDARD fee included (0.125% per side = 0.25% Round-Trip)

Usage:
    from main import run_portfolio
    hybrid_eq = run_portfolio()
"""

import sys
import pandas as pd
import numpy as np
import engine
from run_hybrid_portfolio import run_hybrid_engine, run_macro_with_fee

# Per-asset best configurations (validated for hyper-trend + regime override)
BEST_CFGS = {
    'BTC': engine.make_cfg(adaptive_trail=False, pyramid_enabled=True,
                           reentry_ema20=True, strong_wide_stop=True, trail_max_strong=12.0, strong_alloc=0.98),
    'ETH': engine.make_cfg(adaptive_trail=False, pyramid_enabled=True,
                           strong_wide_stop=True, trail_max_strong=14.0, strong_alloc=0.98, tp1_enabled=False),
    'SOL': engine.make_cfg(adaptive_trail=False, pyramid_enabled=True, pyramid_max_adds=2,
                           pyramid_add_fractions=(0.5, 0.3), strong_wide_stop=True, trail_max_strong=16.0,
                           strong_alloc=0.98, tp1_enabled=False),
}

def run_best(filepath, capital=1000.0):
    """Run V_BEST on a single asset file (Macro Core component)."""
    df = engine.add_indicators(engine.load_real_data(filepath))
    asset_name = filepath.split('/')[-1].split('_')[0].upper()
    cfg = BEST_CFGS.get(asset_name, BEST_CFGS['BTC'])
    trail = (7.5, 5.0) if asset_name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(asset_name)
    return engine.run_backtest(df, cfg, capital, trail)

def run_portfolio(capital=1000.0, weights=None):
    """Production entry point: Runs the Hybrid 80/20 portfolio with full fees."""
    hybrid_eq, macro_part, micro_part = run_hybrid_engine(initial_capital=capital, weights=weights)
    return hybrid_eq

if __name__ == '__main__':
    print("=" * 80)
    print("🏆 RUNNING PRODUCTION HYBRID (80/20) PORTFOLIO STRATEGY")
    print("=" * 80)
    
    port = run_portfolio()
    cap = 1000.0
    final_val = port.iloc[-1]
    ret_pct = (final_val / cap - 1) * 100
    cummax = port.cummax()
    max_dd = ((port - cummax) / cummax).min() * 100
    
    print(f"Hybrid Portfolio Final Value: ${final_val:,.2f}  (+{ret_pct:,.2f}%)")
    print(f"Max Drawdown:                 {max_dd:.2f}%")
    
    try:
        from generate_dashboard import build_dashboard_data, generate_html_dashboard
        print("⏳ Updating interactive dashboard with Hybrid Portfolio data...")
        payload = build_dashboard_data()
        generate_html_dashboard(payload, 'dashboard.html')
        print("✅ Dashboard updated successfully: dashboard.html")
    except Exception as e:
        print(f"Notice: Dashboard generation skipped ({e})")