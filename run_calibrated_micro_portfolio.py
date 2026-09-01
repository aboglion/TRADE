"""
GLOBAL OPTIMAL CALIBRATED MICRO PORTFOLIO RUNNER
================================================
Executes the Global Optimal Calibration:
  - ScaleFactor = 4.0
  - RSI Threshold = 58.0
  - Trail Multiplier = 4.5
  - Stop ATR = 2.2
  - Volume Multiplier = 1.1
Outputs complete metrics and equity chart under TAKER_STANDARD, MAKER_VIP, and ZERO_FEE.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os

from micro_engine import load_data, calculate_micro_metrics, FEE_PRESETS, INITIAL_CAPITAL
from optimize_micro_calibration import run_grid_backtest, verify_timeframe_spacing

def main():
    print("=" * 80)
    print("🏆 GLOBAL OPTIMAL CALIBRATED MICRO PORTFOLIO — PERFORMANCE AUDIT")
    print("=" * 80)
    
    files = {
        'BTC': 'data/BTC_USD_4h.csv',
        'ETH': 'data/ETH_USD_4h.csv',
        'SOL': 'data/SOL_USD_4h.csv',
    }
    weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    
    dfs = {name: load_data(path) for name, path in files.items()}
    for name, df in dfs.items():
        verify_timeframe_spacing(df, name)
        
    sf = 4.0
    rsi_t = 58.0
    tm = 4.5
    stop_atr = 2.2
    vol_m = 1.1
    
    fee_presets = ['TAKER_STANDARD', 'MAKER_VIP', 'ZERO_FEE']
    results_rows = []
    equity_curves = {}
    
    for preset in fee_presets:
        preset_info = FEE_PRESETS[preset]
        fee_side = preset_info['fee'] + preset_info['slippage']
        
        eqs = {}
        all_tr = []
        total_fees = 0.0
        
        for name, df in dfs.items():
            w = weights[name]
            c_cap = INITIAL_CAPITAL * w
            
            x = df.copy()
            ema_fast = max(3, int(round(9 * sf)))
            ema_slow = max(10, int(round(50 * sf)))
            ema_macro = max(30, int(round(200 * sf)))
            donch_b = max(4, int(round(12 * sf)))
            rsi_p = max(3, int(round(7 * sf)))
            
            x["EMA_Fast"] = x.Close.ewm(span=ema_fast, adjust=False).mean()
            x["EMA_Slow"] = x.Close.ewm(span=ema_slow, adjust=False).mean()
            x["EMA_Macro"] = x.Close.ewm(span=ema_macro, adjust=False).mean()
            
            prev = x.Close.shift()
            tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
            x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
            
            delta = x.Close.diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/rsi_p, min_periods=rsi_p).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/rsi_p, min_periods=rsi_p).mean()
            rs = gain / loss.replace(0, np.nan)
            x["RSI"] = 100 - (100 / (1 + rs))
            
            x["DonchHigh"] = x.High.rolling(donch_b).max().shift(1)
            x["VolSMA20"] = x.Volume.rolling(20).mean()
            x = x.dropna(subset=['ATR', 'RSI', 'EMA_Macro'])
            
            cash = c_cap
            pos_units = 0.0
            entry_px = 0.0
            stop_px = 0.0
            extreme_px = 0.0
            entry_i = 0
            trade_fees = 0.0
            asset_fees = 0.0
            
            trades = []
            eq_val, eq_idx = [c_cap], [x.index[0]]
            
            close_v = x.Close.values
            low_v = x.Low.values
            high_v = x.High.values
            atr_v = x.ATR.values
            rsi_v = x.RSI.values
            ef_v, es_v, emac_v = x.EMA_Fast.values, x.EMA_Slow.values, x.EMA_Macro.values
            donch_v = x.DonchHigh.values
            vol_v, volsma_v = x.Volume.values, x.VolSMA20.values
            
            warmup = max(60, ema_macro)
            
            for i in range(warmup, len(x)):
                c = close_v[i]
                atr = atr_v[i]
                
                if pos_units == 0.0:
                    ef, es, emac = ef_v[i], es_v[i], emac_v[i]
                    rsi = rsi_v[i]
                    vol = vol_v[i]
                    volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
                    
                    macro_bull = (c > es) and (es > emac)
                    micro_surge = (c >= donch_v[i]) and (rsi >= rsi_t) and (vol > volsma * vol_m)
                    
                    if macro_bull and micro_surge:
                        invested = cash * 0.90
                        entry_px = c * (1.0 + fee_side)
                        entry_fee = invested * fee_side
                        pos_units = invested / entry_px
                        cash -= invested
                        stop_px = entry_px - stop_atr * atr
                        extreme_px = entry_px
                        entry_i = i
                        trade_fees = entry_fee
                        trades.append({'entry_date': x.index[i], 'entry_px': entry_px})
                else:
                    extreme_px = max(extreme_px, high_v[i])
                    raw_trail = extreme_px - tm * atr
                    stop_px = max(stop_px, raw_trail)
                    
                    max_hold = int(round(36 * sf))
                    
                    if low_v[i] <= stop_px or (i - entry_i) >= max_hold:
                        exit_px = min(stop_px, c)
                        exit_px_net = exit_px * (1.0 - fee_side)
                        exit_fee = pos_units * exit_px * fee_side
                        trade_fees += exit_fee
                        asset_fees += trade_fees
                        
                        net_pnl = pos_units * (exit_px_net - entry_px)
                        cash += pos_units * exit_px_net
                        
                        trades[-1].update({
                            'exit_date': x.index[i],
                            'exit_px': exit_px,
                            'net_pnl': net_pnl,
                            'return_pct_net': net_pnl / (pos_units * entry_px) * 100,
                            'bars_held': i - entry_i
                        })
                        pos_units = 0.0
                        trade_fees = 0.0

                eq_val.append(cash + pos_units * c)
                eq_idx.append(x.index[i])
                
            eqs[name] = pd.Series(eq_val, index=eq_idx, name=name)
            total_fees += asset_fees
            if trades:
                tr_df = pd.DataFrame(trades)
                all_tr.append(tr_df.assign(asset=name))
                m_asset = calculate_micro_metrics(eqs[name], tr_df, asset_fees)
                results_rows.append({'Regime': preset, 'Asset': name, **m_asset})
                
        comb = pd.concat(eqs.values(), axis=1).ffill()
        for name in dfs:
            comb[name] = comb[name].fillna(INITIAL_CAPITAL * weights[name])
        port_eq = comb.sum(axis=1)
        equity_curves[f'Portfolio ({preset})'] = port_eq
        
        comb_tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
        port_m = calculate_micro_metrics(port_eq, comb_tr, total_fees)
        results_rows.append({'Regime': preset, 'Asset': 'PORTFOLIO_40_30_30', **port_m})

    results_df = pd.DataFrame(results_rows)
    print("\n" + "=" * 80)
    print("📊 GLOBAL OPTIMAL CALIBRATED MICRO PERFORMANCE SUMMARY")
    print("=" * 80)
    cols = ['Regime', 'Asset', 'Final ($)', 'Return (%)', 'CAGR (%)', 'Sharpe', 'MaxDD (%)', 'Trades', 'Trades/Year', 'WinRate (%)', 'TotalFeesPaid ($)']
    print(results_df[cols].to_string(index=False))

    # Plot Equity Curve
    plt.figure(figsize=(12, 6))
    colors = {
        'Portfolio (TAKER_STANDARD)': '#e53e3e',
        'Portfolio (MAKER_VIP)':      '#2b6cb0',
        'Portfolio (ZERO_FEE)':       '#38a169',
    }
    for label, eq in equity_curves.items():
        plt.plot(eq.index, eq.values, label=label, color=colors.get(label, '#718096'), lw=2.0)
    plt.axhline(INITIAL_CAPITAL, color='gray', linestyle='--', alpha=0.7, label='Initial $1,000')
    plt.yscale('log')
    plt.title('Global Optimal Calibrated Micro Portfolio Equity (Log Scale)', fontsize=14, fontweight='bold')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Equity ($ Log)')
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left')
    plt.tight_layout()
    chart_path = 'data/global_optimal_micro_portfolio_chart.png'
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"\n📈 Chart saved to: {chart_path}")

if __name__ == '__main__':
    main()

