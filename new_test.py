import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# הגדרות הון וסיכון (v12.1 ENHANCED WALK-FORWARD READY ENGINE)
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
POSITION_ALLOCATION = 0.90

# ───────────────────────────────────────────────────────────
# 1. SYNTHETIC DATA GENERATOR (For immediate testing)
# ───────────────────────────────────────────────────────────
def generate_synthetic_data(ticker='BTC', days=1000):
    np.random.seed(42)
    dates = pd.date_range(end=datetime.today(), periods=days*6, freq='4h') # 6 periods per day
    returns = np.random.normal(0.0005, 0.015, len(dates))
    close = 100 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({'Date': dates, 'Close': close})
    df['Open'] = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    df['High'] = df[['Open', 'Close']].max(axis=1) * (1 + np.random.uniform(0, 0.01, len(df)))
    df['Low'] = df[['Open', 'Close']].min(axis=1) * (1 - np.random.uniform(0, 0.01, len(df)))
    df['Volume'] = np.random.uniform(1e6, 5e6, len(df))
    return df.set_index('Date').sort_index()

def load_real_data(filepath='BTC_USD_4h.csv'):
    import os
    if not os.path.exists(filepath):
        print(f"[INFO] '{filepath}' not found. Generating synthetic data for testing.")
        return generate_synthetic_data(filepath.split('_')[0])
    
    df = pd.read_csv(filepath)
    date_col = 'observation_date' if 'observation_date' in df.columns else 'Date'
    df['Date'] = pd.to_datetime(df[date_col])
    
    close_col = 'Close' if 'Close' in df.columns else 'CBBTCUSD'
    df['Close'] = pd.to_numeric(df[close_col], errors='coerce')
    df = df.dropna(subset=['Date', 'Close']).set_index('Date').sort_index()
    
    df['Open'] = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    df['High'] = df[['Open', 'Close']].max(axis=1) * 1.005
    df['Low'] = df[['Open', 'Close']].min(axis=1) * 0.995
    df['Volume'] = df['Close'].pct_change().abs() * 1e6 + 1e6
    print(f"[INFO] Loaded {len(df)} candles | {df.index[0]} -> {df.index[-1]}")
    return df

# ───────────────────────────────────────────────────────────
# 2. INDICATORS & REGIME DETECTION
# ───────────────────────────────────────────────────────────
def add_indicators(df):
    x = df.copy()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)

    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))

    up_move = x.High - x.High.shift(1)
    down_move = x.Low.shift(1) - x.Low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_smooth = pd.Series(tr).ewm(alpha=1/14, min_periods=14).mean()
    plus_di = 100 * pd.Series(plus_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / tr_smooth
    minus_di = 100 * pd.Series(minus_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["ADX"] = dx.ewm(alpha=1/14, min_periods=14).mean()

    high30, low30 = x.High.rolling(30).max(), x.Low.rolling(30).min()
    x["RangeToATR"] = (high30 - low30) / x.ATR
    
    # 180 periods on 4h chart = 30 Days. Renamed for clarity.
    x["Ret30D"] = (x.Close - x.Close.shift(180)) / x.Close.shift(180)

    regimes = []
    for i in range(len(x)):
        r = x.iloc[i]
        ret = r['Ret30D'] if not np.isnan(r['Ret30D']) else 0.0

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

# ───────────────────────────────────────────────────────────
# 3. BACKTEST ENGINE
# ───────────────────────────────────────────────────────────
def run_backtest_v9(df, capital=INITIAL_CAPITAL):
    fee_slip = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    cash = capital
    in_pos = False
    entry_px = units = highest_px = 0
    trades = []
    equity_val, equity_idx = [capital], [df.index[0]]

    for i in range(50, len(df)):
        r = df.iloc[i]
        curr_price = r.Close
        regime = r.RegimeV12

        if not in_pos:
            if regime == 'STRONG_BULL' and r.Close >= r.Donchian30:
                in_pos, mode = True, 'STRONG_BULL_TREND'
            elif regime == 'BULL' and r.Close >= r.Donchian30:
                in_pos, mode = True, 'TREND'
            elif regime == 'SIDEWAYS' and r.Close > r.EMA200 and r.RSI < 42 and r.Close > r.Open:
                in_pos, mode = True, 'DIP'
            else:
                mode = 'CASH'

            if in_pos:
                alloc = 0.95 if mode == 'STRONG_BULL_TREND' else POSITION_ALLOCATION
                entry_px = r.Close * (1 + fee_slip)
                units = (cash * alloc) / entry_px
                highest_px = entry_px
                trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode})

        else:
            highest_px = max(highest_px, r.High)
            if mode == 'STRONG_BULL_TREND':
                exit_signal = (r.Low <= highest_px - 5.5 * r.ATR) or (r.Close < r.EMA200)
                exit_px = min(highest_px - 5.5 * r.ATR, r.Close)
            elif mode == 'TREND':
                exit_signal = (r.Low <= highest_px - 3.5 * r.ATR) or (r.Close < r.EMA50)
                exit_px = min(highest_px - 3.5 * r.ATR, r.Close)
            else: # DIP mode
                exit_signal = (r.High >= entry_px + 1.8 * r.ATR) or (r.Low <= entry_px - 2.0 * r.ATR)
                exit_px = entry_px + 1.8 * r.ATR if r.High >= entry_px + 1.8 * r.ATR else entry_px - 2.0 * r.ATR

            if exit_signal:
                in_pos = False
                final_exit_px = exit_px * (1 - fee_slip)
                pnl = units * (final_exit_px - entry_px)
                cash += pnl
                trades[-1].update({
                    'exit_date': df.index[i], 'exit': final_exit_px,
                    'pnl_usd': pnl, 'return_pct': (final_exit_px - entry_px) / entry_px * 100
                })
                units = 0

        equity_val.append(cash + (units * curr_price if in_pos else 0))
        equity_idx.append(df.index[i])

    return pd.DataFrame(trades), pd.Series(equity_val, index=equity_idx, name='Equity')

