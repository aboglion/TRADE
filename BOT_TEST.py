

# Walk-Forward + Regime Detection + Dynamic Stop + Graduated Exit

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import itertools
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# הגדרות
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 10_000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
RISK_FRACTION = 0.01
MAX_ALLOCATION = 0.95

# Walk-Forward
TRAIN_DAYS = 450
TEST_DAYS = 90
STEP_DAYS = 90
MIN_TRADES_PER_WF = 8

# Grid Search
PARAM_GRID = {
    'atr_stop_mult':   [1.5, 2.0, 2.5],
    'atr_target_mult': [2.0, 3.0, 4.0, 5.0],
    'hold_bars':       [10, 15, 20, 30],
    'rsi_high':        [60, 65, 70],
    'rsi_low':         40,
    'vol_mult':        1.25,
    'mom_min':         0.005,
    'mom_max':         0.08,
    'cooldown_bars':   3,
    'graduated':       True,
}

# ═══════════════════════════════════════════════════════════
# טעינת נתונים אמיתיים
# ═══════════════════════════════════════════════════════════
def load_real_data(filepath='CBBTCUSD.csv'):
    df = pd.read_csv(filepath)
    df['Date'] = pd.to_datetime(df['observation_date'])
    df['Close'] = pd.to_numeric(df['CBBTCUSD'], errors='coerce')
    df = df[['Date', 'Close']].dropna()
    df = df.set_index('Date')
    
    # יצירת OHLC מנתוני סגירה
    df['Open'] = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    df['High'] = df[['Open', 'Close']].max(axis=1) * 1.005
    df['Low'] = df[['Open', 'Close']].min(axis=1) * 0.995
    df['Volume'] = df['Close'].pct_change().abs() * 1e6 + 1e6
    
    print(f"[INFO] Loaded {len(df)} days | {df.index[0].date()} -> {df.index[-1].date()}")
    return df

