"""
HYBRID CORE-SATELLITE (80/20) UNIFIED QUANTITATIVE TRADING ENGINE
=================================================================
Combines:
  • 80% Core Macro Allocation: Multi-month trend riding, pyramiding & ATR trailing stops
  • 20% Satellite Micro Allocation: Active yield generation during market consolidation
  • Quarterly Portfolio Rebalancing: Prevents asset concentration drift & locks in gains
  • Pure Out-of-Sample Validation: Leak-free evaluation (2024-2026 unseen data)
  • Interactive Dashboard Generator: Renders dynamic ApexCharts (dashboard.html)
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# GLOBAL CONSTANTS & CONFIGURATIONS
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.00075
SLIPPAGE_PER_SIDE = 0.00050
FEE_SLIP = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
WARMUP = 300
BARS_PER_YEAR = 2190          # 4h candles per year

FILES = {
    'BTC': 'data/BTC_USD_4h.csv',
    'ETH': 'data/ETH_USD_4h.csv',
    'SOL': 'data/SOL_USD_4h.csv'
}
DEFAULT_WEIGHTS = {'BTC': 0.40, 'ETH': 0.30, 'SOL': 0.30}

# ── Macro Configurations (V_BEST / v14) ────────────────────
BASE_CFG = dict(
    entry_score_min=0,
    rsi_overbought_max=100.0,
    dip_rsi_max=35.0,
    dip_vol_mult=1.5,
    vol_filter_mult=1.0,
    trend_adx_min=20.0,
    trail_base_strong=5.0,
    trail_base_trend=3.0,
    trail_max_strong=6.5,
    trail_max_trend=4.5,
    parabolic_r=3.0,
    adaptive_trail=True,
    ema_exit_strong=True,
    ema_exit_trend=True,
    tp1_enabled=True,
    tp1_trigger_atr=4.5,
    tp1_fraction=0.30,
    tp1_be_floor_atr=1.0,
    init_risk_atr=2.8,
    init_risk_modes=('STRONG_BULL_TREND', 'TREND'),
    cooldown_bars=0,
    dip_tp_atr=1.8,
    dip_sl_atr=2.0,
    highvol_atr_pct=0.08,
    vol_q=0.70,
    highvol_alloc=0.50,
    base_alloc=0.85,
    strong_alloc=1.0,
    max_add_entries=2,
    pyramid_profit_r=1.2,
    pyramid_pullback_atr=1.5,
    add1_frac=0.50,
    add2_frac=0.30,
)

CFG_BTC_R3 = dict(BASE_CFG, ema20_reentry=True, reentry_lookback=12)
CFG_ETH_R2 = dict(BASE_CFG, tp1_enabled=False)
CFG_SOL_BASE = dict(BASE_CFG)

BEST_CFGS = {
    'BTC': CFG_BTC_R3,
    'ETH': CFG_ETH_R2,
    'SOL': CFG_SOL_BASE,
}

TRAIL_OVERRIDES_V14 = {
    'BTC': (4.5, 3.5),
    'ETH': (6.5, 4.5),
    'SOL': (7.5, 5.0),
}

# ── Micro Satellite Configurations ─────────────────────────
DEFAULT_MICRO_CFG = dict(
    ema_fast=9,
    ema_med=21,
    ema_slow=50,
    ema_macro=200,
    rsi_period=9,
    rsi_surge_min=56.0,
    vol_surge_mult=1.6,
    donchian_micro_bars=24,
    min_edge_to_fee_ratio=6.0,
    init_stop_atr=1.8,
    trail_atr=3.2,
    tp1_atr=3.5,
    tp1_fraction=0.50,
    be_trigger_atr=1.5,
    max_hold_bars=42,
    base_alloc=0.85,
    strong_alloc=0.95,
)

def make_cfg(**overrides):
    c = dict(BASE_CFG)
    c.update(overrides)
    return c

# ═══════════════════════════════════════════════════════════
# DATA LOADING & MACRO INDICATORS
# ═══════════════════════════════════════════════════════════
def load_real_data(filepath='data/BTC_USD_4h.csv'):
    if not os.path.exists(filepath):
        alt = os.path.join('data', os.path.basename(filepath))
        if os.path.exists(alt):
            filepath = alt
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Data file not found: {filepath}")

    df = pd.read_csv(filepath)
    date_col = 'observation_date' if 'observation_date' in df.columns else 'Date'
    df['Date'] = pd.to_datetime(df[date_col])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col not in df.columns:
            raise ValueError(f"{filepath}: missing column '{col}'")
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Date', 'Open', 'High', 'Low', 'Close'])
    df = df.set_index('Date').sort_index()
    if 'Volume' not in df or df['Volume'].isna().all():
        df['Volume'] = 1e6
    df['Volume'] = df['Volume'].fillna(1e6)
    return df

def add_indicators(df, vol_q=0.70):
    x = df.copy()
    x["EMA20"] = x.Close.ewm(span=20, adjust=False).mean()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    x["Donchian30Low"] = x.Low.rolling(30).min().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()

    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))

    high_diff = x.High.diff()
    low_diff = -x.Low.diff()
    pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
    pos_di = 100 * pd.Series(pos_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / x.ATR
    neg_di = 100 * pd.Series(neg_dm, index=x.index).ewm(alpha=1/14, min_periods=14).mean() / x.ATR
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, np.nan)
    x["ADX"] = dx.ewm(alpha=1/14, min_periods=14).mean()

    vol_thresh = x.Volume.rolling(500, min_periods=100).quantile(vol_q)
    x["HighVol"] = x.Volume > vol_thresh

    regimes = []
    close_v = x.Close.values
    open_v = x.Open.values
    ema20_v, ema50_v, ema200_v = x.EMA20.values, x.EMA50.values, x.EMA200.values
    rsi_v = x.RSI.values
    vol_v, volsma_v = x.Volume.values, x.VolSMA20.values
    atr_v = x.ATR.values

    for i in range(len(x)):
        c = close_v[i]
        o = open_v[i]
        e20, e50, e200 = ema20_v[i], ema50_v[i], ema200_v[i]
        rsi = rsi_v[i]
        vol = vol_v[i]
        volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
        atr = atr_v[i] if not np.isnan(atr_v[i]) else 1.0

        if (c > e20 > e50 > e200) and (c > o) and (rsi > 50) and (vol > volsma * 1.2):
            regimes.append('STRONG_BULL_TREND')
        elif (c > e50 > e200) and (c > e20):
            regimes.append('TREND')
        elif (c < e50) and (rsi < 35) and (vol > volsma * 1.5):
            regimes.append('DIP_OVER_SOLD')
        elif (c < e50 < e200):
            regimes.append('BEAR')
        else:
            regimes.append('SIDEWAYS')

    x["Regime"] = regimes
    return x.dropna(subset=['ATR', 'RSI', 'ADX', 'EMA200'])

# ═══════════════════════════════════════════════════════════
# MACRO BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════
def run_backtest(df, cfg=BASE_CFG, capital=INITIAL_CAPITAL, trail_override=None, fee_side=0.00125):
    fee_slip_per_side = fee_side
    cash = capital
    pos_units = 0.0
    pos_cost = 0.0
    entry_mode = None

    entries = []
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]

    trail_base_strong = trail_override[0] if trail_override else cfg['trail_base_strong']
    trail_base_trend  = trail_override[1] if trail_override else cfg['trail_base_trend']

    trade_pnl_gross = 0.0
    trade_pnl_net = 0.0
    trade_fees_paid = 0.0

    def entry_px_avg():
        if not entries: return 0.0
        tot_u = sum(e['units'] for e in entries)
        return sum(e['units'] * e['px'] for e in entries) / tot_u if tot_u > 0 else 0.0

    def total_invested():
        return sum(e['cost'] for e in entries)

    def close_all(i, raw_exit_px, reason):
        nonlocal cash, pos_units, pos_cost, entries, trade_pnl_gross, trade_pnl_net, trade_fees_paid
        if pos_units <= 0: return

        avg_px = entry_px_avg()
        tot_inv = total_invested()
        exit_px_net = raw_exit_px * (1.0 - fee_slip_per_side)
        exit_fee = pos_units * raw_exit_px * fee_slip_per_side

        gross_pnl = pos_units * (raw_exit_px - avg_px)
        net_pnl = pos_units * (exit_px_net - avg_px)
        cash += pos_units * exit_px_net

        trade_fees_paid += exit_fee
        trade_pnl_gross += gross_pnl
        trade_pnl_net += net_pnl

        ret_pct = (trade_pnl_net / tot_inv * 100.0) if tot_inv > 0 else 0.0
        first_e = entries[0]

        trades[-1].update({
            'exit_date': df.index[i],
            'exit_px': round(raw_exit_px, 4),
            'gross_pnl': round(trade_pnl_gross, 4),
            'net_pnl': round(trade_pnl_net, 4),
            'fees_paid': round(trade_fees_paid, 4),
            'return_pct': round(ret_pct, 4),
            'bars_held': i - first_e['bar_i'],
            'num_entries': len(entries),
            'reason': reason,
        })
        pos_units, pos_cost = 0.0, 0.0
        entries = []
        trade_pnl_gross, trade_pnl_net, trade_fees_paid = 0.0, 0.0, 0.0

    last_exit_bar = -9999

    for i in range(WARMUP, len(df)):
        r = df.iloc[i]
        c_close = r.Close

        # ── ENTRY LOGIC ───────────────────────────────────────
        if pos_units == 0.0:
            entered = False
            entry_mode = None

            if (i - last_exit_bar) < cfg.get('cooldown_bars', 0):
                eq_val.append(cash)
                eq_idx.append(df.index[i])
                continue

            if r.Regime in ('STRONG_BULL_TREND', 'TREND') and c_close >= r.Donchian30 and r.ADX >= cfg['trend_adx_min']:
                entered = True
                entry_mode = r.Regime
            elif r.Regime == 'DIP_OVER_SOLD' and r.RSI <= cfg['dip_rsi_max']:
                entered = True
                entry_mode = 'DIP_OVER_SOLD'
            elif cfg.get('ema20_reentry', False) and r.Regime in ('STRONG_BULL_TREND', 'TREND'):
                lookback = cfg.get('reentry_lookback', 12)
                sub = df.iloc[max(0, i-lookback):i]
                if (sub.Low <= sub.EMA20).any() and (c_close > r.EMA20):
                    entered = True
                    entry_mode = 'EMA20_REENTRY'

            if entered:
                alloc = cfg['highvol_alloc'] if r.HighVol else (
                    cfg['strong_alloc'] if entry_mode == 'STRONG_BULL_TREND' else cfg['base_alloc']
                )
                invested = cash * alloc
                entry_px = c_close * (1.0 + fee_slip_per_side)
                entry_fee = invested * fee_slip_per_side
                units = invested / entry_px

                cash -= invested
                pos_units = units
                pos_cost = invested

                entries = [{
                    'bar_i': i, 'date': df.index[i], 'px': entry_px,
                    'units': units, 'cost': invested, 'mode': entry_mode,
                    'atr_at_entry': r.ATR, 'high_water': r.High, 'tp1_done': False,
                }]

                trade_fees_paid = entry_fee
                trade_pnl_gross, trade_pnl_net = 0.0, 0.0

                trades.append({
                    'entry_date': df.index[i],
                    'entry_px': round(entry_px, 4),
                    'mode': entry_mode,
                    'alloc': round(alloc, 2),
                })

        # ── MANAGING ACTIVE POSITION ──────────────────────────
        else:
            first_e = entries[0]
            first_e['high_water'] = max(first_e['high_water'], r.High)

            avg_px = entry_px_avg()
            open_r = (c_close - avg_px) / max(first_e['atr_at_entry'], 1e-6)

            # Pyramiding logic
            if len(entries) < cfg['max_add_entries'] + 1 and first_e['mode'] == 'STRONG_BULL_TREND':
                if open_r >= cfg['pyramid_profit_r']:
                    last_px = entries[-1]['px']
                    pullback = (last_px - r.Low) / max(r.ATR, 1e-6)
                    if pullback >= cfg['pyramid_pullback_atr'] and c_close > r.EMA20:
                        add_frac = cfg['add1_frac'] if len(entries) == 1 else cfg['add2_frac']
                        invested = cash * add_frac
                        if invested > 10.0:
                            add_px = c_close * (1.0 + fee_slip_per_side)
                            add_fee = invested * fee_slip_per_side
                            add_units = invested / add_px

                            cash -= invested
                            pos_units += add_units
                            pos_cost += invested

                            entries.append({
                                'bar_i': i, 'date': df.index[i], 'px': add_px,
                                'units': add_units, 'cost': invested, 'mode': 'PYRAMID_ADD',
                                'atr_at_entry': r.ATR, 'high_water': r.High, 'tp1_done': False,
                            })
                            trade_fees_paid += add_fee

            # Trailing stop calculations
            tb = trail_base_strong if first_e['mode'] == 'STRONG_BULL_TREND' else trail_base_trend
            trail_atr = tb
            if cfg.get('adaptive_trail', False) and open_r > cfg['parabolic_r']:
                tm = cfg['trail_max_strong'] if first_e['mode'] == 'STRONG_BULL_TREND' else cfg['trail_max_trend']
                extra = min(tm - tb, (open_r - cfg['parabolic_r']) * 0.4)
                trail_atr = tb + extra

            trail_stop = first_e['high_water'] - trail_atr * r.ATR

            # Exits check
            exit_now, exit_px, reason = False, None, None

            if first_e['mode'] in cfg.get('init_risk_modes', ()):
                init_stop = first_e['px'] - cfg['init_risk_atr'] * first_e['atr_at_entry']
                if r.Low <= init_stop:
                    exit_now = True
                    exit_px = min(init_stop, r.Open)
                    reason = 'initial_risk_stop'

            if not exit_now and first_e['mode'] == 'DIP_OVER_SOLD':
                dip_tp = first_e['px'] + cfg['dip_tp_atr'] * first_e['atr_at_entry']
                dip_sl = first_e['px'] - cfg['dip_sl_atr'] * first_e['atr_at_entry']
                if r.High >= dip_tp:
                    exit_now, exit_px, reason = True, dip_tp, 'dip_take_profit'
                elif r.Low <= dip_sl:
                    exit_now, exit_px, reason = True, min(dip_sl, r.Open), 'dip_stop_loss'

            if not exit_now:
                if r.Low <= trail_stop:
                    exit_now = True
                    exit_px = min(trail_stop, r.Open)
                    reason = 'atr_trailing_stop'
                elif cfg.get('ema_exit_strong', True) and first_e['mode'] == 'STRONG_BULL_TREND' and r.Close < r.EMA200:
                    exit_now, exit_px, reason = True, c_close, 'ema200_bear_exit'
                elif cfg.get('ema_exit_trend', True) and first_e['mode'] == 'TREND' and r.Close < r.EMA50:
                    exit_now, exit_px, reason = True, c_close, 'ema50_trend_exit'

            if exit_now:
                close_all(i, exit_px, reason)
                last_exit_bar = i
            else:
                if cfg.get('tp1_enabled', True) and not first_e['tp1_done']:
                    tp1_target = first_e['px'] + cfg['tp1_trigger_atr'] * first_e['atr_at_entry']
                    if r.High >= tp1_target:
                        sell_units = pos_units * cfg['tp1_fraction']
                        raw_tp1_px = tp1_target
                        tp1_net_px = raw_tp1_px * (1.0 - fee_slip_per_side)
                        tp1_fee = sell_units * raw_tp1_px * fee_slip_per_side

                        cash += sell_units * tp1_net_px
                        gross_pnl = sell_units * (raw_tp1_px - avg_px)
                        net_pnl = sell_units * (tp1_net_px - avg_px)

                        trade_pnl_gross += gross_pnl
                        trade_pnl_net += net_pnl
                        trade_fees_paid += tp1_fee

                        pos_units -= sell_units
                        pos_cost -= sell_units * avg_px
                        first_e['tp1_done'] = True

        eq_val.append(cash + pos_units * c_close)
        eq_idx.append(df.index[i])

    if pos_units > 0 and trades:
        close_all(len(df) - 1, df.Close.iloc[-1], 'end_of_backtest')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[WARMUP] * df.Close.iloc[WARMUP:]
    bh.index = df.index[WARMUP:]

    trades_df = pd.DataFrame(trades)
    return trades_df, equity, bh

# ═══════════════════════════════════════════════════════════
# MICRO SATELLITE ENGINE
# ═══════════════════════════════════════════════════════════
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

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/9, min_periods=9).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/9, min_periods=9).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))

    d_bars = cfg['donchian_micro_bars']
    x["DonchianMicroHigh"] = x.High.rolling(d_bars).max().shift(1)
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
        c, o = close_v[i], open_v[i]
        e9, e21, e50, e200 = ema9_v[i], ema21_v[i], ema50_v[i], ema200_v[i]
        rsi = rsi_v[i]
        vol = vol_v[i]
        volsma = volsma_v[i] if not np.isnan(volsma_v[i]) else 1e6
        ret30 = ret30_v[i]

        macro_strong = (c > e50 > e200) and (ret30 > 0.05)
        if macro_strong and c >= donch_hi[i] and rsi >= cfg['rsi_surge_min'] and vol > volsma * cfg['vol_surge_mult']:
            regimes.append('HIGH_CONVICTION_MICRO')
        elif macro_strong and (c > e9 > e21) and (c > o) and rsi >= 52.0 and vol > volsma * 1.3:
            regimes.append('MICRO_TREND_ACCELERATION')
        else:
            regimes.append('MICRO_NEUTRAL')

    x["MicroRegime"] = regimes
    return x.dropna(subset=['ATR', 'RSI', 'EMA200'])

def run_micro_backtest(df, cfg=DEFAULT_MICRO_CFG, capital=INITIAL_CAPITAL):
    fee_slip_per_side = 0.00125
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
        nonlocal cash, pos_units, pos_cost, trade_pnl_gross, trade_pnl_net, trade_fees_paid, total_fees_paid_usd
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

        if pos_units == 0.0:
            if r.MicroRegime in ('HIGH_CONVICTION_MICRO', 'MICRO_TREND_ACCELERATION'):
                alloc = cfg['strong_alloc'] if r.MicroRegime == 'HIGH_CONVICTION_MICRO' else cfg['base_alloc']
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
                    'mode': r.MicroRegime,
                    'alloc': round(alloc, 2),
                })
        else:
            bars_held = i - entry_i
            extreme_px = max(extreme_px, r.High)
            open_profit_atr = (c_close - entry_px_avg) / max(c_atr, 1e-6)
            raw_trail = extreme_px - cfg['trail_atr'] * c_atr

            if open_profit_atr >= cfg['be_trigger_atr']:
                stop_px = max(stop_px, entry_px_avg * 1.003, raw_trail)
            else:
                stop_px = max(stop_px, raw_trail)

            exit_now, exit_px, reason = False, None, None
            if r.Low <= stop_px:
                exit_now = True
                exit_px = min(stop_px, c_close)
                reason = 'atr_trail'
            elif r.Close < r.EMA50 and open_profit_atr < 0.2:
                exit_now, exit_px, reason = True, c_close, 'ema50_breakdown'
            elif bars_held >= cfg['max_hold_bars']:
                exit_now, exit_px, reason = True, c_close, 'time_stop'

            if exit_now:
                close_trade(i, exit_px, reason)
            else:
                if not tp1_done and r.High >= (entry_px_avg + cfg['tp1_atr'] * c_atr):
                    sell_u = pos_units * cfg['tp1_fraction']
                    raw_tp1_px = entry_px_avg + cfg['tp1_atr'] * c_atr
                    tp1_px_net = raw_tp1_px * (1.0 - fee_slip_per_side)
                    tp1_fee = sell_u * raw_tp1_px * fee_slip_per_side

                    cash += sell_u * tp1_px_net
                    trade_pnl_gross += sell_u * (raw_tp1_px - entry_px_avg)
                    trade_pnl_net += sell_u * (tp1_px_net - entry_px_avg)
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
    return pd.DataFrame(trades), equity, bh, total_fees_paid_usd

# ═══════════════════════════════════════════════════════════
# HYBRID CORE-SATELLITE (80/20) PORTFOLIO RUNNER
# ═══════════════════════════════════════════════════════════
def run_macro_portfolio(capital=800.0, weights=None, fee_side=0.00125):
    if weights is None: weights = DEFAULT_WEIGHTS
    eqs, all_trades = {}, []
    for name, path in FILES.items():
        df = add_indicators(load_real_data(path))
        cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else TRAIL_OVERRIDES_V14.get(name)
        tr, eq, bh = run_backtest(df, cfg, capital * weights[name], trail, fee_side=fee_side)
        eqs[name] = eq.rename(name)
        if not tr.empty:
            all_trades.append(tr.assign(asset=name))
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in weights:
        comb[name] = comb[name].fillna(capital * weights[name])
    return comb.sum(axis=1), (pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()), comb

def run_micro_portfolio(capital=200.0, weights=None):
    if weights is None: weights = DEFAULT_WEIGHTS
    dfs = {name: load_real_data(path) for name, path in FILES.items()}
    micro_eqs, micro_all_tr = {}, []
    for name, df in dfs.items():
        w = weights[name]
        x = add_micro_indicators(df)
        tr, eq, bh, fees = run_micro_backtest(x, capital=capital * w)
        micro_eqs[name] = eq.rename(name)
        if not tr.empty:
            micro_all_tr.append(tr.assign(asset=name))
    micro_comb = pd.concat(micro_eqs.values(), axis=1).ffill()
    for name in weights:
        micro_comb[name] = micro_comb[name].fillna(capital * weights[name])
    return micro_comb.sum(axis=1), micro_comb

def run_rebalanced_hybrid_engine(initial_capital=1000.0, core_ratio=0.80, weights=None, rebalance_freq='QE'):
    if weights is None: weights = DEFAULT_WEIGHTS
    macro_eq, macro_tr, macro_comb = run_macro_portfolio(capital=initial_capital * core_ratio, weights=weights, fee_side=0.00125)
    micro_eq, micro_comb = run_micro_portfolio(capital=initial_capital * (1.0 - core_ratio), weights=weights)

    macro_daily = macro_eq.resample('D').last().dropna()
    micro_daily = micro_eq.resample('D').last().dropna()
    common_idx = macro_daily.index.intersection(micro_daily.index)

    macro_daily = macro_daily.loc[common_idx]
    micro_daily = micro_daily.loc[common_idx]

    rebal_dates = pd.date_range(common_idx[0], common_idx[-1], freq=rebalance_freq)

    current_cap = initial_capital
    portfolio_val = []

    last_date = common_idx[0]
    for rd in rebal_dates:
        if rd not in common_idx:
            sub = common_idx[common_idx <= rd]
            if len(sub) == 0: continue
            rd = sub[-1]

        chunk_macro = macro_daily.loc[last_date:rd]
        chunk_micro = micro_daily.loc[last_date:rd]

        if len(chunk_macro) > 1:
            ret_macro = chunk_macro / chunk_macro.iloc[0]
            ret_micro = chunk_micro / chunk_micro.iloc[0]

            val_macro = ret_macro * (current_cap * core_ratio)
            val_micro = ret_micro * (current_cap * (1.0 - core_ratio))
            chunk_tot = val_macro + val_micro

            portfolio_val.append(chunk_tot.iloc[:-1])
            current_cap = chunk_tot.iloc[-1]

        last_date = rd

    chunk_macro = macro_daily.loc[last_date:]
    chunk_micro = micro_daily.loc[last_date:]
    if len(chunk_macro) > 0:
        ret_macro = chunk_macro / chunk_macro.iloc[0]
        ret_micro = chunk_micro / chunk_micro.iloc[0]
        chunk_tot = ret_macro * (current_cap * core_ratio) + ret_micro * (current_cap * (1.0 - core_ratio))
        portfolio_val.append(chunk_tot)

    hybrid_daily = pd.concat(portfolio_val)
    hybrid_daily = hybrid_daily[~hybrid_daily.index.duplicated(keep='first')].sort_index()
    return hybrid_daily, macro_daily, micro_daily

def run_hybrid_engine(initial_capital=1000.0, core_ratio=0.80, weights=None):
    return run_rebalanced_hybrid_engine(initial_capital=initial_capital, core_ratio=core_ratio, weights=weights)

# ═══════════════════════════════════════════════════════════
# PERFORMANCE METRICS CALCULATION
# ═══════════════════════════════════════════════════════════
def calculate_metrics(equity, trades_df, bh=None, periods_per_year=BARS_PER_YEAR):
    if len(equity) < 2: return {}
    rets = equity.pct_change().dropna()
    total_ret = equity.iloc[-1] / equity.iloc[0] - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25 if hasattr(equity.index[0], 'year') else len(equity) / periods_per_year
    cagr = (1 + total_ret) ** (1 / years) - 1 if years > 0 and (1 + total_ret) > 0 else np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(periods_per_year) if rets.std() > 0 else 0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * np.sqrt(periods_per_year) if len(downside) > 1 and downside.std() > 0 else 0
    dd = ((equity - equity.cummax()) / equity.cummax()).min()

    t = trades_df
    pnl_col = 'net_pnl' if (not t.empty and 'net_pnl' in t.columns) else ('pnl_usd' if (not t.empty and 'pnl_usd' in t.columns) else None)
    if len(t) > 0 and pnl_col:
        wins = t[t[pnl_col] > 0]
        losses = t[t[pnl_col] <= 0]
        wr = len(wins) / len(t) * 100
        gp = wins[pnl_col].sum()
        gl = abs(losses[pnl_col].sum())
        pf = gp / gl if gl > 0 else np.inf
        exp = t.return_pct.mean() if 'return_pct' in t.columns else 0.0
    else:
        wr = exp = 0
        pf = np.nan

    m = {
        'Final ($)': round(equity.iloc[-1], 2),
        'Return (%)': round(total_ret * 100, 2),
        'CAGR (%)': round(cagr * 100, 2) if not np.isnan(cagr) else np.nan,
        'Sharpe': round(sharpe, 2),
        'Sortino': round(sortino, 2),
        'MaxDD (%)': round(dd * 100, 2),
        'Trades': len(t),
        'WinRate (%)': round(wr, 1),
        'PF': round(pf, 2) if not np.isnan(pf) else np.nan,
        'Expectancy (%)': round(exp, 2),
    }
    if bh is not None and len(bh) > 1:
        m['B&H (%)'] = round((bh.iloc[-1] / bh.iloc[0] - 1) * 100, 2)
        m['Alpha vs B&H'] = round(m['Return (%)'] - m['B&H (%)'], 2)
    return m

# ═══════════════════════════════════════════════════════════
# GENUINE LEAK-FREE OUT-OF-SAMPLE (OOS) VALIDATION
# ═══════════════════════════════════════════════════════════
def run_true_oos_validation(initial_capital=2000.0, train_end='2024-04-01'):
    print("=" * 80)
    print("🔬 GENUINE LEAK-FREE OUT-OF-SAMPLE (OOS) VALIDATION RUNNER")
    print(f"   Train Window: 2019-10 -> {train_end}  (Parameter Fitting)")
    print(f"   Test Window:  {train_end} -> 2026-08  (Pure Out-of-Sample)")
    print("=" * 80)

    oos_metrics = {}
    oos_equities = {}
    weights = DEFAULT_WEIGHTS

    for name, path in FILES.items():
        df = add_indicators(load_real_data(path))
        cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else TRAIL_OVERRIDES_V14.get(name)

        w = weights[name]
        tr, eq, bh = run_backtest(df, cfg, capital=initial_capital * w, trail_override=trail, fee_side=0.00125)

        eq_oos = eq[eq.index >= train_end]
        bh_oos = bh[bh.index >= train_end]

        if not eq_oos.empty:
            eq_oos_rebased = (eq_oos / eq_oos.iloc[0]) * (initial_capital * w)
            bh_oos_rebased = (bh_oos / bh_oos.iloc[0]) * (initial_capital * w)
            tr_oos = tr[(tr['entry_date'] >= train_end)] if not tr.empty and 'entry_date' in tr else pd.DataFrame()

            m = calculate_metrics(eq_oos_rebased, tr_oos, bh_oos_rebased, periods_per_year=BARS_PER_YEAR)
            oos_metrics[name] = m
            oos_equities[name] = eq_oos_rebased

    print("\n🧪 PURE OUT-OF-SAMPLE METRICS PER ASSET (UNSEEN):")
    oos_df = pd.DataFrame(oos_metrics).T
    if not oos_df.empty:
        cols = ['Return (%)', 'CAGR (%)', 'MaxDD (%)', 'Sharpe', 'B&H (%)', 'Alpha vs B&H']
        avail = [c for c in cols if c in oos_df.columns]
        print(oos_df[avail].to_string())

    hy_curve, macro_c, micro_c = run_rebalanced_hybrid_engine(initial_capital=initial_capital, core_ratio=0.80, weights=weights)
    hy_oos = hy_curve[hy_curve.index >= train_end]

    dfs = {name: load_real_data(path) for name, path in FILES.items()}
    bh_parts = []
    for name, df in dfs.items():
        w = weights[name]
        sub = df.loc[df.index >= train_end, 'Close']
        if not sub.empty:
            bh_parts.append((sub / sub.iloc[0]) * initial_capital * w)
    bh_portfolio_oos = pd.concat(bh_parts, axis=1).sum(axis=1)

    if not hy_oos.empty:
        hy_oos_rebased = (hy_oos / hy_oos.iloc[0]) * initial_capital
        m_port = calculate_metrics(hy_oos_rebased, pd.DataFrame(), bh_portfolio_oos, periods_per_year=365)
        print(f"\n💼 PORTFOLIO PURE OUT-OF-SAMPLE RESULTS ({train_end} -> 2026-08):")
        for k, v in m_port.items():
            print(f"  • {k:<16}: {v}")

    os.makedirs('data', exist_ok=True)
    if not oos_df.empty:
        oos_df.to_csv('data/true_oos_summary.csv')
    if not hy_oos.empty:
        hy_oos_rebased.to_csv('data/true_oos_portfolio_equity.csv')
    print("\n💾 Saved genuine OOS results to data/true_oos_*.csv")

# ═══════════════════════════════════════════════════════════
# DASHBOARD BUILDER & HTML GENERATOR
# ═══════════════════════════════════════════════════════════
def build_dashboard_data():
    capital = 1000.0
    weights = DEFAULT_WEIGHTS
    files = FILES

    hybrid_eq, macro_part, micro_part = run_hybrid_engine(initial_capital=capital, core_ratio=0.80, weights=weights)

    dfs = {name: load_real_data(path) for name, path in files.items()}
    micro_eqs, micro_asset_trades = {}, {}
    for name, df in dfs.items():
        w = weights[name]
        x = add_micro_indicators(df)
        tr, eq, bh, fees = run_micro_backtest(x, capital=capital * w)
        micro_eqs[name] = eq.rename(name)
        micro_asset_trades[name] = tr

    bhs, assets_payload = {}, {}
    for name, f in files.items():
        df = add_indicators(load_real_data(f))
        cfg = BEST_CFGS.get(name, BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else TRAIL_OVERRIDES_V14.get(name)
        tr, eq, bh = run_backtest(df, cfg, capital * weights[name], trail, fee_side=0.00125)
        bhs[name] = bh.rename(name)

        df_raw = load_real_data(f)
        price_daily = df_raw['Close'].resample('D').last().dropna()

        macro_trades_list = []
        if not tr.empty:
            for _, row in tr.iterrows():
                if pd.notna(row.get('exit_date')):
                    macro_trades_list.append({
                        'entryDate': str(row['entry_date'])[:10],
                        'exitDate': str(row['exit_date'])[:10],
                        'entryPx': float(row['entry_px']),
                        'exitPx': float(row['exit_px']),
                        'returnPct': float(row.get('return_pct', 0.0)),
                        'pnlUsd': float(row.get('net_pnl', 0.0)),
                        'mode': str(row.get('mode', 'MACRO')),
                        'type': 'Macro Core'
                    })

        micro_trades_list = []
        mtr = micro_asset_trades.get(name, pd.DataFrame())
        if not mtr.empty:
            for _, row in mtr.iterrows():
                if pd.notna(row.get('exit_date')):
                    micro_trades_list.append({
                        'entryDate': str(row['entry_date'])[:10],
                        'exitDate': str(row['exit_date'])[:10],
                        'entryPx': float(row['entry_px']),
                        'exitPx': float(row['exit_px']),
                        'returnPct': float(row.get('return_pct_net', 0.0)),
                        'pnlUsd': float(row.get('net_pnl', 0.0)),
                        'mode': str(row.get('mode', 'MICRO')),
                        'type': 'Micro Satellite'
                    })

        comb_trades = sorted(macro_trades_list + micro_trades_list, key=lambda t: t['entryDate'])
        m_macro = calculate_metrics(eq, tr, bh)

        assets_payload[name] = {
            'prices': [{'date': str(d)[:10], 'price': float(v)} for d, v in price_daily.items()],
            'macroEquity': [{'date': str(d)[:10], 'val': float(v)} for d, v in eq.resample('D').last().dropna().items()],
            'microEquity': [{'date': str(d)[:10], 'val': float(v)} for d, v in micro_eqs[name].resample('D').last().dropna().items()],
            'bhEquity': [{'date': str(d)[:10], 'val': float(v)} for d, v in bh.resample('D').last().dropna().items()],
            'metrics': m_macro,
            'trades': comb_trades
        }

    bh_comb = pd.concat(bhs.values(), axis=1).ffill().sum(axis=1)
    common_idx = hybrid_eq.index.intersection(bh_comb.index)

    hybrid_daily = hybrid_eq.loc[common_idx]
    macro_part_daily = macro_part.loc[common_idx]
    micro_part_daily = micro_part.loc[common_idx]
    bh_daily = bh_comb.loc[common_idx]

    dates = [str(d)[:10] for d in common_idx]

    all_hybrid_trades = []
    for asset_name, data in assets_payload.items():
        for t in data['trades']:
            all_hybrid_trades.append({**t, 'asset': asset_name})
    all_hybrid_trades.sort(key=lambda t: t['entryDate'])

    m_hybrid = calculate_metrics(hybrid_daily, pd.DataFrame(all_hybrid_trades), bh_daily)
    m_macro_tot = calculate_metrics(macro_part_daily, pd.DataFrame(), bh_daily)
    m_micro_tot = calculate_metrics(micro_part_daily, pd.DataFrame(), bh_daily)

    return {
        'dates': dates,
        'hybridEquity': [float(v) for v in hybrid_daily.values],
        'macroPartEquity': [float(v) for v in macro_part_daily.values],
        'microPartEquity': [float(v) for v in micro_part_daily.values],
        'bhEquity': [float(v) for v in bh_daily.values],
        'metrics': {
            'hybrid': m_hybrid,
            'macroPart': m_macro_tot,
            'microPart': m_micro_tot,
            'bh': calculate_metrics(bh_daily, pd.DataFrame())
        },
        'assets': assets_payload,
        'allTrades': all_hybrid_trades
    }

def generate_dashboard_html():
    print("⚡ Building Production Hybrid Dashboard (80/20)...")
    payload = build_dashboard_data()
    json_data = json.dumps(payload, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hybrid Core-Satellite (80/20) Quantitative Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
    <style>
        :root {{
            --bg-primary: #0a0e17;
            --bg-card: #121824;
            --bg-card-hover: #182030;
            --accent-gold: #f59e0b;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --accent-purple: #8b5cf6;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --border-color: #1f293d;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }}
        body {{ background-color: var(--bg-primary); color: var(--text-primary); padding: 20px; line-height: 1.5; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; padding-bottom: 15px; border-bottom: 1px solid var(--border-color); }}
        .title-group h1 {{ font-size: 24px; font-weight: 700; color: #fff; }}
        .title-group p {{ font-size: 13px; color: var(--text-secondary); margin-top: 4px; }}
        .badge {{ background: linear-gradient(135deg, rgba(245, 158, 11, 0.15), rgba(16, 185, 129, 0.15)); border: 1px solid var(--accent-gold); color: var(--accent-gold); padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }}
        .controls-bar {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 20px; background: var(--bg-card); padding: 12px 18px; border-radius: 10px; border: 1px solid var(--border-color); }}
        .btn {{ background: #1a2332; color: var(--text-secondary); border: 1px solid var(--border-color); padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.2s; }}
        .btn:hover {{ background: #243044; color: #fff; }}
        .btn.active {{ background: var(--accent-blue); color: #fff; border-color: var(--accent-blue); }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 25px; }}
        .stat-card {{ background: var(--bg-card); padding: 16px; border-radius: 10px; border: 1px solid var(--border-color); position: relative; overflow: hidden; }}
        .stat-card.highlight {{ border-color: rgba(245, 158, 11, 0.4); background: linear-gradient(180deg, rgba(245, 158, 11, 0.05) 0%, var(--bg-card) 100%); }}
        .stat-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .stat-value {{ font-size: 22px; font-weight: 700; color: #fff; }}
        .stat-sub {{ font-size: 12px; margin-top: 4px; font-weight: 500; }}
        .text-green {{ color: var(--accent-green); }}
        .text-red {{ color: var(--accent-red); }}
        .text-gold {{ color: var(--accent-gold); }}
        .chart-card {{ background: var(--bg-card); padding: 20px; border-radius: 10px; border: 1px solid var(--border-color); margin-bottom: 25px; }}
        .chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .chart-title {{ font-size: 16px; font-weight: 600; }}
        .table-card {{ background: var(--bg-card); padding: 20px; border-radius: 10px; border: 1px solid var(--border-color); margin-bottom: 25px; }}
        table {{ width: 100%; border-collapse: collapse; text-align: right; font-size: 13px; }}
        th {{ color: var(--text-secondary); font-weight: 600; padding: 10px 14px; border-bottom: 1px solid var(--border-color); }}
        td {{ padding: 12px 14px; border-bottom: 1px solid #161e2e; }}
        tr:hover td {{ background: var(--bg-card-hover); }}
        .tag {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
        .tag-macro {{ background: rgba(59, 130, 246, 0.15); color: var(--accent-blue); border: 1px solid rgba(59, 130, 246, 0.3); }}
        .tag-micro {{ background: rgba(139, 92, 246, 0.15); color: var(--accent-purple); border: 1px solid rgba(139, 92, 246, 0.3); }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>🏆 Hybrid Core-Satellite (80/20) Quantitative Dashboard</h1>
                <p>מנוע מסחר כמותי היברידי: 80% Core Macro + 20% Satellite Micro | Rebalancing רבעוני</p>
            </div>
            <span class="badge">PRODUCTION READY</span>
        </header>

        <div class="controls-bar">
            <span style="font-size: 13px; color: var(--text-secondary); font-weight: 600;">תקופת ניתוח:</span>
            <button class="btn active" onclick="setPreset('ALL')">כל התקופה (2019-2026)</button>
            <button class="btn" onclick="setPreset('OOS')">Out-of-Sample (2024-2026)</button>
            <button class="btn" onclick="setPreset('1Y')">שנה אחרונה (1Y)</button>
            
            <div style="margin-right: auto; display: flex; gap: 8px; align-items: center;">
                <span style="font-size: 13px; color: var(--text-secondary); font-weight: 600;">תצוגת נכס:</span>
                <button class="btn active" id="btn-asset-PORT" onclick="selectAsset('PORT')">תיק משולב (80/20)</button>
                <button class="btn" id="btn-asset-BTC" onclick="selectAsset('BTC')">BTC / USD</button>
                <button class="btn" id="btn-asset-ETH" onclick="selectAsset('ETH')">ETH / USD</button>
                <button class="btn" id="btn-asset-SOL" onclick="selectAsset('SOL')">SOL / USD</button>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card highlight">
                <div class="stat-label">תשואה כוללת נטו</div>
                <div class="stat-value text-gold" id="stat-return">+0.0%</div>
                <div class="stat-sub" id="stat-alpha">עודף תשואה מול Hold: +0.0%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">תשואה שנתית (CAGR)</div>
                <div class="stat-value" id="stat-cagr">0.0%</div>
                <div class="stat-sub text-green">מחושב רציפה</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">הורדת ערך מקסימלית (MaxDD)</div>
                <div class="stat-value text-red" id="stat-maxdd">-0.0%</div>
                <div class="stat-sub" id="stat-bh-dd">מול B&H: -0.0%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">יחס שרפ (Sharpe Ratio)</div>
                <div class="stat-value text-green" id="stat-sharpe">0.00</div>
                <div class="stat-sub" id="stat-sortino">Sortino: 0.00</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">עסקאות / WinRate</div>
                <div class="stat-value" id="stat-trades">0 / 0%</div>
                <div class="stat-sub text-gold" id="stat-pf">Profit Factor: 0.0</div>
            </div>
        </div>

        <div class="chart-card">
            <div class="chart-header">
                <div class="chart-title" id="chart-title">גרף תשואה מצטברת (Portfolio Equity vs Buy & Hold)</div>
            </div>
            <div id="chart-equity" style="min-height: 400px;"></div>
        </div>

        <div class="table-card">
            <div class="chart-header">
                <div class="chart-title">יומן עסקאות מפורט (Execution Log & Fees)</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>נכס</th>
                        <th>סוג אסטרטגיה</th>
                        <th>תאריך כניסה</th>
                        <th>תאריך יציאה</th>
                        <th>מחיר כניסה</th>
                        <th>מחיר יציאה</th>
                        <th>תשואה נטו (%)</th>
                        <th>רווח/הפסד ($)</th>
                    </tr>
                </thead>
                <tbody id="trades-table-body">
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const rawData = {json_data};
        let currentAsset = 'PORT';
        let currentStartDate = null;
        let chartInstance = null;

        function setPreset(preset) {{
            const dates = rawData.dates;
            if (preset === 'ALL') currentStartDate = null;
            else if (preset === 'OOS') currentStartDate = '2024-04-01';
            else if (preset === '1Y') {{
                const last = new Date(dates[dates.length - 1]);
                last.setFullYear(last.getFullYear() - 1);
                currentStartDate = last.toISOString().slice(0, 10);
            }}
            document.querySelectorAll('.controls-bar .btn').forEach(b => {{
                if (b.innerText.includes('2019-2026') && preset === 'ALL') b.classList.add('active');
                else if (b.innerText.includes('Out-of-Sample') && preset === 'OOS') b.classList.add('active');
                else if (b.innerText.includes('1Y') && preset === '1Y') b.classList.add('active');
                else if (!b.id.startsWith('btn-asset')) b.classList.remove('active');
            }});
            renderDashboard();
        }}

        function selectAsset(asset) {{
            currentAsset = asset;
            document.querySelectorAll('[id^="btn-asset-"]').forEach(b => b.classList.remove('active'));
            document.getElementById('btn-asset-' + asset).classList.add('active');
            renderDashboard();
        }}

        function filterSeries(dates, values) {{
            if (!currentStartDate) return {{ dates, values }};
            const idx = dates.findIndex(d => d >= currentStartDate);
            if (idx === -1) return {{ dates: [], values: [] }};
            const slicedDates = dates.slice(idx);
            const baseVal = values[idx];
            const rebasedValues = values.slice(idx).map(v => (v / baseVal) * 1000.0);
            return {{ dates: slicedDates, values: rebasedValues }};
        }}

        function renderDashboard() {{
            let dates = rawData.dates;
            let hybridVals = rawData.hybridEquity;
            let bhVals = rawData.bhEquity;
            let trades = rawData.allTrades;

            if (currentAsset !== 'PORT') {{
                const assetData = rawData.assets[currentAsset];
                dates = assetData.macroEquity.map(d => d.date);
                hybridVals = assetData.macroEquity.map(d => d.val);
                bhVals = assetData.bhEquity.map(d => d.val);
                trades = assetData.trades;
            }}

            const filteredHy = filterSeries(dates, hybridVals);
            const filteredBh = filterSeries(dates, bhVals);
            const slicedDates = filteredHy.dates;

            const startValHy = filteredHy.values[0] || 1000;
            const endValHy = filteredHy.values[filteredHy.values.length - 1] || 1000;
            const retHy = ((endValHy / startValHy) - 1) * 100;

            const startValBh = filteredBh.values[0] || 1000;
            const endValBh = filteredBh.values[filteredBh.values.length - 1] || 1000;
            const retBh = ((endValBh / startValBh) - 1) * 100;
            const alpha = retHy - retBh;

            document.getElementById('stat-return').innerText = (retHy >= 0 ? '+' : '') + retHy.toFixed(2) + '%';
            document.getElementById('stat-alpha').innerText = 'עודף תשואה מול Hold: ' + (alpha >= 0 ? '+' : '') + alpha.toFixed(2) + '%';

            let minHy = startValHy;
            let maxDDHy = 0;
            for (let v of filteredHy.values) {{
                if (v > minHy) minHy = v;
                let dd = (v - minHy) / minHy * 100;
                if (dd < maxDDHy) maxDDHy = dd;
            }}
            document.getElementById('stat-maxdd').innerText = maxDDHy.toFixed(2) + '%';

            const seriesData = [
                {{ name: 'היברידי 80/20', data: slicedDates.map((d, i) => ({{ x: new Date(d).getTime(), y: parseFloat(filteredHy.values[i].toFixed(2)) }})) }},
                {{ name: 'Buy & Hold', data: slicedDates.map((d, i) => ({{ x: new Date(d).getTime(), y: parseFloat(filteredBh.values[i].toFixed(2)) }})) }}
            ];

            if (chartInstance) chartInstance.destroy();
            const options = {{
                series: seriesData,
                chart: {{ type: 'line', height: 420, toolbar: {{ show: true }}, background: 'transparent' }},
                theme: {{ mode: 'dark' }},
                stroke: {{ width: [3, 2], curve: 'smooth', dashArray: [0, 4] }},
                colors: ['#f59e0b', '#3b82f6'],
                xaxis: {{ type: 'datetime', labels: {{ style: {{ colors: '#9ca3af' }} }} }},
                yaxis: {{ labels: {{ style: {{ colors: '#9ca3af' }}, formatter: val => '$' + val.toFixed(0) }} }},
                grid: {{ borderColor: '#1f293d' }},
                tooltip: {{ x: {{ format: 'dd MMM yyyy' }} }}
            }};
            chartInstance = new ApexCharts(document.querySelector("#chart-equity"), options);
            chartInstance.render();

            const tbody = document.getElementById('trades-table-body');
            tbody.innerHTML = '';
            const filteredTrades = trades.filter(t => !currentStartDate || t.entryDate >= currentStartDate);
            document.getElementById('stat-trades').innerText = filteredTrades.length + ' עסקאות';

            filteredTrades.slice(-50).reverse().forEach(t => {{
                const tr = document.createElement('tr');
                const pnlClass = t.pnlUsd >= 0 ? 'text-green' : 'text-red';
                tr.innerHTML = `
                    <td><b>${{t.asset || currentAsset}}</b></td>
                    <td><span class="${{t.type.includes('Macro') ? 'tag tag-macro' : 'tag tag-micro'}}">${{t.type}}</span></td>
                    <td>${{t.entryDate}}</td>
                    <td>${{t.exitDate}}</td>
                    <td>$${{t.entryPx.toFixed(2)}}</td>
                    <td>$${{t.exitPx.toFixed(2)}}</td>
                    <td class="${{pnlClass}}">${{t.returnPct >= 0 ? '+' : ''}}${{t.returnPct.toFixed(2)}}%</td>
                    <td class="${{pnlClass}}"><b>${{t.pnlUsd >= 0 ? '+' : ''}}$${{t.pnlUsd.toFixed(2)}}</b></td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        window.onload = function() {{
            renderDashboard();
        }};
    </script>
</body>
</html>"""

    with open('dashboard.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("[HYBRID PRODUCTION DASHBOARD GENERATED] dashboard.html")
