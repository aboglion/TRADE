"""
FRACTAL SCALING & SENSITIVITY CALIBRATION TEST
==============================================
Tests the user's hypothesis:
"What works on a 1-year timeframe can work on a 1-day timeframe if we calibrate parameter sensitivity."

Compares 3 scaling regimes on BTC 4h data:
1. Micro-Unscaled: Fast EMA 9/21/50 (Lookback ~1.5 - 8 days)
2. Medium-Calibrated: EMA 30/75/300 (Lookback ~5 - 50 days)
3. Proportional Fractal Calibration (Macro equivalent on 4h): EMA 120/300/1200 (Lookback ~20 - 200 days)
"""

import pandas as pd
import numpy as np
from micro_engine import load_data, calculate_micro_metrics, FEE_PRESETS, INITIAL_CAPITAL

def add_calibrated_indicators(df, ema_fast, ema_med, ema_slow, ema_macro, donch_bars, rsi_period):
    x = df.copy()
    x["EMA_Fast"] = x.Close.ewm(span=ema_fast, adjust=False).mean()
    x["EMA_Med"] = x.Close.ewm(span=ema_med, adjust=False).mean()
    x["EMA_Slow"] = x.Close.ewm(span=ema_slow, adjust=False).mean()
    x["EMA_Macro"] = x.Close.ewm(span=ema_macro, adjust=False).mean()
    
    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    
    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))
    
    x["DonchHigh"] = x.High.rolling(donch_bars).max().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()
    
    regimes = []
    close_v = x.Close.values
    ef_v, em_v, es_v, emac_v = x.EMA_Fast.values, x.EMA_Med.values, x.EMA_Slow.values, x.EMA_Macro.values
    rsi_v = x.RSI.values
    donch_v = x.DonchHigh.values
    vol_v, volsma_v = x.Volume.values, x.VolSMA20.values

    for i in range(len(x)):
        c = close_v[i]
        ef, em, es, emac = ef_v[i], em_v[i], es_v[i], emac_v[i]
        rsi = rsi_v[i]
        vol = vol_v[i]
        volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
        
        # Macro Bull Gate
        macro_bull = (c > es > emac)
        
        if macro_bull and c >= donch_v[i] and rsi >= 54.0 and vol > volsma * 1.2:
            regimes.append('CALIBRATED_BREAKOUT')
        elif macro_bull and ef > em > es and c > ef:
            regimes.append('CALIBRATED_TREND')
        else:
            regimes.append('NEUTRAL')
            
    x["Regime"] = regimes
    return x.dropna(subset=['ATR', 'RSI', 'EMA_Macro'])

def run_calibrated_backtest(df, fee_preset='TAKER_STANDARD', init_stop_atr=2.0, tp_atr=3.5, trail_atr=3.0):
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
    eq_val, eq_idx = [INITIAL_CAPITAL], [df.index[0]]
    
    for i in range(60, len(df)):
        r = df.iloc[i]
        c = r.Close
        atr = r.ATR
        
        if pos_units == 0.0:
            if r.Regime in ('CALIBRATED_BREAKOUT', 'CALIBRATED_TREND'):
                invested = cash * 0.90
                entry_px = c * (1.0 + fee_side)
                entry_fee = invested * fee_side
                pos_units = invested / entry_px
                cash -= invested
                stop_px = entry_px - init_stop_atr * atr
                extreme_px = entry_px
                entry_i = i
                trade_fees = entry_fee
                trades.append({'entry_date': df.index[i], 'entry_px': entry_px})
        else:
            extreme_px = max(extreme_px, r.High)
            raw_trail = extreme_px - trail_atr * atr
            stop_px = max(stop_px, raw_trail)
            
            if r.Low <= stop_px or (i - entry_i) >= 60:
                exit_px = min(stop_px, c)
                exit_px_net = exit_px * (1.0 - fee_side)
                exit_fee = pos_units * exit_px * fee_side
                trade_fees += exit_fee
                total_fees += trade_fees
                
                net_pnl = pos_units * (exit_px_net - entry_px)
                cash += pos_units * exit_px_net
                
                trades[-1].update({
                    'exit_date': df.index[i],
                    'exit_px': exit_px,
                    'net_pnl': net_pnl,
                    'return_pct_net': net_pnl / (pos_units * entry_px) * 100,
                    'bars_held': i - entry_i
                })
                pos_units = 0.0
                trade_fees = 0.0

        eq_val.append(cash + pos_units * c)
        eq_idx.append(df.index[i])
        
    eq = pd.Series(eq_val, index=eq_idx)
    tr_df = pd.DataFrame(trades)
    return tr_df, eq, total_fees

def main():
    print("=" * 80)
    print("🔬 FRACTAL SCALING & PARAMETER CALIBRATION TEST (BTC 4h Data)")
    print("=" * 80)
    
    df = load_data('data/BTC_USD_4h.csv')
    
    configs = [
        {"name": "1. Micro-Fast (Unscaled)", "fast": 9, "med": 21, "slow": 50, "macro": 200, "donch": 12, "rsi": 7},
        {"name": "2. Medium Calibrated (Intraday)", "fast": 24, "med": 60, "slow": 150, "macro": 600, "donch": 24, "rsi": 14},
        {"name": "3. Proportional Fractal (Macro Equivalent on 4h)", "fast": 120, "med": 300, "slow": 600, "macro": 1200, "donch": 72, "rsi": 28},
    ]
    
    for cfg in configs:
        x = add_calibrated_indicators(df, cfg['fast'], cfg['med'], cfg['slow'], cfg['macro'], cfg['donch'], cfg['rsi'])
        for regime in ['TAKER_STANDARD', 'ZERO_FEE']:
            tr, eq, fees = run_calibrated_backtest(x, fee_preset=regime)
            m = calculate_micro_metrics(eq, tr, fees)
            print(f"\nConfiguration: {cfg['name']} | Fee Regime: {regime}")
            print(f"  Final Equity: ${m.get('Final ($)', 0):,} | Return: {m.get('Return (%)', 0)}% | "
                  f"Sharpe: {m.get('Sharpe', 0)} | MaxDD: {m.get('MaxDD (%)', 0)}% | Trades: {m.get('Trades', 0)} | "
                  f"Fees Paid: ${m.get('TotalFeesPaid ($)', 0)}")

if __name__ == '__main__':
    main()

