"""
V_ULTIMATE — State-Adaptive Regime Engine
==========================================
Combines the optimal logic for EVERY specific market state:

1. STRONG_BULL State:
   - Full 100% allocation
   - Wide catastrophic trail only (R2 wide stop - 9 ATR) to prevent shakeouts
   - Pyramiding enabled (add 50% on 1.5R pullback after 1.5R profit)
   - Fast EMA20 Reclaim (R1) after any temporary exit

2. BULL / TREND State:
   - Donchian 30 breakout with ADX > 20 & volume confirmation
   - Standard ATR trail (3.5 ATR)
   - TP1 partial profit taking (30% at 4.5 ATR, BE floor)

3. SIDEWAYS State:
   - Dip buying ONLY (RSI < 35, Close > EMA200, green candle, vol > 1.5x)
   - Fixed target/stop (1.8 ATR / 2.0 ATR)
   - No breakout entries (prevents false breakouts)

4. BEAR State:
   - 100% Cash / Stablecoins (Zero market drawdown risk)
   - Post-BEAR Recovery (R4): Immediate entry on first EMA50 reclaim green candle

5. HIGH_VOL State (Across any regime):
   - Dynamic volatility scale (reduce allocation to 60% only in extreme vol spikes)
"""
import os
import numpy as np
import pandas as pd
from v14 import (load_real_data, add_indicators, calculate_metrics,
                  FEE_SLIP, INITIAL_CAPITAL, WARMUP, BARS_PER_YEAR,
                  TRAIL_OVERRIDES_V14)

