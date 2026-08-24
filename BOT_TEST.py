# Walk-Forward + Regime Detection + Trend Rider Engine (v10.5 HIGH-PROFIT)

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import itertools
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# הגדרות הון וסיכון (מכוון לרווחים גבוהים במגמה)
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
POSITION_ALLOCATION = 0.90   # הקצאת הון במגמה עולה לתפיסת תשואה מירבית

# Walk-Forward
TRAIN_DAYS = 2700
TEST_DAYS = 540
STEP_DAYS = 540
MIN_TRADES_PER_WF = 3

# Grid Search (אופטימיזציה למקסום רווח נטו)
PARAM_GRID = {
    'atr_trail_mult':  [2.8, 3.5],
    'donchian_period': [20, 30],
    'use_ema_exit':    [True, False]
}

# ═══════════════════════════════════════════════════════════
# טעינת נתונים
# ═══════════════════════════════════════════════════════════
def load_real_data(filepath='CBBTCUSD_4h.csv'):
    import os
    if not os.path.exists(filepath) and os.path.exists('CBBTCUSD.csv'):
        filepath = 'CBBTCUSD.csv'
        
    df = pd.read_csv(filepath)
    if 'observation_date' in df.columns:
        df['Date'] = pd.to_datetime(df['observation_date'])
    elif 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        df['Date'] = pd.to_datetime(df.iloc[:, 0])
        
    if 'CBBTCUSD' in df.columns:
        df['Close'] = pd.to_numeric(df['CBBTCUSD'], errors='coerce')
    elif 'Close' in df.columns:
        df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
        
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
    
    prev = x.Close.shift()
    tr = pd.concat([
        x.High - x.Low,
        (x.High - prev).abs(),
        (x.Low - prev).abs()
    ], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    
    return x.dropna()

# ═══════════════════════════════════════════════════════════
# מנוע מסחר Trend Rider (ממקסם רווחים במגמות עולות)
# ═══════════════════════════════════════════════════════════
def run_backtest_v9(df, params, capital=INITIAL_CAPITAL):
    fee_slip = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    atr_trail = params.get('atr_trail_mult', 3.5)
    donchian_col = 'Donchian30' if params.get('donchian_period', 30) == 30 else 'Donchian20'
    use_ema_exit = params.get('use_ema_exit', True)
    
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
            # תנאי כניסה: מגמה עולה (מחיר > EMA50 > EMA200) + פריצת דונצ'יאן
            if r.Close > r.EMA50 > r.EMA200 and r.Close >= r[donchian_col]:
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
            
            # תנאי יציאה: שבירת ATR Trailing Stop או שבירת EMA50
            exit_signal = (r.Low <= stop_px) or (use_ema_exit and r.Close < r.EMA50)
            
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

# ═══════════════════════════════════════════════════════════
# סטטיסטיקות
# ═══════════════════════════════════════════════════════════
def calc_stats(trades, equity, label=""):
    init_cap = equity.iloc[0] if len(equity) > 0 else INITIAL_CAPITAL
    if trades.empty:
        return {
            "label": label,
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_trade_pct": 0.0,
            "net_pnl_usd": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "max_consec_loss": 0,
        }
    
    wins = trades[trades.pnl_usd > 0]
    losses = trades[trades.pnl_usd <= 0]
    gross_win = wins.pnl_usd.sum() if len(wins) else 0
    gross_loss = abs(losses.pnl_usd.sum()) if len(losses) else 0.001
    
    cummax = equity.cummax()
    dd = (equity - cummax) / cummax
    max_dd = dd.min()
    
    rets = equity.pct_change().dropna()
    ann_factor = np.sqrt(365)
    sharpe = (rets.mean() / rets.std()) * ann_factor if rets.std() > 0 else 0
    downside = rets[rets < 0].std()
    sortino = (rets.mean() / downside) * ann_factor if downside > 0 else 0
    calmar = (rets.mean() * 365) / abs(max_dd) if max_dd != 0 else 0
    
    return {
        "label": label,
        "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "avg_trade_pct": trades.return_pct.mean(),
        "net_pnl_usd": trades.pnl_usd.sum(),
        "net_return_pct": (equity.iloc[-1] - init_cap) / init_cap * 100,
        "profit_factor": gross_win / gross_loss,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_consec_loss": 0,
    }

# ═══════════════════════════════════════════════════════════
# Walk-Forward
# ═══════════════════════════════════════════════════════════
def walk_forward(df, param_grid):
    list_keys = [k for k, v in param_grid.items() if isinstance(v, list)]
    list_vals = [param_grid[k] for k in list_keys]
    base_params = {k: v for k, v in param_grid.items() if k not in list_keys}
    
    all_test_stats = []
    all_test_trades = []
    all_equity = []
    
    start = 200
    wf_num = 1
    
    while start + TRAIN_DAYS + TEST_DAYS <= len(df):
        train_df = df.iloc[start:start+TRAIN_DAYS]
        test_df = df.iloc[start+TRAIN_DAYS:start+TRAIN_DAYS+TEST_DAYS]
        
        print(f"WF #{wf_num}: Train {train_df.index[0].date()} -> {train_df.index[-1].date()} | Test {test_df.index[0].date()} -> {test_df.index[-1].date()}")
        
        best_score = -np.inf
        best_params = None
        
        for combo in itertools.product(*list_vals):
            params = base_params.copy()
            params.update(dict(zip(list_keys, combo)))
            tr, eq = run_backtest_v9(train_df, params, capital=INITIAL_CAPITAL)
            if len(tr) < MIN_TRADES_PER_WF:
                continue
            st = calc_stats(tr, eq)
            score = st["net_return_pct"]
            if score > best_score:
                best_score = score
                best_params = params
        
        if best_params is None:
            best_params = {'atr_trail_mult': 3.5, 'donchian_period': 30, 'use_ema_exit': True}
        
        test_trades, test_eq = run_backtest_v9(test_df, best_params, capital=INITIAL_CAPITAL)
        test_stats = calc_stats(test_trades, test_eq, f"WF{wf_num}_OOS")
        test_stats["wf_num"] = wf_num
        all_test_stats.append(test_stats)
        
        if not test_trades.empty:
            test_trades = test_trades.copy()
            test_trades["wf_num"] = wf_num
            all_test_trades.append(test_trades)
            
        norm_eq = test_eq / test_eq.iloc[0]
        all_equity.append(norm_eq)
        
        print(f"  OOS: {test_stats['trades']} trades | WR: {test_stats['win_rate_pct']:.1f}% | Return: {test_stats['net_return_pct']:+.1f}% | Final: ${test_eq.iloc[-1]:,.2f}")
        
        start += STEP_DAYS
        wf_num += 1
    
    if not all_test_stats:
        return pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)
    
    stats_df = pd.DataFrame(all_test_stats)
    non_empty_trades = [t for t in all_test_trades if not t.empty]
    trades_df = pd.concat(non_empty_trades, ignore_index=True) if non_empty_trades else pd.DataFrame()
    continuous_eq = pd.concat(all_equity) * INITIAL_CAPITAL
    return stats_df, trades_df, continuous_eq

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 BTC-USD HIGH-PROFIT TREND RIDER (v10.5)")
    print("=" * 70)
    
    df = load_real_data()
    df = add_indicators(df)
    
    stats_df, trades_df, oos_equity = walk_forward(df, PARAM_GRID)
    
    print("\n" + "=" * 70)
    print("AGGREGATE SUMMARY")
    print("=" * 70)
    print(f"  סה\"כ עסקאות ב-OOS: {stats_df.trades.sum()}")
    print(f"  תשואה ממוצעת לחלון: {stats_df.net_return_pct.mean():+.2f}%")
    
    stats_df.to_csv("v9_summary.csv", index=False)
    if not trades_df.empty:
        trades_df.to_csv("v9_trades.csv", index=False)
    oos_equity.to_frame("Equity").to_csv("v9_equity.csv")
    
    print("\n[INFO] Done.")
