"""
HYBRID CORE-SATELLITE (80/20) MASTER PRODUCTION CLI LAUNCHER
=============================================================
Primary entry point for the Hybrid Quantitative Trading System.

Usage:
  python3 main.py             # Runs recommended hybrid strategy & generates dashboard.html
  python3 main.py --dashboard # Generates dashboard.html
  python3 main.py --oos       # Runs leak-free Out-of-Sample (OOS 2024-2026) validation
  python3 main.py --test      # Runs unit test suite
"""

import sys
import engine

def main():
    args = sys.argv[1:]
    
    if '--oos' in args:
        engine.run_true_oos_validation()
    elif '--dashboard' in args:
        engine.generate_dashboard_html()
    elif '--test' in args:
        import pytest
        sys.exit(pytest.main(['tests/']))
    else:
        print("⚡ Running Production Hybrid Core-Satellite Engine (80/20)...")
        engine.run_rebalanced_hybrid_engine()
        engine.generate_dashboard_html()

if __name__ == '__main__':
    main()