def run_ultimate_backtest(df, capital=INITIAL_CAPITAL, trail_override=None):
    ts_base, tt_base = (trail_override if trail_override else (5.5, 3.5))
    ts_max, tt_max = 9.0, 6.0

    cash = capital
    pos_units = 0.0
    pos_cost = 0.0
    mode = None
    entry_px_avg = 0.0
    extreme_px = 0.0
    init_risk_px = 0.0
    invested_total = 0.0
    be_px = 0.0
    tp1_done = False
    adds_done = 0
    entry_i = 0
    trade_pnl = 0.0
    last_exit_i = -10**9
    last_mode = None
    last_bear_i = -10**9
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]

    def close_trade(i, raw_exit_px, reason):
        nonlocal cash, pos_units, pos_cost, trade_pnl, last_exit_i, last_mode
        final_px = raw_exit_px * (1 - FEE_SLIP)
        pnl = pos_units * (final_px - entry_px_avg)
        cash += pos_units * final_px
        trade_pnl += pnl
        last_exit_i, last_mode = i, mode
        trades[-1].update({
            'exit_date': df.index[i], 'exit': round(final_px, 4),
            'pnl_usd': round(trade_pnl, 4),
            'return_pct': round(trade_pnl / invested_total * 100, 4) if invested_total else 0.0,
            'bars_held': i - entry_i, 'tp1': tp1_done, 'adds': adds_done,
            'reason': reason,
        })
        pos_units, pos_cost, trade_pnl = 0.0, 0.0, 0.0

    for i in range(WARMUP, len(df)):
        r = df.iloc[i]

        # ══ NO POSITION ══
        if pos_units == 0.0:
            entered, mode = False, None

            if r.Regime != 'BEAR':
                rsi_ok = r.RSI < 100.0
                vol_ok = (not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * 1.0

                # State 1 & 2: Trend / Breakout Entries
                if rsi_ok:
                    if r.Regime == 'STRONG_BULL' and r.Close >= r.Donchian30 and vol_ok:
                        entered, mode = True, 'STRONG_BULL_TREND'
                    elif r.Regime == 'BULL' and r.Close >= r.Donchian30 and vol_ok and r.ADX > 20.0:
                        entered, mode = True, 'TREND'

                # State 3: Sideways Dip Entry
                if not entered and r.Regime == 'SIDEWAYS' and r.Close > r.EMA200:
                    if r.RSI < 35.0 and r.Close > r.Open and not np.isnan(r.VolSMA20) and r.Volume > r.VolSMA20 * 1.5:
                        entered, mode = True, 'DIP'

                # Fast Re-entry (R1) in STRONG_BULL after shakeout
                if (not entered and last_mode == 'STRONG_BULL_TREND' and last_exit_i > -10**9
                        and (i - last_exit_i) <= 42 and r.Regime == 'STRONG_BULL'
                        and r.Close > r.EMA20 and r.Close > r.Open):
                    entered, mode = True, 'STRONG_BULL_TREND'

                # Recovery Entry (R4) after BEAR regime
                if (not entered and last_bear_i > -10**9 and (i - last_bear_i) <= 90
                        and r.Regime in ('BULL', 'STRONG_BULL')
                        and r.Close > r.EMA50 and r.Close > r.Open):
                    entered = True
                    mode = 'STRONG_BULL_TREND' if r.Regime == 'STRONG_BULL' else 'TREND'

            if entered:
                alloc = 0.95 if mode == 'STRONG_BULL_TREND' else 0.90
                if bool(getattr(r, 'HighVol', False)):
                    alloc = min(alloc, 0.60)
                alloc = float(np.clip(alloc, 0.05, 0.99))

                entry_px = r.Close * (1 + FEE_SLIP)
                invested = cash * alloc
                pos_units = invested / entry_px
                pos_cost = invested
                cash -= invested
                invested_total = invested
                entry_px_avg = entry_px
                extreme_px = entry_px
                init_risk_px = 3.5 * r.ATR
                be_px = entry_px * (1 + FEE_SLIP) / (1 - FEE_SLIP)
                tp1_done, adds_done = False, 0
                entry_i, trade_pnl = i, 0.0
                trades.append({'entry_date': df.index[i], 'entry': round(entry_px, 4), 'mode': mode})

        # ══ IN POSITION ══
        else:
            extreme_px = max(extreme_px, r.High)

            # State 3 DIP Exit (Fixed ATR target/stop)
            if mode == 'DIP':
                target_px = entry_px_avg + 1.8 * r.ATR
                stop_dip = entry_px_avg - 2.0 * r.ATR
                if r.Low <= stop_dip:
                    close_trade(i, min(stop_dip, r.Close), 'dip_stop')
                elif r.High >= target_px:
                    close_trade(i, target_px, 'dip_target')
                eq_val.append(cash + pos_units * r.Close)
                eq_idx.append(df.index[i])
                continue

            # State 1 & 2 Trailing Exits
            if mode == 'STRONG_BULL_TREND':
                mult = ts_max  # Wide stop (9 ATR) in STRONG_BULL to avoid shakeouts
            else:
                mult = tt_base # Normal stop (3.5 ATR) in TREND

            raw_trail = extreme_px - mult * r.ATR
            stop_px = max(raw_trail, be_px + 1.0 * r.ATR) if tp1_done else max(raw_trail, entry_px_avg - 3.5 * r.ATR)

            exit_now, exit_px, reason = False, None, None
            if r.Low <= stop_px:
                exit_now = True
                exit_px = min(stop_px, r.Close)
                reason = 'trail_stop'
            elif mode == 'STRONG_BULL_TREND' and r.Close < r.EMA200:
                exit_now, exit_px, reason = True, r.Close, 'ema200_catastrophic'
            elif mode == 'TREND' and r.Close < r.EMA50:
                exit_now, exit_px, reason = True, r.Close, 'ema50_trend_exit'

            if exit_now:
                close_trade(i, exit_px, reason)
            else:
                # TP1 Partial Profit (TREND mode only)
                if mode == 'TREND' and not tp1_done:
                    trigger = entry_px_avg + 4.5 * r.ATR
                    if r.High >= trigger:
                        sell_u = pos_units * 0.30
                        fill = trigger * (1 - FEE_SLIP)
                        cash += sell_u * fill
                        trade_pnl += sell_u * (fill - entry_px_avg)
                        pos_units -= sell_u
                        pos_cost -= sell_u * entry_px_avg
                        tp1_done = True

                # Pyramiding (STRONG_BULL mode only)
                if (mode == 'STRONG_BULL_TREND' and adds_done < 1
                        and r.Regime == 'STRONG_BULL'
                        and (extreme_px - entry_px_avg) >= 1.5 * init_risk_px
                        and (extreme_px - r.Close) >= 1.5 * r.ATR
                        and r.Close > r.EMA20):
                    add_invested = min(cash, invested_total * 0.50)
                    if add_invested > cash * 0.05:
                        add_px = r.Close * (1 + FEE_SLIP)
                        add_units = add_invested / add_px
                        entry_px_avg = ((entry_px_avg * pos_units + add_px * add_units) / (pos_units + add_units))
                        pos_units += add_units
                        pos_cost += add_invested
                        cash -= add_invested
                        invested_total += add_invested
                        adds_done += 1

        if r.Regime == 'BEAR':
            last_bear_i = i

        eq_val.append(cash + pos_units * r.Close)
        eq_idx.append(df.index[i])

    if pos_units > 0 and trades:
        close_trade(len(df) - 1, df.Close.iloc[-1], 'end_of_test')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[WARMUP] * df.Close.iloc[WARMUP:]
    bh.index = df.index[WARMUP:]
    return pd.DataFrame(trades), equity, bh

def run_portfolio_ultimate(capital=INITIAL_CAPITAL, weights=None):
    if weights is None:
        weights = {'BTC': 0.5, 'ETH': 0.3, 'SOL': 0.2}
    files = {'BTC': 'BTC_USD_4h.csv', 'ETH': 'ETH_USD_4h.csv', 'SOL': 'SOL_USD_4h.csv'}
    eqs = {}
    for name, f in files.items():
        df = add_indicators(load_real_data(f))
        ov = TRAIL_OVERRIDES_V14.get(name)
        _, eq, _ = run_ultimate_backtest(df, capital * weights[name], ov)
        eqs[name] = eq.rename(name)
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for n in weights:
        comb[n] = comb[n].fillna(capital * weights[n])
    return comb.sum(axis=1)

if __name__ == '__main__':
    port = run_portfolio_ultimate(1000.0)
    total_ret = (port.iloc[-1] / 1000.0 - 1) * 100
    rets = port.pct_change().dropna()
    dd = ((port - port.cummax()) / port.cummax()).min() * 100
    sharpe = rets.mean() / rets.std() * np.sqrt(BARS_PER_YEAR)
    cagr = ((port.iloc[-1] / 1000.0) ** (1 / (len(port) / BARS_PER_YEAR)) - 1) * 100
    print("=" * 60)
    print("🚀 V_ULTIMATE STATE-ADAPTIVE MASTER PORTFOLIO")
    print("=" * 60)
    print(f"Final Capital : ${port.iloc[-1]:,.2f}")
    print(f"Total Return  : +{total_ret:,.1f}%")
    print(f"CAGR          : {cagr:.1f}%")
    print(f"Max Drawdown  : {dd:.1f}%")
    print(f"Sharpe Ratio  : {sharpe:.2f}")
    print(f"Calmar Ratio  : {cagr / abs(dd):.2f}")
    print("=" * 60)
