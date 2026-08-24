import pandas as pd
import numpy as np
import BOT_TEST

# Load real data
btc_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('BTC_USD_4h.csv'))
eth_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('ETH_USD_4h.csv'))
sol_df = BOT_TEST.add_indicators(BOT_TEST.load_real_data('SOL_USD_4h.csv'))

# Slice last 1 year (2025-08-25 to current max date)
start_date = '2025-08-25 00:00:00'
btc_sub = btc_df[btc_df.index >= start_date].copy()
eth_sub = eth_df[eth_df.index >= start_date].copy()
sol_sub = sol_df[sol_df.index >= start_date].copy()

# Run BOT_TEST engine on sliced data
tr_b, eq_b = BOT_TEST.run_backtest_v9(btc_sub, capital=1000.0)
tr_e, eq_e = BOT_TEST.run_backtest_v9(eth_sub, capital=1000.0)
tr_s, eq_s = BOT_TEST.run_backtest_v9(sol_sub, capital=1000.0)

# Portfolio 50/25/25
res, port_eq = BOT_TEST.run_portfolio_50_25_25(btc_sub, eth_sub, sol_sub, capital=1000.0)

# Buy & Hold calculations
btc_bh = (btc_sub.Close.iloc[-1] - btc_sub.Close.iloc[0]) / btc_sub.Close.iloc[0] * 100
eth_bh = (eth_sub.Close.iloc[-1] - eth_sub.Close.iloc[0]) / eth_sub.Close.iloc[0] * 100
sol_bh = (sol_sub.Close.iloc[-1] - sol_sub.Close.iloc[0]) / sol_sub.Close.iloc[0] * 100

port_bh = 0.50 * btc_bh + 0.25 * eth_bh + 0.25 * sol_bh

# Drawdowns
btc_dd_bot = ((eq_b - eq_b.cummax()) / eq_b.cummax() * 100).min()
btc_bh_eq = (btc_sub.Close / btc_sub.Close.iloc[0]) * 1000.0
btc_dd_bh = ((btc_bh_eq - btc_bh_eq.cummax()) / btc_bh_eq.cummax() * 100).min()

port_dd_bot = ((port_eq - port_eq.cummax()) / port_eq.cummax() * 100).min()
port_bh_eq = (0.50 * (btc_sub.Close / btc_sub.Close.iloc[0]) + 
              0.25 * (eth_sub.Close / eth_sub.Close.iloc[0]) + 
              0.25 * (sol_sub.Close / sol_sub.Close.iloc[0])) * 1000.0
port_dd_bh = ((port_bh_eq - port_bh_eq.cummax()) / port_bh_eq.cummax() * 100).min()

print("==========================================================================")
print(f"  BOT_TEST ENGINE vs BUY & HOLD (LAST 1 YEAR: 2025-08-25 TO 2026-08-24)")
print("==========================================================================")

print("\n--- 1. SINGLE ASSET BTC ($1,000 STARTING CAPITAL) ---")
print(f"BOT Final Capital:       ${eq_b.iloc[-1]:,.2f} ({((eq_b.iloc[-1]-1000)/1000)*100:+.2f}%)")
print(f"Buy & Hold Final:        ${btc_bh_eq.iloc[-1]:,.2f} ({btc_bh:+.2f}%)")
print(f"Outperformance:          {((eq_b.iloc[-1]-1000)/1000)*100 - btc_bh:+.2f}%")
print(f"BOT Max Drawdown:        {btc_dd_bot:.2f}%  (vs B&H Max DD: {btc_dd_bh:.2f}%)")
print(f"Total BOT Trades:        {len(tr_b)}")

wins_b = tr_b[tr_b['pnl_usd'] > 0]
losses_b = tr_b[tr_b['pnl_usd'] <= 0]
if len(tr_b) > 0:
    wr_b = len(wins_b) / len(tr_b) * 100
    pf_b = wins_b['pnl_usd'].sum() / abs(losses_b['pnl_usd'].sum()) if len(losses_b)>0 and losses_b['pnl_usd'].sum()!=0 else np.nan
    print(f"Win Rate:                {wr_b:.1f}% ({len(wins_b)}W / {len(losses_b)}L)")
    print(f"Profit Factor:           {pf_b:.2f}")

print("\n--- 2. PORTFOLIO 50/25/25 (BTC 50% / ETH 25% / SOL 25%) ---")
print(f"BOT Portfolio Final:     ${port_eq.iloc[-1]:,.2f} ({((port_eq.iloc[-1]-1000)/1000)*100:+.2f}%)")
print(f"Buy & Hold Portfolio:    ${port_bh_eq.iloc[-1]:,.2f} ({port_bh:+.2f}%)")
print(f"Outperformance:          {((port_eq.iloc[-1]-1000)/1000)*100 - port_bh:+.2f}%")
print(f"BOT Max Drawdown:        {port_dd_bot:.2f}%  (vs B&H Max DD: {port_dd_bh:.2f}%)")

print("\n--- 3. PER ASSET BREAKDOWN (BOT vs BUY & HOLD) ---")
tr_b_sub, eq_b_sub = res['BTC']
tr_e_sub, eq_e_sub = res['ETH']
tr_s_sub, eq_s_sub = res['SOL']

print(f"BTC: BOT {((eq_b_sub.iloc[-1]-500)/500)*100:+.2f}% (${eq_b_sub.iloc[-1]:,.2f}) vs B&H {btc_bh:+.2f}% (${(btc_sub.Close.iloc[-1]/btc_sub.Close.iloc[0])*500:,.2f}) | Diff: {((eq_b_sub.iloc[-1]-500)/500)*100 - btc_bh:+.2f}%")
print(f"ETH: BOT {((eq_e_sub.iloc[-1]-250)/250)*100:+.2f}% (${eq_e_sub.iloc[-1]:,.2f}) vs B&H {eth_bh:+.2f}% (${(eth_sub.Close.iloc[-1]/eth_sub.Close.iloc[0])*250:,.2f}) | Diff: {((eq_e_sub.iloc[-1]-250)/250)*100 - eth_bh:+.2f}%")
print(f"SOL: BOT {((eq_s_sub.iloc[-1]-250)/250)*100:+.2f}% (${eq_s_sub.iloc[-1]:,.2f}) vs B&H {sol_bh:+.2f}% (${(sol_sub.Close.iloc[-1]/sol_sub.Close.iloc[0])*250:,.2f}) | Diff: {((eq_s_sub.iloc[-1]-250)/250)*100 - sol_bh:+.2f}%")

print("\n=== ALL BTC TRADES TAKEN BY BOT IN THE LAST 1 YEAR ===")
for idx, row in tr_b.iterrows():
    entry_d = str(row['entry_date'])[:16] if pd.notnull(row['entry_date']) else 'N/A'
    exit_d = str(row['exit_date'])[:16] if pd.notnull(row['exit_date']) else 'STILL OPEN'
    exit_p = f"${row['exit']:,.2f}" if pd.notnull(row['exit']) else 'N/A'
    ret_p = f"{row['return_pct']:+6.2f}%" if pd.notnull(row['return_pct']) else 'N/A'
    pnl_u = f"${row['pnl_usd']:+7.2f}" if pd.notnull(row['pnl_usd']) else 'N/A'
    print(f"Trade #{idx+1:02d} | Entry: {entry_d} @ ${row['entry']:,.2f} | Exit: {exit_d} @ {exit_p} | Mode: {row['mode']:<18} | Return: {ret_p} | PnL: {pnl_u}")
