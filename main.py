"""
DYNAMIC REGIME-ADAPTIVE 2.0x MASTER PRODUCTION CLI LAUNCHER
============================================================
Primary entry point for the Dynamic Regime-Adaptive 2.0x Strategy.

Usage:
  python3 main.py             # Runs 2.0x Dynamic Adaptive Strategy & generates dashboard.html
  python3 main.py --dashboard # Generates dashboard.html
  python3 main.py --oos       # Runs leak-free Out-of-Sample (OOS 2024-2026) validation
"""

import sys
import engine

def main():
    args = sys.argv[1:]
    
    if '--oos' in args:
        engine.run_true_oos_validation()
    elif '--dashboard' in args:
        engine.generate_dashboard_html()
    else:
        print("⚡ Running Dynamic Regime-Adaptive 2.0x Production Engine...")
        dyn_eq, hy_aligned, bh_aligned = engine.run_dynamic_adaptive_20x_engine(bull_leverage=2.0)
        m = engine.calculate_metrics(dyn_eq, pd.DataFrame(), bh_aligned)
        print("\n🏆 DYNAMIC REGIME-ADAPTIVE 2.0x OVERALL RESULTS:")
        for k, v in m.items():
            print(f"  • {k:<16}: {v}")
        engine.generate_dashboard_html()

if __name__ == '__main__':
    import pandas as pd
    main()