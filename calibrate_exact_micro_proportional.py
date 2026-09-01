"""
EXACT MATHEMATICAL PROPORTIONAL CALIBRATION ENGINE
=================================================
Tests exact proportional mathematical scaling of proven macro logic (v14/V_BEST)
to micro timeframes (4h / 1h equivalent) to achieve maximum return and fee resistance.
"""

import pandas as pd
import numpy as np
import os

from micro_engine import load_data, calculate_micro_metrics, FEE_PRESETS, INITIAL_CAPITAL

def run_exact_proportional_backtest(df, scale_factor, rsi_sens, trail_mult, fee_preset='TAKER_STANDARD'):
    # Mathematically scaled indicator periods
    ema_fast = max(3, int(round(9 * scale_factor)))
    ema_med = max(5, int(round(21 * scale_factor)))
    ema_slow = max(10, int(round(50 * scale_factor)))
    ema_macro = max(30, int(round(200 * scale_factor)))
    donch_bars = max(4, int(round(12 * scale_factor)))
    rsi_p = max(3, int(round(7 * scale_factor)))
    
    x = df.copy()
    x["EMA_Fast"] = x.Close.ewm(span=ema_fast, adjust=False).mean()
    x["EMA_Med"] = x.Close.ewm(span=ema_med, adjust=False).mean()
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
    
    x["DonchHigh"] = x.High.rolling(donch_bars).max().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()
    x = x.dropna(subset=['ATR', 'RSI', 'EMA_Macro'])
    
    preset = FEE_PRESETS[fee_preset]
    fee_side = preset['fee'] + preset['slippage']
    
    cash = INITIAL_CAPITAL
    pos_units = 0.0
    entry_px = 0.0
    stop_px = 0.0
    extreme_px = 0.0
    entry_i = 0
    trade_fees = 0.0
    total_fees = 0.0
    
    trades = []
    eq_val, eq_idx = [INITIAL_CAPITAL], [x.index[0]]
    
    close_v = x.Close.values
    low_v = x.Low.values
    high_v = x.High.values
    atr_v = x.ATR.values
    rsi_v = x.RSI.values
    ef_v, em_v, es_v, emac_v = x.EMA_Fast.values, x.EMA_Med.values, x.EMA_Slow.values, x.EMA_Macro.values
    donch_v = x.DonchHigh.values
    vol_v, volsma_v = x.Volume.values, x.VolSMA20.values
    
    warmup = max(60, ema_macro)
    
    for i in range(warmup, len(x)):
        c = close_v[i]
        atr = atr_v[i]
        
        if pos_units == 0.0:
            # Proportional Regime Triggers
            ef, em, es, emac = ef_v[i], em_v[i], es_v[i], emac_v[i]
            rsi = rsi_v[i]
            vol = vol_v[i]
            volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
            
            macro_bull = (c > es) and (es > emac)
            micro_surge = (c >= donch_v[i]) and (rsi >= rsi_sens) and (vol > volsma * 1.2)
            
            if macro_bull and micro_surge:
                invested = cash * 0.90
                entry_px = c * (1.0 + fee_side)
                entry_fee = invested * fee_side
                pos_units = invested / entry_px
                cash -= invested
                stop_px = entry_px - 2.0 * atr
                extreme_px = entry_px
                entry_i = i
                trade_fees = entry_fee
                trades.append({'entry_date': x.index[i], 'entry_px': entry_px})
        else:
            extreme_px = max(extreme_px, high_v[i])
            raw_trail = extreme_px - trail_mult * atr
            stop_px = max(stop_px, raw_trail)
            
            max_hold = int(round(30 * scale_factor))
            
            if low_v[i] <= stop_px or (i - entry_i) >= max_hold:
                exit_px = min(stop_px, c)
                exit_px_net = exit_px * (1.0 - fee_side)
                exit_fee = pos_units * exit_px * fee_side
                trade_fees += exit_fee
                total_fees += trade_fees
                
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
        
    eq = pd.Series(eq_val, index=eq_idx)
    tr_df = pd.DataFrame(trades)
    return tr_df, eq, total_fees

def main():
    print("=" * 80)
    print("📐 EXACT MATHEMATICAL PROPORTIONAL CALIBRATION MATRIX")
    print("=" * 80)
    
    df = load_data('data/BTC_USD_4h.csv')
    
    scale_factors = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0]
    rsi_thresholds = [50.0, 55.0, 60.0]
    trail_multipliers = [2.0, 3.0, 4.0]
    
    best_res = []
    
    for sf in scale_factors:
        for rsi_sens in rsi_thresholds:
            for tm in trail_multipliers:
                for fee in ['TAKER_STANDARD', 'ZERO_FEE']:
                    tr, eq, fees = run_exact_proportional_backtest(df, sf, rsi_sens, tm, fee_preset=fee)
                    m = calculate_micro_metrics(eq, tr, fees)
                    if m and 'Final ($)' in m:
                        best_res.append({
                            'ScaleFactor': sf,
                            'RSISensitivity': rsi_sens,
                            'TrailMult': tm,
                            'FeeRegime': fee,
                            'Final ($)': m['Final ($)'],
                            'Return (%)': m['Return (%)'],
                            'Sharpe': m['Sharpe'],
                            'MaxDD (%)': m['MaxDD (%)'],
                            'Trades': m['Trades'],
                            'TotalFees ($)': m['TotalFeesPaid ($)'],
                        })
                        
    res_df = pd.DataFrame(best_res)
    
    print("\n🏆 TOP 10 CALIBRATED CONFIGURATIONS (TAKER_STANDARD FEE):")
    taker_df = res_df[res_df.FeeRegime == 'TAKER_STANDARD'].sort_values(by='Final ($)', ascending=False)
    print(taker_df.head(10).to_string(index=False))
    
    print("\n🏆 TOP 10 CALIBRATED CONFIGURATIONS (ZERO_FEE):")
    zero_df = res_df[res_df.FeeRegime == 'ZERO_FEE'].sort_values(by='Final ($)', ascending=False)
    print(zero_df.head(10).to_string(index=False))

if __name__ == '__main__':
    main()
