"""
RUNNER FOR MICRO STRATEGY — INTRADAY & DAILY HIGH-VELOCITY TRADING WITH FEE ANALYSIS
=====================================================================================
Executes the micro trading engine across BTC, ETH, SOL and multi-asset portfolio.
Compares performance across 3 Fee Regimes:
  1. TAKER_STANDARD (0.075% fee + 0.025% slippage = 0.20% round-trip)
  2. MAKER_VIP (0.020% fee + 0.010% slippage = 0.06% round-trip)
  3. ZERO_FEE (0.00% ideal baseline)
"""

import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from micro_engine import (load_data, add_micro_indicators, run_micro_backtest,
                          calculate_micro_metrics, make_micro_cfg, INITIAL_CAPITAL)

def run_micro_portfolio(dfs, cfg, weights, capital=INITIAL_CAPITAL):
    eqs = {}
    all_trades = []
    total_portfolio_fees = 0.0
    
    for name, df in dfs.items():
        tr, eq, _, fees = run_micro_backtest(df, cfg, capital * weights[name])
        eqs[name] = eq.rename(name)
        total_portfolio_fees += fees
        if not tr.empty:
            all_trades.append(tr.assign(asset=name))
            
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in dfs:
        comb[name] = comb[name].fillna(capital * weights[name])
        
    portfolio_equity = comb.sum(axis=1)
    combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return combined_trades, portfolio_equity, total_portfolio_fees

def main():
    print("=" * 80)
    print("⚡ MICRO STRATEGY ENGINE — INTRADAY / FAST-CYCLE DAILY TRADING WITH FEE MODELING")
    print("=" * 80)

    # 1. Load Data
    files = {
        'BTC': 'data/BTC_USD_4h.csv',
        'ETH': 'data/ETH_USD_4h.csv',
        'SOL': 'data/SOL_USD_4h.csv',
    }
    
    dfs = {}
    for name, path in files.items():
        dfs[name] = add_micro_indicators(load_data(path))

    weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    fee_presets = ['TAKER_STANDARD', 'MAKER_VIP', 'ZERO_FEE']
    
    results_rows = []
    equity_curves = {}

    # 2. Run Backtests per Asset & Portfolio under all Fee Regimes
    for preset in fee_presets:
        cfg = make_micro_cfg(fee_preset=preset)
        print(f"\n🧪 Testing Fee Regime: {preset}...")
        
        # Portfolio Run
        port_tr, port_eq, port_fees = run_micro_portfolio(dfs, cfg, weights)
        port_m = calculate_micro_metrics(port_eq, port_tr, port_fees)
        
        equity_curves[f'Portfolio ({preset})'] = port_eq
        
        port_row = {'Regime': preset, 'Asset': 'PORTFOLIO_40_30_30', **port_m}
        results_rows.append(port_row)
        
        print(f"  --> Portfolio: Final ${port_m['Final ($)']:,} | Return: {port_m['Return (%)']}% | "
              f"Sharpe: {port_m['Sharpe']} | MaxDD: {port_m['MaxDD (%)']}% | Fees Paid: ${port_m['TotalFeesPaid ($)']}")
        
        # Per Asset Runs
        for name, df in dfs.items():
            tr, eq, bh, fees = run_micro_backtest(df, cfg, INITIAL_CAPITAL * weights[name])
            m = calculate_micro_metrics(eq, tr, fees, bh)
            row = {'Regime': preset, 'Asset': name, **m}
            results_rows.append(row)

    results_df = pd.DataFrame(results_rows)
    os.makedirs('data', exist_ok=True)
    results_df.to_csv('data/micro_strategy_summary.csv', index=False)
    
    print("\n" + "=" * 80)
    print("📊 MICRO STRATEGY PERFORMANCE & FEE DRAG SUMMARY TABLE")
    print("=" * 80)
    
    cols_to_show = ['Regime', 'Asset', 'Final ($)', 'Return (%)', 'CAGR (%)', 'Sharpe',
                    'MaxDD (%)', 'Trades', 'Trades/Year', 'WinRate (%)', 'PF', 'TotalFeesPaid ($)', 'FeeDrag (% Capital)']
    print(results_df[cols_to_show].to_string(index=False))

    # 3. Plot Fee Drag Comparison Chart
    plt.figure(figsize=(12, 6))
    colors = {
        'Portfolio (TAKER_STANDARD)': '#e53e3e',  # Red
        'Portfolio (MAKER_VIP)':      '#2b6cb0',  # Blue
        'Portfolio (ZERO_FEE)':       '#38a169',  # Green
    }
    
    for label, eq in equity_curves.items():
        plt.plot(eq.index, eq.values, label=label, color=colors.get(label, '#718096'), lw=2.0)
        
    plt.axhline(INITIAL_CAPITAL, color='gray', linestyle='--', alpha=0.7, label='Initial $1,000')
    plt.yscale('log')
    plt.title('Micro Strategy Equity Curves — Impact of Fees (Log Scale)', fontsize=14, fontweight='bold')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Equity ($ Log Scale)')
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left')
    plt.tight_layout()
    chart_path = 'data/micro_strategy_fee_chart.png'
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"\n📈 Chart saved to: {chart_path}")

if __name__ == '__main__':
    main()

