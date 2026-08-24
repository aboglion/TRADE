import pandas as pd
import numpy as np
import BOT_TEST

# Load full data & add indicators
btc_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('BTC_USD_4h.csv'))
eth_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('ETH_USD_4h.csv'))
sol_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('SOL_USD_4h.csv'))

start_date = '2025-08-25 00:00:00'
end_date = btc_df.index[-1]

# Slicing data after calculating indicators
btc_sub = btc_df[btc_df.index >= start_date].copy()
eth_sub = eth_df[eth_df.index >= start_date].copy()
sol_sub = sol_df[sol_df.index >= start_date].copy()

# Custom backtest runner starting from index 0 (since indicators are pre-calculated)
def run_backtest_custom(df, capital=1000.0, alloc_pct=0.90):
    fee_slip = BOT_TEST.FEE_PER_SIDE + BOT_TEST.SLIPPAGE_PER_SIDE
    cash = capital
    in_pos = False
    entry_px = 0
    units = 0
    highest_px = 0
    trades = []
    equity_val = [capital]
    equity_idx = [df.index[0]]
    mode = 'TREND'
    
    for i in range(len(df)):
        r = df.iloc[i]
        curr_price = r.Close
        regime = r.RegimeV12
        
        if not in_pos:
            if regime == 'BEAR':
                pass
            elif regime == 'STRONG_BULL':
                if r.Close >= r.Donchian30:
                    in_pos = True
                    mode = 'STRONG_BULL_TREND'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * 0.95) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode, 'status': 'OPEN'})
            elif regime == 'BULL':
                if r.Close >= r.Donchian30:
                    in_pos = True
                    mode = 'TREND'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * alloc_pct) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode, 'status': 'OPEN'})
            elif regime == 'SIDEWAYS':
                if r.Close > r.EMA200 and r.RSI < 42 and r.Close > r.Open:
                    in_pos = True
                    mode = 'DIP'
                    entry_px = r.Close * (1 + fee_slip)
                    units = (cash * alloc_pct) / entry_px
                    highest_px = entry_px
                    trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode, 'status': 'OPEN'})
        else:
            highest_px = max(highest_px, r.High)
            
            if mode == 'STRONG_BULL_TREND':
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
                trades[-1]['status'] = 'CLOSED'
                units = 0
                
        current_val = cash + (units * curr_price if in_pos else 0)
        equity_val.append(current_val)
        equity_idx.append(df.index[i])
        
    trades_df = pd.DataFrame(trades)
    eq = pd.Series(equity_val, index=equity_idx, name='Equity')
    return trades_df, eq

