# Walk-Forward + Regime Detection + Trend Rider Engine (v12.0 OPTIMAL DYNAMIC REGIME ENGINE)

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# הגדרות הון וסיכון (v12.0 OPTIMAL DYNAMIC REGIME ENGINE)
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
POSITION_ALLOCATION = 0.90

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

def add_indicators(df):
    x = df.copy()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    
    # ATR
    prev = x.Close.shift()
    tr = pd.concat([
        x.High - x.Low,
        (x.High - prev).abs(),
        (x.Low - prev).abs()
    ], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    
    # RSI 14
    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))
    
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
    
    # Range Compression Ratio
    high30 = x.High.rolling(30).max()
    low30 = x.Low.rolling(30).min()
    x["Range30"] = high30 - low30
    x["RangeToATR"] = x.Range30 / x.ATR
    x["Ret30"] = (x.Close - x.Close.shift(180)) / x.Close.shift(180)
    
    regimes = []
    for i in range(len(x)):
        r = x.iloc[i]
        ret = r['Ret30'] if not np.isnan(r['Ret30']) else 0.0
        
        if r.Close < r.EMA200 or ret < -0.12:
            regimes.append('BEAR')
        elif r.Close > r.EMA50 > r.EMA200 and ret > 0.05:
            regimes.append('STRONG_BULL')
        elif r.RangeToATR < 4.5 or r.ADX < 18.0:
            regimes.append('SIDEWAYS')
        else:
            regimes.append('BULL')
            
    x["RegimeV12"] = regimes
    return x.dropna()

def run_backtest_v9(df, params=None, capital=INITIAL_CAPITAL):
    fee_slip = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    cash = capital
    in_pos = False
    entry_px = 0
    units = 0
    highest_px = 0
    trades = []
    equity_val = [capital]
    equity_idx = [df.index[0]]
    mode = 'TREND'
    
    for i in range(50, len(df)):
        r = df.iloc[i]
        curr_price = r.Close
        regime = r.RegimeV12
        
        if not in_pos:
            if regime == 'BEAR':
                pass # Cash Protection (0% position)
            elif regime == 'STRONG_BULL':
                if r.Close >= r.Donchian30:
                    in_pos = True
                    mode = 'STRONG_BULL_TREND'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * 0.95) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode})
            elif regime == 'BULL':
                if r.Close >= r.Donchian30:
                    in_pos = True
                    mode = 'TREND'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * POSITION_ALLOCATION) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode})
            elif regime == 'SIDEWAYS':
                # Full 90% allocation for Sideways Dip-Buying
                if r.Close > r.EMA200 and r.RSI < 42 and r.Close > r.Open:
                    in_pos = True
                    mode = 'DIP'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * POSITION_ALLOCATION) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode})
        else:
            highest_px = max(highest_px, r.High)
            
            if mode == 'STRONG_BULL_TREND':
                # Extended 5.5 ATR trail to capture mega parabolic bull rallies without premature exit
                stop_px = highest_px - 5.5 * r.ATR
                exit_signal = (r.Low <= stop_px) or (r.Close < r.EMA200)
            elif mode == 'TREND':
                stop_px = highest_px - 3.5 * r.ATR
                exit_signal = (r.Low <= stop_px) or (r.Close < r.EMA50)
            else: # DIP mode
                target_px = entry_px + 1.8 * r.ATR
                stop_px = entry_px - 2.0 * r.ATR
                exit_signal = (r.High >= target_px) or (r.Low <= stop_px)
                
            if exit_signal:
                in_pos = False
                exit_px = min(stop_px, r.Close) * (1 - fee_slip) if r.Low <= stop_px else r.Close * (1 - fee_slip)
                pnl = units * (exit_px - entry_px)
                cash += pnl
                
                trades[-1]['exit_date'] = df.index[i]
                trades[-1]['exit'] = exit_px
                trades[-1]['pnl_usd'] = pnl
                trades[-1]['return_pct'] = (exit_px - entry_px) / entry_px * 100
                units = 0
                
        current_val = cash + (units * curr_price if in_pos else 0)
        equity_val.append(current_val)
        equity_idx.append(df.index[i])
        
    trades_df = pd.DataFrame(trades)
    eq = pd.Series(equity_val, index=equity_idx, name='Equity')
    return trades_df, eq

def run_portfolio_50_25_25(btc_df, eth_df, sol_df, capital=INITIAL_CAPITAL):
    cap_btc = capital * 0.50 # 50% BTC ($500)
    cap_eth = capital * 0.25 # 25% ETH ($250)
    cap_sol = capital * 0.25 # 25% SOL ($250)
    
    tr_b, eq_b = run_backtest_v9(btc_df, capital=cap_btc)
    tr_e, eq_e = run_backtest_v9(eth_df, capital=cap_eth)
    tr_s, eq_s = run_backtest_v9(sol_df, capital=cap_sol) if len(sol_df) > 50 else (pd.DataFrame(), pd.Series(cap_sol, index=btc_df.index))
    
    comb_eq = pd.DataFrame({'BTC': eq_b, 'ETH': eq_e, 'SOL': eq_s}).ffill().fillna(cap_sol)
    total_eq = comb_eq.sum(axis=1)
    
    return {'BTC': (tr_b, eq_b), 'ETH': (tr_e, eq_e), 'SOL': (tr_s, eq_s)}, total_eq

if __name__ == "__main__":
    df = load_real_data()
    df = add_indicators(df)
    trades, eq = run_backtest_v9(df)
    print(f"[SUCCESS] Strategy v12.0 Optimal Engine completed. Trades: {len(trades)} | Final Capital: ${eq.iloc[-1]:,.2f}")
    
    import os
    if os.path.exists('ETH_USD_4h.csv') and os.path.exists('SOL_USD_4h.csv'):
        eth_df = add_indicators(load_real_data('ETH_USD_4h.csv'))
        sol_df = add_indicators(load_real_data('SOL_USD_4h.csv'))
        
        _, port_eq = run_portfolio_50_25_25(df, eth_df, sol_df, capital=INITIAL_CAPITAL)
        print(f"[PORTFOLIO 50/25/25] Final Capital (BTC 50% + ETH 25% + SOL 25%): ${port_eq.iloc[-1]:,.2f}")