# ───────────────────────────────────────────────────────────
# 4. PERFORMANCE METRICS ENGINE
# ───────────────────────────────────────────────────────────
def calculate_metrics(equity_series, trades_df):
    if len(equity_series) < 2: return {}
    returns = equity_series.pct_change().dropna()
    periods_per_year = 2190 # 4h data
    
    total_return = (equity_series.iloc[-1] / equity_series.iloc[0]) - 1
    years = len(equity_series) / periods_per_year
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(periods_per_year) if returns.std() > 0 else 0
    
    rolling_max = equity_series.cummax()
    max_drawdown = ((equity_series - rolling_max) / rolling_max).min()
    
    if not trades_df.empty and 'return_pct' in trades_df.columns:
        wins = trades_df[trades_df['return_pct'] > 0]
        losses = trades_df[trades_df['return_pct'] <= 0]
        win_rate = len(wins) / len(trades_df)
        profit_factor = abs(wins['pnl_usd'].sum() / losses['pnl_usd'].sum()) if len(losses) > 0 and losses['pnl_usd'].sum() != 0 else np.inf
    else:
        win_rate = profit_factor = 0
        
    return {
        'Total Return (%)': round(total_return * 100, 2),
        'CAGR (%)': round(cagr * 100, 2),
        'Sharpe Ratio': round(sharpe, 2),
        'Max Drawdown (%)': round(max_drawdown * 100, 2),
        'Win Rate (%)': round(win_rate * 100, 2),
        'Profit Factor': round(profit_factor, 2),
        'Total Trades': len(trades_df)
    }

# ───────────────────────────────────────────────────────────
# 5. PORTFOLIO ENGINE (Safe Alignment)
# ───────────────────────────────────────────────────────────
def run_portfolio_50_25_25(btc_df, eth_df, sol_df, capital=INITIAL_CAPITAL):
    tr_b, eq_b = run_backtest_v9(btc_df, capital=capital * 0.50)
    tr_e, eq_e = run_backtest_v9(eth_df, capital=capital * 0.25)
    tr_s, eq_s = run_backtest_v9(sol_df, capital=capital * 0.25) if len(sol_df) > 50 else (pd.DataFrame(), pd.Series(capital * 0.25, index=btc_df.index, name='SOL'))
    
    # Safe outer merge to prevent forward-fill bias on assets with different start dates
    comb_eq = pd.concat([eq_b.rename('BTC'), eq_e.rename('ETH'), eq_s.rename('SOL')], axis=1).ffill().fillna(capital * 0.25)
    return {'BTC': (tr_b, eq_b), 'ETH': (tr_e, eq_e), 'SOL': (tr_s, eq_s)}, comb_eq.sum(axis=1)

# ═══════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 Initializing v12.1 Optimal Dynamic Regime Engine...\n")
    
    # Load or generate data
    df = add_indicators(load_real_data('BTC_USD_4h.csv'))
    trades, eq = run_backtest_v9(df)
    
    metrics = calculate_metrics(eq, trades)
    print("📊 [BTC SINGLE ASSET METRICS]")
    for k, v in metrics.items(): print(f"  {k}: {v}")
    print(f"  Final Capital: ${eq.iloc[-1]:,.2f}\n")

    # Portfolio Test (Will use synthetic data if CSVs are missing)
    eth_df = add_indicators(load_real_data('ETH_USD_4h.csv'))
    sol_df = add_indicators(load_real_data('SOL_USD_4h.csv'))
    
    _, port_eq = run_portfolio_50_25_25(df, eth_df, sol_df, capital=INITIAL_CAPITAL)
    print(f"💼 [PORTFOLIO 50/25/25] Final Capital: ${port_eq.iloc[-1]:,.2f}")
    
    # Optional: Plot Equity Curve
    plt.figure(figsize=(12, 6))
    plt.plot(port_eq.index, port_eq.values, label='Portfolio Equity', color='blue')
    plt.title('v12.1 Walk-Forward Ready Portfolio Equity Curve')
    plt.xlabel('Date')
    plt.ylabel('Capital (USD)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.show()