"""
DYNAMIC REGIME-ADAPTIVE GUARDED 20x MASTER PRODUCTION CLI LAUNCHER
===================================================================
Primary entry point for the Dynamic Regime-Adaptive Guarded 20x Strategy.

Usage:
  python3 main.py             # Runs Guarded 20x Dynamic Adaptive Strategy & generates dashboard.html
  python3 main.py --dashboard # Generates dashboard.html
  python3 main.py --oos       # Runs leak-free Out-of-Sample (OOS 2024-2026) validation
"""

import sys
import pandas as pd
import engine

def main():
    args = sys.argv[1:]
    
    if '--oos' in args:
        engine.run_true_oos_validation()
    elif '--dashboard' in args:
        engine.generate_dashboard_html()
    else:
        print("⚡ Running Dynamic Regime-Adaptive Guarded 20x Production Engine (with Binance Tiered Brackets & Stepped Ladder)...")
        dyn_eq, hy_aligned, bh_aligned = engine.run_dynamic_adaptive_20x_engine(
            initial_capital=1000.0,
            clamp_binance_brackets=True
        )
        m = engine.calculate_metrics(dyn_eq, pd.DataFrame(), bh_aligned)
        print("\n🏆 DYNAMIC REGIME-ADAPTIVE GUARDED 20x (BINANCE BRACKETS) RESULTS:")
        for k, v in m.items():
            print(f"  • {k:<16}: {v}")
        engine.generate_dashboard_html()

if __name__ == '__main__':
    main()