# ═══════════════════════════════════════════════════════════
# אינדיקטורים
# ═══════════════════════════════════════════════════════════
def add_indicators(df):
    x = df.copy()
    
    # Trend
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    
    # RSI
    delta = x.Close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["RSI"] = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))
    
    # MACD
    x["MACD"] = x.Close.ewm(span=12, adjust=False).mean() - x.Close.ewm(span=26, adjust=False).mean()
    x["Signal"] = x.MACD.ewm(span=9, adjust=False).mean()
    x["Hist"] = x.MACD - x.Signal
    
    # Bollinger
    x["BBmid"] = x.Close.rolling(20).mean()
    sd = x.Close.rolling(20).std()
    x["BBup"] = x.BBmid + 2 * sd
    x["BBlow"] = x.BBmid - 2 * sd
    
    # ATR
    prev = x.Close.shift()
    tr = pd.concat([
        x.High - x.Low,
        (x.High - prev).abs(),
        (x.Low - prev).abs()
    ], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["ATRpct"] = x.ATR / x.Close
    x["VolAvg"] = x.ATRpct.rolling(60).mean()
    
    # Momentum
    x["Mom5"] = x.Close.pct_change(5)
    x["Mom10"] = x.Close.pct_change(10)
    x["VolMA"] = x.Volume.rolling(30).mean()
    
    # Regime Detection
    x["Regime"] = detect_regime(x)
    
    return x.dropna()

# ═══════════════════════════════════════════════════════════
# זיהוי רג'ים
# ═══════════════════════════════════════════════════════════
def detect_regime(df):
    regime = pd.Series(index=df.index, dtype='object')
    
    for i in range(200, len(df)):
        price = df.Close.iloc[i]
        ema200 = df.EMA200.iloc[i]
        ema50 = df.EMA50.iloc[i]
        ret_30 = (price - df.Close.iloc[i-30]) / df.Close.iloc[i-30]
        vol_30 = df.Close.iloc[i-30:i].pct_change().std()
        
        if price < ema200 * 0.97 and ema50 < ema200:
            regime.iloc[i] = 'BEAR'
        elif ret_30 < -0.15:
            regime.iloc[i] = 'CRASH'
        elif ret_30 > 0.10 and price > ema50 > ema200:
            regime.iloc[i] = 'STRONG_BULL'
        elif ret_30 > 0.03 and price > ema200:
            regime.iloc[i] = 'BULL'
        elif ret_30 > -0.03:
            regime.iloc[i] = 'SIDEWAYS'
        else:
            regime.iloc[i] = 'WEAK_BEAR'
    
    regime.iloc[:200] = 'BULL'
    return regime

# ═══════════════════════════════════════════════════════════
# פילטרים חכמים
# ═══════════════════════════════════════════════════════════
def check_signal_v9(df, i, params):
    r = df.iloc[i]
    p = df.iloc[i-1]
    regime = r['Regime']
    
    # לא לסחור בדוב או קריסה
    if regime in ['BEAR', 'CRASH']:
        return False
    
    # פילטר 1: מגמה חיובית
    if not (r.Close > r.EMA50 > r.EMA200):
        return False
    
    # פילטר 2: RSI אופטימלי
    if not (params['rsi_low'] < r.RSI < params['rsi_high']):
        return False
    
    # פילטר 3: MACD חיובי ועולה
    if not (r.Hist > 0 and r.Hist > p.Hist):
        return False
    
    # פילטר 4: מחיר מעל BB middle
    if not (r.Close > r.BBmid):
        return False
    
    # פילטר 5: לא ליד BB upper
    if r.Close > r.BBup * 0.98:
        return False
    
    # פילטר 6: תנודתיות סבירה
    if r.ATRpct > r.VolAvg * params['vol_mult']:
        return False
    
    # פילטר 7: מומנטום מתון
    if not (params['mom_min'] < r.Mom5 < params['mom_max']):
        return False
    
    # פילטר 8: מומנטום 10 ימים חיובי
    if not (r.Mom10 > 0):
        return False
    
    # פילטר 9: נפח מעל ממוצע
    if not (r.Volume > r.VolMA):
        return False
    
    # פילטר 10: אין ירידה חדה ב-5 ימים
    ret_5 = (r.Close - df.Close.iloc[i-5]) / df.Close.iloc[i-5]
    if ret_5 < -0.03:
        return False
    
    return True

# ═══════════════════════════════════════════════════════════
# מנוע מסחר
# ═══════════════════════════════════════════════════════════
def run_backtest_v9(df, params, capital=INITIAL_CAPITAL):
    fee_slip = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    hold_bars = params['hold_bars']
    cooldown = params.get('cooldown_bars', 3)
    graduated = params.get('graduated', True)
    
    trades = []
    equity_idx = [df.index[0]]
    equity_val = [capital]
    cash = capital
    
    i = 200
    last_loss_bar = -999
    
    while i < len(df) - hold_bars - 1:
        # Cooldown אחרי הפסד
        if i - last_loss_bar < cooldown:
            i += 1
            continue
        
        if not check_signal_v9(df, i, params):
            i += 1
            continue
        
        entry_i = i + 1
        entry = df.Open.iloc[entry_i] * (1 + fee_slip)
        atr = df.ATR.iloc[i]
        
        # סטופ דינמי מבוסס ATR
        stop_mult = params['atr_stop_mult']
        stop = entry - stop_mult * atr
        target = entry + params['atr_target_mult'] * atr
        
        risk_per_unit = entry - stop
        if risk_per_unit <= 0:
            i += 1
            continue
        
        units = min(
            (cash * RISK_FRACTION) / risk_per_unit,
            (cash * MAX_ALLOCATION) / entry
        )
        
        if units <= 0:
            i += 1
            continue
        
        # ניהול העסקה
        if graduated:
            # יציאה מדורגת אמיתית
            total_pnl = 0
            remaining_units = units
            
            for j in range(entry_i, min(entry_i + hold_bars + 1, len(df))):
                current_low = df.Low.iloc[j]
                current_high = df.High.iloc[j]
                
                # סטופ
                if current_low <= stop:
                    exit_px = stop * (1 - fee_slip)
                    pnl = remaining_units * (exit_px - entry)
                    total_pnl += pnl
                    break
                
                # יעד 1: +50% מהיעד -> יציאה 40%
                target_1 = entry + (target - entry) * 0.5
                if current_high >= target_1 and remaining_units > units * 0.6:
                    exit_px = target_1 * (1 - fee_slip)
                    portion = remaining_units * 0.4
                    pnl = portion * (exit_px - entry)
                    total_pnl += pnl
                    remaining_units -= portion
                    stop = entry  # העלאת סטופ לנקודת כניסה
                
                # יעד 2: +75% מהיעד -> יציאה 30%
                target_2 = entry + (target - entry) * 0.75
                if current_high >= target_2 and remaining_units > units * 0.3:
                    exit_px = target_2 * (1 - fee_slip)
                    portion = remaining_units * 0.5
                    pnl = portion * (exit_px - entry)
                    total_pnl += pnl
                    remaining_units -= portion
                
                # יעד מלא
                if current_high >= target:
                    exit_px = target * (1 - fee_slip)
                    pnl = remaining_units * (exit_px - entry)
                    total_pnl += pnl
                    break
                
                # Timeout
                if j == entry_i + hold_bars:
                    exit_px = df.Close.iloc[j] * (1 - fee_slip)
                    pnl = remaining_units * (exit_px - entry)
                    total_pnl += pnl
                    break
            
            ret = total_pnl / cash
            cash += total_pnl
            
            trades.append({
                'entry_date': df.index[entry_i],
                'exit_date': df.index[min(j, len(df)-1)],
                'entry': entry,
                'exit': df.Close.iloc[min(j, len(df)-1)],
                'return_pct': 100 * ret,
                'pnl_usd': total_pnl,
                'reason': 'graduated',
                'bars_held': min(j, len(df)-1) - entry_i,
            })
            
            if total_pnl < 0:
                last_loss_bar = min(j, len(df)-1)
            
            equity_idx.append(df.index[min(j, len(df)-1)])
            equity_val.append(cash)
            i = min(j, len(df)-1) + 1
        else:
            # יציאה רגילה
            exit_i = entry_i + hold_bars
            reason = 'timeout'
            exit_raw = df.Close.iloc[min(exit_i, len(df)-1)]
            
            for j in range(entry_i, min(entry_i + hold_bars + 1, len(df))):
                if df.Low.iloc[j] <= stop:
                    exit_i, reason, exit_raw = j, 'stop', stop
                    break
                if df.High.iloc[j] >= target:
                    exit_i, reason, exit_raw = j, 'target', target
                    break
            
            exit_px = exit_raw * (1 - fee_slip)
            pnl = units * (exit_px - entry)
            ret = pnl / cash
            cash += pnl
            
            trades.append({
                'entry_date': df.index[entry_i],
                'exit_date': df.index[exit_i],
                'entry': entry,
                'exit': exit_px,
                'return_pct': 100 * ret,
                'pnl_usd': pnl,
                'reason': reason,
                'bars_held': exit_i - entry_i,
            })
            
            if pnl < 0:
                last_loss_bar = exit_i
            
            equity_idx.append(df.index[exit_i])
            equity_val.append(cash)
            i = exit_i + 1
    
    trades_df = pd.DataFrame(trades)
    eq = pd.Series(equity_val, index=equity_idx, name='Equity')
    eq = eq.reindex(df.index, method='ffill').fillna(capital)
    return trades_df, eq

# ═══════════════════════════════════════════════════════════
# סטטיסטיקות
# ═══════════════════════════════════════════════════════════
def calc_stats(trades, equity, label=""):
    if trades.empty:
        return {"label": label, "trades": 0}
    
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
    
    win_mask = (trades.pnl_usd > 0).tolist()
    max_consec_loss = 0
    curr = 0
    for w in win_mask:
        if not w:
            curr += 1
            max_consec_loss = max(max_consec_loss, curr)
        else:
            curr = 0
    
    return {
        "label": label,
        "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "avg_trade_pct": trades.return_pct.mean(),
        "net_pnl_usd": trades.pnl_usd.sum(),
        "net_return_pct": (equity.iloc[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100,
        "profit_factor": gross_win / gross_loss,
        "max_drawdown_pct": max_dd * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_consec_loss": max_consec_loss,
    }

# ═══════════════════════════════════════════════════════════
# Monte Carlo
# ═══════════════════════════════════════════════════════════
def monte_carlo(trades, n_sims=2000):
    if trades.empty or len(trades) < 5:
        return None
    pnls = trades.pnl_usd.values.copy()
    final_vals = []
    for _ in range(n_sims):
        np.random.shuffle(pnls)
        eq = INITIAL_CAPITAL + np.cumsum(pnls)
        final_vals.append(eq[-1])
    final_vals = np.array(final_vals)
    return {
        "mean_final": final_vals.mean(),
        "pct5": np.percentile(final_vals, 5),
        "pct95": np.percentile(final_vals, 95),
        "prob_profit": (final_vals > INITIAL_CAPITAL).mean() * 100,
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
        
        print(f"\nWF #{wf_num}: Train {train_df.index[0].date()} -> {train_df.index[-1].date()} | Test {test_df.index[0].date()} -> {test_df.index[-1].date()}")
        
        best_score = -np.inf
        best_params = None
        
        for combo in itertools.product(*list_vals):
            params = base_params.copy()
            params.update(dict(zip(list_keys, combo)))
            tr, eq = run_backtest_v9(train_df, params)
            if len(tr) < MIN_TRADES_PER_WF:
                continue
            st = calc_stats(tr, eq)
            if st["max_drawdown_pct"] < 0:
                score = (st["net_return_pct"] / abs(st["max_drawdown_pct"])) * np.sqrt(min(len(tr), 60))
            else:
                score = st["net_return_pct"] * np.sqrt(min(len(tr), 60))
            if score > best_score:
                best_score = score
                best_params = params
        
        if best_params is None:
            start += STEP_DAYS
            wf_num += 1
            continue
        
        test_trades, test_eq = run_backtest_v9(test_df, best_params)
        test_stats = calc_stats(test_trades, test_eq, f"WF{wf_num}_OOS")
        test_stats["wf_num"] = wf_num
        all_test_stats.append(test_stats)
        
        test_trades = test_trades.copy()
        test_trades["wf_num"] = wf_num
        all_test_trades.append(test_trades)
        
        norm_eq = test_eq / test_eq.iloc[0]
        all_equity.append(norm_eq)
        
        print(f"  OOS: {test_stats['trades']} trades | WR: {test_stats['win_rate_pct']:.1f}% | Return: {test_stats['net_return_pct']:+.1f}%")
        
        start += STEP_DAYS
        wf_num += 1
    
    if not all_test_stats:
        return pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)
    
    stats_df = pd.DataFrame(all_test_stats)
    trades_df = pd.concat(all_test_trades, ignore_index=True)
    continuous_eq = pd.concat(all_equity) * INITIAL_CAPITAL
    return stats_df, trades_df, continuous_eq

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 70)
    print("BTC-USD ULTIMATE STRATEGY v9.0")
    print("=" * 70)
    
    # 1. Load data
    df = load_real_data()
    df = add_indicators(df)
    print(f"[INFO] Total bars: {len(df)}")
    
    # 2. Walk-Forward
    stats_df, trades_df, oos_equity = walk_forward(df, PARAM_GRID)
    
    if stats_df.empty:
        print("[ERROR] No walk-forward windows completed.")
        exit(1)
    
    # 3. Summary
    print("\n" + "=" * 70)
    print("AGGREGATE OOS SUMMARY")
    print("=" * 70)
    agg = {
        "total_trades": int(stats_df.trades.sum()),
        "avg_win_rate": stats_df.win_rate_pct.mean(),
        "avg_profit_factor": stats_df.profit_factor.mean(),
        "total_return": stats_df.net_return_pct.sum(),
        "avg_sharpe": stats_df.sharpe.mean(),
        "avg_calmar": stats_df.calmar.mean(),
    }
    for k, v in agg.items():
        print(f"  {k:25s}: {v:,.2f}")
    
    # 4. Monte Carlo
    mc = monte_carlo(trades_df)
    if mc:
        print("\nMONTE CARLO:")
        for k, v in mc.items():
            print(f"  {k:20s}: {v:,.2f}")
    
    # 5. Save
    stats_df.to_csv("v9_summary.csv", index=False)
    trades_df.to_csv("v9_trades.csv", index=False)
    oos_equity.to_frame("Equity").to_csv("v9_equity.csv")
    
    print("\n[INFO] Files saved.")