def calc_stats(trades_df, eq, initial_cap, buy_hold_ret):
    peak = eq.cummax()
    dd = (eq - peak) / peak * 100
    max_dd = dd.min()
    
    closed_trades = trades_df[trades_df['status'] == 'CLOSED'] if not trades_df.empty and 'status' in trades_df.columns else trades_df
    open_trades = trades_df[trades_df['status'] == 'OPEN'] if not trades_df.empty and 'status' in trades_df.columns else pd.DataFrame()
    
    total_t = len(trades_df)
    n_closed = len(closed_trades)
    
    if n_closed > 0:
        wins = closed_trades[closed_trades['pnl_usd'] > 0]
        losses = closed_trades[closed_trades['pnl_usd'] <= 0]
        win_rate = len(wins) / n_closed * 100
        gross_profit = wins['pnl_usd'].sum()
        gross_loss = abs(losses['pnl_usd'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (np.inf if gross_profit > 0 else 0)
    else:
        win_rate, profit_factor = 0.0, 0.0
        
    final_cap = eq.iloc[-1]
    net_return = (final_cap - initial_cap) / initial_cap * 100
    
    return {
        'initial_capital': initial_cap,
        'final_capital': final_cap,
        'net_return': net_return,
        'buy_hold_ret': buy_hold_ret,
        'max_dd': max_dd,
        'total_trades': total_t,
        'closed_trades': n_closed,
        'open_trades': len(open_trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
    }

# Run Single Asset BTC
btc_trades, btc_eq = run_backtest_custom(btc_sub, capital=1000.0)
btc_bh = (btc_sub.Close.iloc[-1] - btc_sub.Close.iloc[0]) / btc_sub.Close.iloc[0] * 100
btc_stats = calc_stats(btc_trades, btc_eq, 1000.0, btc_bh)

# Run Portfolio (50/25/25)
tr_b, eq_b = run_backtest_custom(btc_sub, capital=500.0)
tr_e, eq_e = run_backtest_custom(eth_sub, capital=250.0)
tr_s, eq_s = run_backtest_custom(sol_sub, capital=250.0)

comb_eq = pd.DataFrame({'BTC': eq_b, 'ETH': eq_e, 'SOL': eq_s}).ffill().fillna(250.0)
port_eq = comb_eq.sum(axis=1)

eth_bh = (eth_sub.Close.iloc[-1] - eth_sub.Close.iloc[0]) / eth_sub.Close.iloc[0] * 100
sol_bh = (sol_sub.Close.iloc[-1] - sol_sub.Close.iloc[0]) / sol_sub.Close.iloc[0] * 100

b_stats = calc_stats(tr_b, eq_b, 500.0, btc_bh)
e_stats = calc_stats(tr_e, eq_e, 250.0, eth_bh)
s_stats = calc_stats(tr_s, eq_s, 250.0, sol_bh)

port_peak = port_eq.cummax()
port_dd = (port_eq - port_peak) / port_peak * 100
port_max_dd = port_dd.min()
port_net_ret = (port_eq.iloc[-1] - 1000.0) / 1000.0 * 100

print("==========================================================================")
print(f"  RESULTS: BACKTEST 1 YEAR (2025-08-25 TO 2026-08-24)")
print("==========================================================================")

print("\n--- BTC SINGLE ASSET ($1,000 Starting Capital) ---")
print(f"Initial Capital:     ${btc_stats['initial_capital']:,.2f}")
print(f"Final Capital:       ${btc_stats['final_capital']:,.2f}")
print(f"Net Return:          {btc_stats['net_return']:+.2f}%")
print(f"Buy & Hold Return:   {btc_stats['buy_hold_ret']:+.2f}% (${btc_sub.Close.iloc[0]:,.2f} -> ${btc_sub.Close.iloc[-1]:,.2f})")
print(f"Max Drawdown:        {btc_stats['max_dd']:.2f}%")
print(f"Total Trades:        {btc_stats['total_trades']} ({btc_stats['closed_trades']} Closed, {btc_stats['open_trades']} Open)")
print(f"Win Rate (Closed):   {btc_stats['win_rate']:.1f}%")
print(f"Profit Factor:       {btc_stats['profit_factor']:.2f}")

print("\nAll BTC Trades:")
for idx, r in btc_trades.iterrows():
    if r['status'] == 'CLOSED':
        print(f"  [{r['status']}] Entry: {str(r['entry_date'])[:16]} @ ${r['entry']:,.2f} | Exit: {str(r['exit_date'])[:16]} @ ${r['exit']:,.2f} | Mode: {r['mode']:<18} | Return: {r['return_pct']:+6.2f}% | PnL: ${r['pnl_usd']:+7.2f}")
    else:
        print(f"  [{r['status']}]   Entry: {str(r['entry_date'])[:16]} @ ${r['entry']:,.2f} | Currently Open                             | Mode: {r['mode']:<18}")

print("\n--- PORTFOLIO 50/25/25 (BTC 50% / ETH 25% / SOL 25%) ---")
print(f"Initial Capital:     $1,000.00")
print(f"Final Capital:       ${port_eq.iloc[-1]:,.2f}")
print(f"Net Return:          {port_net_ret:+.2f}%")
print(f"Max Drawdown:        {port_max_dd:.2f}%")

print("\nAsset Breakdown:")
print(f"  BTC (50% / $500): Start $500 -> End ${eq_b.iloc[-1]:,.2f} ({b_stats['net_return']:+.2f}%) | Trades: {b_stats['total_trades']} (WR: {b_stats['win_rate']:.1f}%) | B&H: {btc_bh:+.2f}%")
print(f"  ETH (25% / $250): Start $250 -> End ${eq_e.iloc[-1]:,.2f} ({e_stats['net_return']:+.2f}%) | Trades: {e_stats['total_trades']} (WR: {e_stats['win_rate']:.1f}%) | B&H: {eth_bh:+.2f}%")
print(f"  SOL (25% / $250): Start $250 -> End ${eq_s.iloc[-1]:,.2f} ({s_stats['net_return']:+.2f}%) | Trades: {s_stats['total_trades']} (WR: {s_stats['win_rate']:.1f}%) | B&H: {sol_bh:+.2f}%")
