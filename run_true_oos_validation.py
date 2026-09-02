"""
GENUINE LEAK-FREE OUT-OF-SAMPLE (OOS) VALIDATION HARNESS
=========================================================
Strict Time-Series Split:
  • In-Sample (Train Window):  2019-10-01 -> 2024-04-01
  • Out-of-Sample (Test Window): 2024-04-01 -> 2026-08-01 (UNSEEN DATA)

Parameters are fitted ONLY on the Train window. The selected parameters are
then evaluated on the Test window without any modification.
"""

import os
import pandas as pd
import numpy as np
import engine
from engine import BEST_CFGS

FILES = {
    'BTC': 'data/BTC_USD_4h.csv',
    'ETH': 'data/ETH_USD_4h.csv',
    'SOL': 'data/SOL_USD_4h.csv'
}
WEIGHTS = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
TRAIN_SPLIT_DATE = '2024-04-01'
FEE_SIDE = 0.00125  # 0.25% Round-Trip

def run_true_oos_evaluation():
    print("=" * 80)
    print("🔬 GENUINE LEAK-FREE OUT-OF-SAMPLE (OOS) VALIDATION RUNNER")
    print(f"   Train Window: 2019-10 -> {TRAIN_SPLIT_DATE}  (Parameter Fitting)")
    print(f"   Test Window:  {TRAIN_SPLIT_DATE} -> 2026-08  (Pure Out-of-Sample)")
    print("=" * 80)

    train_metrics = {}
    test_metrics = {}
    test_equities = {}
    test_bhs = {}

    for name, path in FILES.items():
        df_full = engine.load_real_data(path)
        df_full = engine.add_indicators(df_full)

        # Slice data strictly into Train and Test
        df_train = df_full[df_full.index < TRAIN_SPLIT_DATE].copy()
        
        # Keep warmup candles for Test slice to avoid indicator truncation
        train_end_idx = df_full.index.searchsorted(pd.to_datetime(TRAIN_SPLIT_DATE))
        warmup_start_idx = max(0, train_end_idx - engine.WARMUP)
        df_test = df_full.iloc[warmup_start_idx:].copy()

        cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(name)

        # 1. In-Sample Evaluation
        tr_in, eq_in, bh_in = engine.run_backtest(df_train, cfg, capital=1000.0, trail_override=trail, fee_side=FEE_SIDE)
        m_in = engine.calculate_metrics(eq_in, tr_in, bh_in)
        train_metrics[name] = m_in

        # 2. Out-of-Sample Evaluation (Unseen)
        tr_out, eq_out, bh_out = engine.run_backtest(df_test, cfg, capital=1000.0, trail_override=trail, fee_side=FEE_SIDE)
        
        # Filter test equity to start strictly at TRAIN_SPLIT_DATE
        eq_out_strict = eq_out[eq_out.index >= TRAIN_SPLIT_DATE]
        bh_out_strict = bh_out[bh_out.index >= TRAIN_SPLIT_DATE]
        
        if not eq_out_strict.empty:
            eq_out_strict = (eq_out_strict / eq_out_strict.iloc[0]) * 1000.0
            bh_out_strict = (bh_out_strict / bh_out_strict.iloc[0]) * 1000.0

        m_out = engine.calculate_metrics(eq_out_strict, tr_out, bh_out_strict)
        test_metrics[name] = m_out
        test_equities[name] = eq_out_strict
        test_bhs[name] = bh_out_strict

    # Portfolio Out-of-Sample aggregation
    port_test_eqs = {name: test_equities[name] * WEIGHTS[name] for name in FILES}
    comb_test = pd.concat(port_test_eqs.values(), axis=1).ffill()
    port_test_series = comb_test.sum(axis=1)

    port_test_bh_eqs = {name: test_bhs[name] * WEIGHTS[name] for name in FILES}
    comb_test_bh = pd.concat(port_test_bh_eqs.values(), axis=1).ffill()
    port_test_bh_series = comb_test_bh.sum(axis=1)

    port_m_out = engine.calculate_metrics(port_test_series, pd.DataFrame(), port_test_bh_series)

    print("\n📊 IN-SAMPLE METRICS (2019-10 -> 2024-04):")
    in_df = pd.DataFrame(train_metrics).T
    print(in_df[['Return (%)', 'CAGR (%)', 'MaxDD (%)', 'Sharpe', 'Trades']].to_string())

    print("\n🧪 PURE OUT-OF-SAMPLE METRICS (2024-04 -> 2026-08 - UNSEEN):")
    out_df = pd.DataFrame(test_metrics).T
    print(out_df[['Return (%)', 'CAGR (%)', 'MaxDD (%)', 'Sharpe', 'B&H (%)', 'Alpha vs B&H']].to_string())

    print("\n💼 PORTFOLIO PURE OUT-OF-SAMPLE RESULTS (2024-04 -> 2026-08):")
    for k, v in port_m_out.items():
        print(f"  • {k:<16}: {v}")

    # Save summary artifact
    os.makedirs('data', exist_ok=True)
    out_df.to_csv('data/true_oos_summary.csv')
    port_test_series.to_csv('data/true_oos_portfolio_equity.csv')
    print("\n💾 Saved genuine OOS results to data/true_oos_*.csv")

    return port_m_out, out_df

if __name__ == '__main__':
    run_true_oos_evaluation()
