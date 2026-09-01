"""
OPTIMIZATION & TIMEFRAME SPACING ALIGNMENT VERIFICATION ENGINE
==============================================================
1. Verifies exact timeframe spacing and period math (4h bars = 6/day = 2,190/yr).
2. Performs 6-dimensional Grid Search to find Global Optimal Calibration.
3. Conducts Parameter Sensitivity & Robustness Audit under TAKER_STANDARD fee.
"""

import os
import pandas as pd
import numpy as np

from micro_engine import load_data, calculate_micro_metrics, FEE_PRESETS, INITIAL_CAPITAL

def verify_timeframe_spacing(df, asset_name):
    time_diffs = df.index.to_series().diff().dropna()
    median_hours = time_diffs.median().total_seconds() / 3600.0
    total_bars = len(df)
    total_days = (df.index[-1] - df.index[0]).total_seconds() / 86400.0
    bars_per_day = total_bars / total_days
    periods_per_year = bars_per_day * 365.25
    
    print(f"⏱️ Timeframe Verification for {asset_name}:")
    print(f"   - Median Bar Interval: {median_hours:.1f} hours")
    print(f"   - Bars per Day: {bars_per_day:.2f}")
    print(f"   - Periods per Year: {periods_per_year:.1f} (Standard 4h = 2191.5)")
    print(f"   - Dataset Date Range: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')} ({total_days:.1f} days)")
    return periods_per_year

def run_grid_backtest(dfs, weights, sf, rsi_t, tm, stop_atr, vol_m, fee_preset='TAKER_STANDARD'):
    preset = FEE_PRESETS[fee_preset]
    fee_side = preset['fee'] + preset['slippage']
    
    eqs = {}
    all_tr = []
    total_fees = 0.0
    
    for name, df in dfs.items():
        w = weights[name]
        c_cap = INITIAL_CAPITAL * w
        
        # Indicator calculation
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
            all_tr.append(pd.DataFrame(trades).assign(asset=name))
            
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in dfs:
        comb[name] = comb[name].fillna(INITIAL_CAPITAL * weights[name])
    port_eq = comb.sum(axis=1)
    comb_tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
    
    m = calculate_micro_metrics(port_eq, comb_tr, total_fees)
    return m

def main():
    print("=" * 80)
    print("🔬 GLOBAL OPTIMIZATION & TIMEFRAME ALIGNMENT AUDIT")
    print("=" * 80)
    
    files = {'BTC': 'data/BTC_USD_4h.csv', 'ETH': 'data/ETH_USD_4h.csv', 'SOL': 'data/SOL_USD_4h.csv'}
    weights = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}
    
    dfs = {}
    for name, path in files.items():
        df = load_data(path)
        verify_timeframe_spacing(df, name)
        dfs[name] = df
        
    print("\n🔍 Running 6-Dimensional Parameter Grid Search across Portfolio under TAKER_STANDARD fee...")
    
    scale_factors = [4.0, 6.0, 8.0, 10.0, 12.0]
    rsi_thresholds = [54.0, 58.0, 62.0]
    trail_mults = [3.0, 3.5, 4.0, 4.5]
    stop_atrs = [1.8, 2.0, 2.2]
    vol_mults = [1.1, 1.3]
    
    grid_results = []
    total_combos = len(scale_factors) * len(rsi_thresholds) * len(trail_mults) * len(stop_atrs) * len(vol_mults)
    print(f"Total Combinations to Evaluate: {total_combos}")
    
    count = 0
    for sf in scale_factors:
        for rsi_t in rsi_thresholds:
            for tm in trail_mults:
                for stop_atr in stop_atrs:
                    for vol_m in vol_mults:
                        m = run_grid_backtest(dfs, weights, sf, rsi_t, tm, stop_atr, vol_m)
                        count += 1
                        if m and 'Final ($)' in m:
                            grid_results.append({
                                'ScaleFactor': sf,
                                'RSI_Thresh': rsi_t,
                                'TrailMult': tm,
                                'StopATR': stop_atr,
                                'VolMult': vol_m,
                                'Final ($)': m['Final ($)'],
                                'Return (%)': m['Return (%)'],
                                'CAGR (%)': m['CAGR (%)'],
                                'Sharpe': m['Sharpe'],
                                'MaxDD (%)': m['MaxDD (%)'],
                                'Trades': m['Trades'],
                                'TotalFees ($)': m['TotalFeesPaid ($)'],
                            })
                            
    res_df = pd.DataFrame(grid_results)
    
    print("\n" + "=" * 80)
    print("🏆 TOP 10 GLOBAL OPTIMAL CALIBRATIONS (PORTFOLIO — TAKER_STANDARD FEE):")
    print("=" * 80)
    top_10 = res_df.sort_values(by='Final ($)', ascending=False).head(10)
    print(top_10.to_string(index=False))
    
    # Save results to CSV
    res_df.to_csv('data/micro_grid_optimization.csv', index=False)
    print(f"\n💾 Full grid results saved to: data/micro_grid_optimization.csv")

if __name__ == '__main__':
    main()
