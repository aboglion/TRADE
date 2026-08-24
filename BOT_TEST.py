# Walk-Forward + Regime Detection + Trend Rider Engine (v11.0 FAKEOUT-PROTECTED)

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import itertools
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# הגדרות הון וסיכון (מכוון לרווחים גבוהים במגמה + הגנת דשדוש)
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
POSITION_ALLOCATION = 0.90

# Walk-Forward
TRAIN_DAYS = 2700
TEST_DAYS = 540
STEP_DAYS = 540
MIN_TRADES_PER_WF = 3

PARAM_GRID = {
    'atr_trail_mult':  [2.8, 3.5],
    'donchian_period': [20, 30],
    'use_fakeout_filter': [True, False]
}

# ═══════════════════════════════════════════════════════════
# טעינת נתונים
# ═══════════════════════════════════════════════════════════
def load_real_data(filepath='BTC_USD_4h.csv'):
    import os
    if not os.path.exists(filepath) and os.path.exists('CBBTCUSD_4h.csv'):
        filepath = 'CBBTCUSD_4h.csv'
        
    df = pd.read_csv(filepath)
    if 'observation_date' in df.columns:
        df['Date'] = pd.to_datetime(df['observation_date'])
    elif 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        df['Date'] = pd.to_datetime(df.iloc[:, 0])
        
    if 'Close' in df.columns:
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    elif 'CBBTCUSD' in df.columns:
        df['Close'] = pd.to_numeric(df['CBBTCUSD'], errors='coerce')
        
    df = df.dropna(subset=['Date', 'Close']).set_index('Date').sort_index()
    
    if 'Open' not in df.columns:
        df['Open'] = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    if 'High' not in df.columns:
        df['High'] = df[['Open', 'Close']].max(axis=1) * 1.005
    if 'Low' not in df.columns:
        df['Low'] = df[['Open', 'Close']].min(axis=1) * 0.995
    if 'Volume' not in df.columns:
        df['Volume'] = df['Close'].pct_change().abs() * 1e6 + 1e6
        
    print(f"[INFO] Loaded {len(df)} candles | {df.index[0]} -> {df.index[-1]}")
    return df

# ═══════════════════════════════════════════════════════════
# אינדיקטורים
# ═══════════════════════════════════════════════════════════
def add_indicators(df):
    x = df.copy()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian20"] = x.High.rolling(20).max().shift(1)
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    x["VolMA20"] = x.Volume.rolling(20).mean()
    x["VolRatio"] = x.Volume / x.VolMA20
    
    # ATR
    prev = x.Close.shift()
    tr = pd.concat([
        x.High - x.Low,
        (x.High - prev).abs(),
        (x.Low - prev).abs()
    ], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    
    # ADX
    up_move = x.High - x.High.shift(1)
    down_move = x.Low.shift(1) - x.Low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_smooth = pd.Series(tr).ewm(alpha=1/14, min_periods=14).mean()
    plus_di = 100 * pd.Series(plus_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / tr_smooth
    minus_di = 100 * pd.Series(minus_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["ADX"] = dx.ewm(alpha=1/14, min_periods=14).mean()
    
    return x.dropna()

# ═══════════════════════════════════════════════════════════
# מנוע מסחר (Fakeout-Protected Trend Rider)
# ═══════════════════════════════════════════════════════════
def run_backtest_v9(df, params, capital=INITIAL_CAPITAL):
    fee_slip = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    atr_trail = params.get('atr_trail_mult', 3.5)
    donchian_col = 'Donchian30' if params.get('donchian_period', 30) == 30 else 'Donchian20'
    use_fakeout_filter = params.get('use_fakeout_filter', True)
    
    cash = capital
    in_pos = False
    entry_px = 0
    units = 0
    highest_px = 0
    trades = []
    equity_val = [capital]
    equity_idx = [df.index[0]]
    
    for i in range(50, len(df)):
        r = df.iloc[i]
        curr_price = r.Close
        
        if not in_pos:
            trend_ok = r.Close > r.EMA50 > r.EMA200
            
            if use_fakeout_filter:
                # Clean margin + ADX strength + Volume confirmation
                breakout_ok = r.Close >= r[donchian_col] * 1.008
                adx_ok = r.ADX >= 18.0
                vol_ok = r.VolRatio >= 1.05
                signal = trend_ok and breakout_ok and adx_ok and vol_ok
            else:
                breakout_ok = r.Close >= r[donchian_col]
                signal = trend_ok and breakout_ok
                
            if signal:
                in_pos = True
                entry_px = r.Close * (1 + fee_slip)
                units = (cash * POSITION_ALLOCATION) / entry_px
                highest_px = entry_px
                trades.append({
                    'entry_date': df.index[i],
                    'entry': entry_px
                })
        else:
            highest_px = max(highest_px, r.High)
            stop_px = highest_px - atr_trail * r.ATR
            
            exit_signal = (r.Low <= stop_px) or (r.Close < r.EMA50)
            
            if exit_signal:
                in_pos = False
                exit_px = min(stop_px, r.Close) * (1 - fee_slip)
                pnl = units * (exit_px - entry_px)
                cash += pnl
                
                trades[-1]['exit_date'] = df.index[i]
                trades[-1]['exit'] = exit_px
                trades[-1]['pnl_usd'] = pnl
                trades[-1]['return_pct'] = (exit_px - entry_px) / entry_px * 100
                trades[-1]['reason'] = 'trend_exit'
                units = 0
                
        current_val = cash + (units * curr_price if in_pos else 0)
        equity_val.append(current_val)
        equity_idx.append(df.index[i])
        
    trades_df = pd.DataFrame(trades)
    eq = pd.Series(equity_val, index=equity_idx, name='Equity')
    return trades_df, eq

if __name__ == "__main__":
    df = load_real_data()
    df = add_indicators(df)
    trades, eq = run_backtest_v9(df, {'atr_trail_mult': 3.5, 'use_fakeout_filter': True})
    print(f"[SUCCESS] Strategy v11.0 execution completed. Trades: {len(trades)} | Final Capital: ${eq.iloc[-1]:,.2f}")
