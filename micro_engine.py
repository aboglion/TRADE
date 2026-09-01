"""
HIGH-CONVICTION MICRO-HYBRID ENGINE — FEE-RESISTANT FAST REGIME TRADING
========================================================================
Solves the fee drag & intraday noise problem by imposing strict conviction gates:
  1. Executes fast micro trades ONLY during confirmed STRONG_BULL macro regimes.
  2. Requires high volume expansion (Volume > 1.8x VolSMA20).
  3. High R-Multiple Target: 3.5 ATR profit target vs 1.8 ATR tight stop (2:1 reward:risk).
  4. Strict Fee-to-Edge Gate: Rejects trades unless expected move >= 6.0x total fee cost.
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

INITIAL_CAPITAL = 1000.0

FEE_PRESETS = {
    'TAKER_STANDARD': {'fee': 0.00075, 'slippage': 0.00025},  # 0.10% / side (0.20% RT)
    'MAKER_VIP':      {'fee': 0.00020, 'slippage': 0.00010},  # 0.03% / side (0.06% RT)
    'ZERO_FEE':       {'fee': 0.00000, 'slippage': 0.00000},  # 0.00% ideal baseline
}

DEFAULT_MICRO_CFG = dict(
    # ── Fast Micro Indicators ──────────────────────────────
    ema_fast=9,
    ema_med=21,
    ema_slow=50,
    ema_macro=200,
    rsi_period=9,
    rsi_surge_min=56.0,
    vol_surge_mult=1.6,
    donchian_micro_bars=24,    # ~4 days on 4h bars
    
    # ── Fee Drag & Edge Filter ─────────────────────────────
    fee_preset='TAKER_STANDARD',
    min_edge_to_fee_ratio=6.0, # Expected TP move must be >= 6.0x round-trip fee
    
    # ── Risk & Exit Parameters ────────────────────────────
    init_stop_atr=1.8,         # Stop loss distance
    trail_atr=3.2,             # Trailing stop distance
    tp1_atr=3.5,               # Take profit target 1
    tp1_fraction=0.50,         # Lock in 50% profit
    be_trigger_atr=1.5,        # Move stop to BE + fee floor after +1.5 ATR profit
    max_hold_bars=42,          # Max hold duration (~7 days)
    
    # ── Sizing ────────────────────────────────────────────
    base_alloc=0.85,
    strong_alloc=0.95,
)

def make_micro_cfg(**overrides):
    c = dict(DEFAULT_MICRO_CFG)
    c.update(overrides)
    return c

def load_data(filepath):
    if not os.path.exists(filepath):
        alt = os.path.join('data', os.path.basename(filepath))
        if os.path.exists(alt):
            filepath = alt
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    df = pd.read_csv(filepath)
    date_col = 'observation_date' if 'observation_date' in df.columns else 'Date'
    df['Date'] = pd.to_datetime(df[date_col])
    for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if c not in df.columns:
            raise ValueError(f"{filepath}: missing column '{c}'")
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['Date', 'Open', 'High', 'Low', 'Close'])
    df = df.set_index('Date').sort_index()
    if 'Volume' not in df or df['Volume'].isna().all():
        df['Volume'] = 1e6
    df['Volume'] = df['Volume'].fillna(1e6)
    return df

def add_micro_indicators(df, cfg=DEFAULT_MICRO_CFG):
    x = df.copy()
    
    x["EMA9"] = x.Close.ewm(span=cfg['ema_fast'], adjust=False).mean()
    x["EMA21"] = x.Close.ewm(span=cfg['ema_med'], adjust=False).mean()
    x["EMA50"] = x.Close.ewm(span=cfg['ema_slow'], adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=cfg['ema_macro'], adjust=False).mean()
    x["VolSMA20"] = x.Volume.rolling(20).mean()
    
    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    
    rsi_p = cfg['rsi_period']
    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/rsi_p, min_periods=rsi_p).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/rsi_p, min_periods=rsi_p).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))
    
    d_bars = cfg['donchian_micro_bars']
    x["DonchianMicroHigh"] = x.High.rolling(d_bars).max().shift(1)
    
    # 30-day return for macro momentum check
    x["Ret30D"] = (x.Close - x.Close.shift(180)) / x.Close.shift(180)
    
    regimes = []
    close_v = x.Close.values
    open_v = x.Open.values
    ema9_v, ema21_v, ema50_v, ema200_v = x.EMA9.values, x.EMA21.values, x.EMA50.values, x.EMA200.values
    rsi_v = x.RSI.values
    vol_v, volsma_v = x.Volume.values, x.VolSMA20.values
    donch_hi = x.DonchianMicroHigh.values
    ret30_v = x.Ret30D.fillna(0).values

    for i in range(len(x)):
        c = close_v[i]
        o = open_v[i]
        e9, e21, e50, e200 = ema9_v[i], ema21_v[i], ema50_v[i], ema200_v[i]
        rsi = rsi_v[i]
        vol = vol_v[i]
        volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
        ret30 = ret30_v[i]
        
        # Macro Bull Regime Gate: Close > EMA50 > EMA200 and positive 30D return
        macro_strong = (c > e50 > e200) and (ret30 > 0.05)
        
        # High-Conviction Micro Trigger
        if macro_strong and c >= donch_hi[i] and rsi >= cfg['rsi_surge_min'] and vol > volsma * cfg['vol_surge_mult']:
            regimes.append('HIGH_CONVICTION_MICRO')
        elif macro_strong and (c > e9 > e21) and (c > o) and rsi >= 52.0 and vol > volsma * 1.3:
            regimes.append('MICRO_TREND_ACCELERATION')
        else:
            regimes.append('MICRO_NEUTRAL')
            
    x["MicroRegime"] = regimes
    return x.dropna(subset=['ATR', 'RSI', 'EMA200'])

def run_micro_backtest(df, cfg=DEFAULT_MICRO_CFG, capital=INITIAL_CAPITAL):
    preset = FEE_PRESETS.get(cfg.get('fee_preset', 'TAKER_STANDARD'), FEE_PRESETS['TAKER_STANDARD'])
    fee_side = preset['fee']
    slip_side = preset['slippage']
    fee_slip_per_side = fee_side + slip_side
    rt_fee_factor = 2.0 * fee_slip_per_side
    
    cash = capital
    pos_units = 0.0
    pos_cost = 0.0
    entry_mode = None
    entry_px_avg = 0.0
    extreme_px = 0.0
    invested_total = 0.0
    stop_px = 0.0
    tp1_done = False
    entry_i = 0
    trade_pnl_gross = 0.0
    trade_pnl_net = 0.0
    trade_fees_paid = 0.0
    total_fees_paid_usd = 0.0
    
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]
    warmup_bars = 60

    def close_trade(i, raw_exit_px, reason):
        nonlocal cash, pos_units, pos_cost, trade_pnl_gross, trade_pnl_net, trade_fees_paid
        nonlocal total_fees_paid_usd
        
        exit_px_net = raw_exit_px * (1.0 - fee_slip_per_side)
        exit_fee = pos_units * raw_exit_px * fee_slip_per_side
        gross_pnl = pos_units * (raw_exit_px - entry_px_avg)
        net_pnl = pos_units * (exit_px_net - entry_px_avg)
        cash += pos_units * exit_px_net
            
        trade_fees_paid += exit_fee
        total_fees_paid_usd += trade_fees_paid
        trade_pnl_gross += gross_pnl
        trade_pnl_net += net_pnl
        
        ret_pct_net = (trade_pnl_net / invested_total * 100.0) if invested_total > 0 else 0.0
        
        trades[-1].update({
            'exit_date': df.index[i],
            'exit_px': round(raw_exit_px, 4),
            'gross_pnl': round(trade_pnl_gross, 4),
            'net_pnl': round(trade_pnl_net, 4),
            'fees_paid': round(trade_fees_paid, 4),
            'return_pct_net': round(ret_pct_net, 4),
            'bars_held': i - entry_i,
            'reason': reason,
        })
        pos_units, pos_cost = 0.0, 0.0
        trade_pnl_gross, trade_pnl_net, trade_fees_paid = 0.0, 0.0, 0.0

    for i in range(warmup_bars, len(df)):
        r = df.iloc[i]
        c_close = r.Close
        c_atr = r.ATR

        # ══ 1. NO POSITION: ENTRY LOGIC ══
        if pos_units == 0.0:
            entered = False
            entry_mode = None

            expected_move_ratio = (cfg['tp1_atr'] * c_atr) / c_close
            fee_edge_ok = (expected_move_ratio >= cfg['min_edge_to_fee_ratio'] * rt_fee_factor)

            if fee_edge_ok and r.MicroRegime in ('HIGH_CONVICTION_MICRO', 'MICRO_TREND_ACCELERATION'):
                entered = True
                entry_mode = r.MicroRegime

            if entered:
                alloc = cfg['strong_alloc'] if entry_mode == 'HIGH_CONVICTION_MICRO' else cfg['base_alloc']
                alloc = float(np.clip(alloc, 0.10, 0.98))

                invested = cash * alloc
                entry_px = c_close * (1.0 + fee_slip_per_side)
                entry_fee = invested * fee_slip_per_side
                pos_units = invested / entry_px
                pos_cost = invested
                cash -= invested

                invested_total = invested
                entry_px_avg = entry_px
                extreme_px = entry_px
                stop_px = entry_px - cfg['init_stop_atr'] * c_atr
                tp1_done = False
                entry_i = i
                trade_fees_paid = entry_fee
                trade_pnl_gross, trade_pnl_net = 0.0, 0.0

                trades.append({
                    'entry_date': df.index[i],
                    'entry_px': round(entry_px, 4),
                    'mode': entry_mode,
                    'alloc': round(alloc, 2),
                })

        # ══ 2. IN POSITION: MANAGING TRADES ══
        else:
            bars_held = i - entry_i
            extreme_px = max(extreme_px, r.High)
            open_profit_atr = (c_close - entry_px_avg) / max(c_atr, 1e-6)

            raw_trail = extreme_px - cfg['trail_atr'] * c_atr
            be_floor = entry_px_avg * (1.0 + rt_fee_factor * 1.5)
            
            if open_profit_atr >= cfg['be_trigger_atr']:
                stop_px = max(stop_px, be_floor, raw_trail)
            else:
                stop_px = max(stop_px, raw_trail)

            exit_now, exit_px, reason = False, None, None
            if r.Low <= stop_px:
                exit_now = True
                exit_px = min(stop_px, c_close)
                reason = 'be_floor_stop' if stop_px >= be_floor else 'atr_trail'
            elif r.Close < r.EMA50 and open_profit_atr < 0.2:
                exit_now, exit_px, reason = True, c_close, 'ema50_breakdown'
            elif bars_held >= cfg['max_hold_bars']:
                exit_now, exit_px, reason = True, c_close, 'time_stop'

            if exit_now:
                close_trade(i, exit_px, reason)
            else:
                # TP1 Partial Lock-in
                if not tp1_done and r.High >= (entry_px_avg + cfg['tp1_atr'] * c_atr):
                    sell_u = pos_units * cfg['tp1_fraction']
                    raw_tp1_px = entry_px_avg + cfg['tp1_atr'] * c_atr
                    tp1_px_net = raw_tp1_px * (1.0 - fee_slip_per_side)
                    tp1_fee = sell_u * raw_tp1_px * fee_slip_per_side
                    
                    cash += sell_u * tp1_px_net
                    gross_pnl = sell_u * (raw_tp1_px - entry_px_avg)
                    net_pnl = sell_u * (tp1_px_net - entry_px_avg)
                    
                    trade_pnl_gross += gross_pnl
                    trade_pnl_net += net_pnl
                    trade_fees_paid += tp1_fee
                    pos_units -= sell_u
                    pos_cost -= sell_u * entry_px_avg
                    tp1_done = True

        eq_val.append(cash + pos_units * c_close)
        eq_idx.append(df.index[i])

    if pos_units > 0 and trades:
        close_trade(len(df) - 1, df.Close.iloc[-1], 'end_of_test')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[warmup_bars] * df.Close.iloc[warmup_bars:]
    bh.index = df.index[warmup_bars:]
    
    trades_df = pd.DataFrame(trades)
    return trades_df, equity, bh, total_fees_paid_usd

def calculate_micro_metrics(equity, trades_df, total_fees_paid, bh=None, periods_per_year=2190):
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    total_ret = equity.iloc[-1] / equity.iloc[0] - 1
    years = len(equity) / periods_per_year
    cagr = (1 + total_ret) ** (1 / years) - 1 if years > 0 and total_ret > 0 else np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(periods_per_year) if rets.std() > 0 else 0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * np.sqrt(periods_per_year) if len(downside) > 1 and downside.std() > 0 else 0
    dd = ((equity - equity.cummax()) / equity.cummax()).min()

    t = trades_df.dropna(subset=['net_pnl']) if not trades_df.empty and 'net_pnl' in trades_df else pd.DataFrame()
    if len(t):
        wins = t[t.net_pnl > 0]
        losses = t[t.net_pnl <= 0]
        wr = len(wins) / len(t) * 100
        gp = wins.net_pnl.sum()
        gl = abs(losses.net_pnl.sum())
        pf = gp / gl if gl > 0 else np.inf
        expectancy_pct = t.return_pct_net.mean()
        avg_bars_held = t.bars_held.mean()
    else:
        wr = expectancy_pct = avg_bars_held = 0
        pf = np.nan

    fee_drag_pct = (total_fees_paid / equity.iloc[0]) * 100.0 if equity.iloc[0] > 0 else 0.0
    trades_per_year = len(t) / years if years > 0 else 0

    m = {
        'Final ($)': round(equity.iloc[-1], 2),
        'Return (%)': round(total_ret * 100, 2),
        'CAGR (%)': round(cagr * 100, 2) if not np.isnan(cagr) else np.nan,
        'Sharpe': round(sharpe, 2),
        'Sortino': round(sortino, 2),
        'MaxDD (%)': round(dd * 100, 2),
        'Trades': len(t),
        'Trades/Year': round(trades_per_year, 1),
        'WinRate (%)': round(wr, 1),
        'PF': round(pf, 2) if not np.isnan(pf) else np.nan,
        'Expectancy (%)': round(expectancy_pct, 2),
        'AvgHoldBars': round(avg_bars_held, 1),
        'TotalFeesPaid ($)': round(total_fees_paid, 2),
        'FeeDrag (% Capital)': round(fee_drag_pct, 2),
    }
    if bh is not None and len(bh) > 1:
        m['B&H (%)'] = round((bh.iloc[-1] / bh.iloc[0] - 1) * 100, 2)
        m['Alpha vs B&H'] = round(m['Return (%)'] - m['B&H (%)'], 2)
    return m
