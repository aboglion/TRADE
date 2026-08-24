import pandas as pd
import numpy as np
import BOT_TEST

df = pd.read_csv('CBBTCUSD_4h.csv')
df['Date'] = pd.to_datetime(df['observation_date'])
df = df.set_index('Date').sort_index()
df = BOT_TEST.add_indicators(df)

# Enhanced Backtest Engine with Early Breakeven + Trailing ATR
def run_high_wr_backtest(df, params, capital=10000.0):
    fee_slip = 0.0008
    hold_bars = params['hold_bars']
    cooldown = params.get('cooldown_bars', 2)
    
    trades = []
    equity_idx = [df.index[0]]
    equity_val = [capital]
    cash = capital
    
    i = 5
    last_loss_bar = -999
    
    while i < len(df) - hold_bars - 1:
        if i - last_loss_bar < cooldown:
            i += 1
            continue
            
        if BOT_TEST.check_signal_v9(df, i, params):
            entry_i = i + 1
            entry = df.Open.iloc[entry_i] * (1 + fee_slip)
            atr = df.ATR.iloc[i]
            
            stop = entry - params['atr_stop_mult'] * atr
            target = entry + params['atr_target_mult'] * atr
            
            risk_per_unit = entry - stop
            if risk_per_unit <= 0:
                i += 1
                continue
                
            units = min((cash * 0.01) / risk_per_unit, (cash * 0.95) / entry)
            if units <= 0:
                i += 1
                continue
                
            remaining_units = units
            total_pnl = 0
            highest_px = entry
            be_triggered = False
            
            for j in range(entry_i, min(entry_i + hold_bars + 1, len(df))):
                current_low = df.Low.iloc[j]
                current_high = df.High.iloc[j]
                highest_px = max(highest_px, current_high)
                
                # 1. Early Breakeven Activation (+0.8 ATR)
                if current_high >= entry + 0.8 * atr and not be_triggered:
                    stop = max(stop, entry + 0.1 * atr)
                    be_triggered = True
                    
                # 2. ATR Trailing Stop (highest_px - 1.8 * ATR)
                if be_triggered:
                    stop = max(stop, highest_px - 1.8 * atr)
                    
                # 3. Stop Loss
                if current_low <= stop:
                    exit_px = stop * (1 - fee_slip)
                    pnl = remaining_units * (exit_px - entry)
                    total_pnl += pnl
                    break
                    
                # 4. Target 1 (+40% of target) -> Exit 40%
                target_1 = entry + (target - entry) * 0.4
                if current_high >= target_1 and remaining_units > units * 0.6:
                    exit_px = target_1 * (1 - fee_slip)
                    portion = remaining_units * 0.4
                    pnl = portion * (exit_px - entry)
                    total_pnl += pnl
                    remaining_units -= portion
                    stop = max(stop, entry + 0.3 * atr)
                    
                # 5. Full Target
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
                'bars_held': min(j, len(df)-1) - entry_i,
            })
            
            if total_pnl < 0:
                last_loss_bar = min(j, len(df)-1)
                
            i = min(j, len(df)-1) + 1
        else:
            i += 1
            
    return pd.DataFrame(trades), pd.Series(equity_val)

BOT_TEST.run_backtest_v9 = run_high_wr_backtest

# Signal filter
def check_signal_wr_optimized(df, i, params):
    r = df.iloc[i]
    p = df.iloc[i-1]
    
    if r['Regime'] in ['CRASH', 'BEAR']:
        return False
        
    if not (r.Close > r.EMA50 > r.EMA200):
        return False
        
    if not (params.get('rsi_low', 48) < r.RSI < params.get('rsi_high', 68)):
        return False
        
    if not (r.Hist > 0 and r.Hist > p.Hist):
        return False
        
    return True

BOT_TEST.check_signal_v9 = check_signal_wr_optimized
BOT_TEST.TRAIN_DAYS = 2700
BOT_TEST.TEST_DAYS = 540
BOT_TEST.STEP_DAYS = 540
BOT_TEST.MIN_TRADES_PER_WF = 5

param_grid = {
    'atr_stop_mult':   [1.8, 2.2],
    'atr_target_mult': [2.2, 3.0],
    'hold_bars':       [18, 24, 30],
    'rsi_high':        [68, 72],
    'rsi_low':         [48, 52],
    'cooldown_bars':   3,
    'graduated':       True,
}

stats_df, trades_df, continuous_eq = BOT_TEST.walk_forward(df, param_grid)

print('\n' + '='*60)
print('HIGH WIN-RATE WALK-FORWARD RESULTS:')
print('TOTAL TRADES OOS:', len(trades_df))
print('AVG TRADES PER YEAR OOS:', f'{len(trades_df) / 5.4:.1f}')
print('TOTAL RETURN OOS:', f'{stats_df.net_return_pct.sum():.2f}%')
print('AVG WIN RATE OOS:', f'{stats_df.win_rate_pct.mean():.2f}%')
print('AVG PROFIT FACTOR OOS:', f'{stats_df.profit_factor.mean():.2f}')
print('='*60)
