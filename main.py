import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# v13 ENGINE — REAL OHLCV + PARTIAL TP + ENTRY FILTERS +
#              DYNAMIC SIZING + PERIOD ROBUSTNESS ANALYSIS
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
FEE_SLIP = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
WARMUP = 300          # bars skipped for indicator warmup (EMA200 etc.)
BARS_PER_YEAR = 2190  # 4h candles

# ── Configuration profiles ──────────────────────────────────
BASELINE_CFG = dict(   # v9 rules, but on REAL OHLCV data
    use_vol_filter=False, use_adx_filter=False,
    trend_adx_min=0.0,   vol_filter_mult=0.0,
    dip_rsi_max=42.0,    dip_vol_mult=0.0,
    base_alloc=0.90, strong_alloc=0.95, highvol_alloc=0.90,
    trail_strong=5.5, trail_trend=3.5,
    dip_tp_atr=1.8, dip_sl_atr=2.0,
    tp1_enabled=False, tp1_trigger_atr=0.0, tp1_fraction=0.0,
    dyn_sizing=False, vol_q=0.75,
)

V13_CFG = dict(        # improved engine
    use_vol_filter=True,  use_adx_filter=True,
    trend_adx_min=20.0,   vol_filter_mult=1.0,   # TREND needs ADX>20 + above-avg volume
    dip_rsi_max=35.0,     dip_vol_mult=1.5,      # DIP heavily tightened
    base_alloc=0.90, strong_alloc=0.95, highvol_alloc=0.60,  # dynamic sizing
    trail_strong=5.5, trail_trend=3.5,
    dip_tp_atr=1.8, dip_sl_atr=2.0,
    tp1_enabled=True, tp1_trigger_atr=4.5, tp1_fraction=0.30,  # partial TP (later trigger)
    tp1_be_floor_atr=1.0,   # after TP1, stop floor = BE + 1*ATR (lock profit, keep room)
    init_risk_atr=3.5,      # hard initial risk cap: stop >= entry - 3.5*ATR
    dyn_sizing=True, vol_q=0.75,
)

def make_cfg(**overrides):
    c = dict(V13_CFG)
    c.update(overrides)
    return c

# Per-asset ATR-trail overrides (strong, trend) — wider trails for high-beta assets
TRAIL_OVERRIDES_V13 = {'SOL': (6.5, 4.0)}

# ───────────────────────────────────────────────────────────
# 1. DATA LOADING (REAL OHLCV)
# ───────────────────────────────────────────────────────────
def generate_synthetic_data(ticker='BTC', days=1000):
    np.random.seed(42)
    dates = pd.date_range(end=datetime.today(), periods=days * 6, freq='4h')
    returns = np.random.normal(0.0005, 0.015, len(dates))
    close = 100 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({'Date': dates, 'Close': close})
    df['Open'] = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    df['High'] = df[['Open', 'Close']].max(axis=1) * (1 + np.random.uniform(0, 0.01, len(df)))
    df['Low'] = df[['Open', 'Close']].min(axis=1) * (1 - np.random.uniform(0, 0.01, len(df)))
    df['Volume'] = np.random.uniform(1e6, 5e6, len(df))
    return df.set_index('Date').sort_index()

def load_real_data(filepath):
    """Loads REAL OHLCV candles. Falls back to synthetic only if file missing."""
    if not os.path.exists(filepath):
        alt = os.path.join('data', os.path.basename(filepath))
        if os.path.exists(alt):
            filepath = alt
    if not os.path.exists(filepath):
        print(f"[WARN] '{filepath}' not found -> synthetic data")
        return generate_synthetic_data(os.path.basename(filepath).split('_')[0])

    df = pd.read_csv(filepath)
    date_col = 'observation_date' if 'observation_date' in df.columns else 'Date'
    df['Date'] = pd.to_datetime(df[date_col])
    for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if c not in df.columns:
            raise ValueError(f"{filepath}: missing column '{c}' (real OHLCV required)")
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['Date', 'Open', 'High', 'Low', 'Close']).set_index('Date').sort_index()
    if 'Volume' not in df or df['Volume'].isna().all():
        df['Volume'] = 1e6
    df['Volume'] = df['Volume'].fillna(1e6)
    print(f"[DATA] {os.path.basename(filepath):<18} {len(df):>6} candles | "
          f"{df.index[0].date()} -> {df.index[-1].date()} | real OHLCV")
    return df

# ───────────────────────────────────────────────────────────
# 2. INDICATORS & REGIME DETECTION
# ───────────────────────────────────────────────────────────
def add_indicators(df, vol_q=0.75):
    x = df.copy()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()

    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    delta = x.Close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))

    up_move = x.High - x.High.shift(1)
    down_move = x.Low.shift(1) - x.Low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_smooth = pd.Series(tr, index=x.index).ewm(alpha=1 / 14, min_periods=14).mean()
    plus_di = 100 * pd.Series(plus_dm, index=x.index).ewm(alpha=1 / 14, min_periods=14).mean() / tr_smooth
    minus_di = 100 * pd.Series(minus_dm, index=x.index).ewm(alpha=1 / 14, min_periods=14).mean() / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["ADX"] = dx.ewm(alpha=1 / 14, min_periods=14).mean()

    high30, low30 = x.High.rolling(30).max(), x.Low.rolling(30).min()
    x["RangeToATR"] = (high30 - low30) / x.ATR
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
    x["Regime"] = regimes

    # Volatility regime flag — expanding quantile shifted(1) => NO lookahead
    atrpct = x.ATR / x.Close * 100
    qthr = atrpct.expanding(min_periods=WARMUP).quantile(vol_q).shift(1)
    x["HighVol"] = (atrpct > qthr).fillna(False)

    return x.dropna(subset=['ATR', 'RSI', 'ADX', 'EMA200'])

# ───────────────────────────────────────────────────────────
# 3. BACKTEST ENGINE (single engine, config-driven)
# ───────────────────────────────────────────────────────────
def run_backtest(df, cfg, capital=INITIAL_CAPITAL, trail_override=None):
    """Returns (trades_df, equity_series, buyhold_series)."""
    ts, tt = (trail_override if trail_override else (cfg['trail_strong'], cfg['trail_trend']))

    cash = capital
    in_pos = False
    mode = None
    entry_px = units = highest_px = invested = be_px = 0.0
    tp1_done = False
    entry_i = 0
    trade_pnl = 0.0
    in_pos_bars = 0
    last_loss_i = -10 ** 9          # for post-loss cooldown
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]

    def close_trade(i, raw_exit_px, reason):
        nonlocal cash, in_pos, units, trade_pnl, last_loss_i
        final_px = raw_exit_px * (1 - FEE_SLIP)
        leg_pnl = units * (final_px - entry_px)
        cash += units * final_px           # release remaining position value
        trade_pnl += leg_pnl
        if trade_pnl < 0:
            last_loss_i = i                # start cooldown after a losing trade
        trades[-1].update({
            'exit_date': df.index[i], 'exit': final_px,
            'pnl_usd': round(trade_pnl, 4),
            'return_pct': round(trade_pnl / invested * 100, 4) if invested else 0.0,
            'bars_held': i - entry_i, 'tp1': tp1_done, 'reason': reason,
        })
        in_pos, units, trade_pnl = False, 0.0, 0.0

    for i in range(WARMUP, len(df)):
        r = df.iloc[i]

        if not in_pos:
            # ── WR-boosting entry gates (all default-off unless enabled in cfg) ──
            cooldown_ok = (cfg.get('cooldown_bars', 0) <= 0) or (i - last_loss_i > cfg['cooldown_bars'])
            ok_candle = (not cfg.get('entry_candle_quality', False)) or (r.Close > (r.High + r.Low) / 2)
            rsi_ok = r.RSI < cfg.get('entry_rsi_max', 100.0)
            vol_ok = True
            if cfg['use_vol_filter']:
                vol_ok = (not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * cfg['vol_filter_mult']

            entered, mode = False, 'CASH'
            if cooldown_ok and ok_candle and rsi_ok:
                if r.Regime == 'STRONG_BULL' and r.Close >= r.Donchian30 and vol_ok:
                    entered, mode = True, 'STRONG_BULL_TREND'
                elif (r.Regime == 'BULL' and r.Close >= r.Donchian30 and vol_ok
                      and (not cfg['use_adx_filter'] or r.ADX > cfg['trend_adx_min'])):
                    entered, mode = True, 'TREND'
                elif (r.Regime == 'SIDEWAYS' and r.Close > r.EMA200 and r.RSI < cfg['dip_rsi_max']
                      and r.Close > r.Open
                      and (cfg['dip_vol_mult'] <= 0 or
                           ((not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * cfg['dip_vol_mult']))):
                    entered, mode = True, 'DIP'
            if entered:
                alloc = cfg['strong_alloc'] if mode == 'STRONG_BULL_TREND' else cfg['base_alloc']
                if cfg['dyn_sizing'] and bool(r.HighVol):
                    alloc = min(alloc, cfg['highvol_alloc'])
                entry_px = r.Close * (1 + FEE_SLIP)
                invested = cash * alloc
                units = invested / entry_px
                highest_px = entry_px
                tp1_done = False
                be_px = entry_px * (1 + FEE_SLIP) / (1 - FEE_SLIP)  # breakeven incl. fees
                entry_i, trade_pnl, in_pos = i, 0.0, True
                cash -= invested
                trades.append({'entry_date': df.index[i], 'entry': entry_px, 'mode': mode})

        else:
            in_pos_bars += 1
            highest_px = max(highest_px, r.High)
            exit_now, exit_px, reason = False, None, None

            if mode == 'DIP':
                target_px = entry_px + cfg['dip_tp_atr'] * r.ATR
                stop_px = entry_px - cfg['dip_sl_atr'] * r.ATR
                if r.Low <= stop_px:                       # conservative: stop first
                    exit_now, exit_px, reason = True, min(stop_px, r.Close), 'dip_stop'
                elif r.High >= target_px:
                    exit_now, exit_px, reason = True, target_px, 'dip_target'
            else:
                m = ts if mode == 'STRONG_BULL_TREND' else tt
                raw_trail = highest_px - m * r.ATR
                if tp1_done:
                    floor_px = be_px + cfg.get('tp1_be_floor_atr', 0.0) * r.ATR
                    stop_px = max(raw_trail, floor_px)
                else:
                    stop_px = raw_trail
                    risk_capped = (cfg.get('init_risk_atr', 0) > 0
                                   and mode in cfg.get('init_risk_modes',
                                                       ('STRONG_BULL_TREND', 'TREND')))
                    if risk_capped:                        # cap initial risk on entry
                        stop_px = max(stop_px, entry_px - cfg['init_risk_atr'] * r.ATR)
                if r.Low <= stop_px:
                    exit_now = True
                    exit_px = min(stop_px, r.Close)
                    if tp1_done and stop_px >= raw_trail:
                        reason = 'be_stop'
                    elif (not tp1_done) and risk_capped \
                            and stop_px >= entry_px - cfg['init_risk_atr'] * r.ATR:
                        reason = 'risk_cap'
                    else:
                        reason = 'atr_trail'
                else:
                    if cfg['tp1_enabled'] and not tp1_done:
                        trigger = entry_px + cfg['tp1_trigger_atr'] * r.ATR
                        if r.High >= trigger:               # partial TP fill at trigger
                            sell_u = units * cfg['tp1_fraction']
                            fill = trigger * (1 - FEE_SLIP)
                            leg = sell_u * (fill - entry_px)
                            cash += sell_u * fill
                            trade_pnl += leg
                            units -= sell_u
                            tp1_done = True
                    ema_ref = r.EMA200 if mode == 'STRONG_BULL_TREND' else r.EMA50
                    if r.Close < ema_ref:
                        exit_now, exit_px, reason = True, r.Close, 'ema_exit'

            if exit_now:
                close_trade(i, exit_px, reason)

        eq_val.append(cash + units * r.Close)
        eq_idx.append(df.index[i])

    if in_pos and trades:                               # force-close open position at end
        close_trade(len(df) - 1, df.Close.iloc[-1], 'end_of_test')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[WARMUP] * df.Close.iloc[WARMUP:]
    bh.index = df.index[WARMUP:]
    return pd.DataFrame(trades), equity, bh

# ───────────────────────────────────────────────────────────
# 4. METRICS
# ───────────────────────────────────────────────────────────
def calculate_metrics(equity, trades_df, bh=None, periods_per_year=BARS_PER_YEAR):
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

    t = trades_df.dropna(subset=['return_pct']) if not trades_df.empty else pd.DataFrame()
    if len(t):
        wins, losses = t[t.return_pct > 0], t[t.return_pct <= 0]
        wr = len(wins) / len(t) * 100
        gp, gl = wins.pnl_usd.sum(), abs(losses.pnl_usd.sum())
        pf = gp / gl if gl > 0 else np.inf
        avg_w = wins.return_pct.mean() if len(wins) else 0
        avg_l = losses.return_pct.mean() if len(losses) else 0
        expectancy = t.return_pct.mean()
        top5_share = t.nlargest(5, 'pnl_usd').pnl_usd.sum() / gp * 100 if gp > 0 else np.nan
    else:
        wr = avg_w = avg_l = expectancy = top5_share = 0
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
        'AvgWin (%)': round(avg_w, 2),
        'AvgLoss (%)': round(avg_l, 2),
        'Expectancy (%)': round(expectancy, 2),
        'Top5Share (%)': round(top5_share, 1) if not np.isnan(top5_share) else np.nan,
    }
    if bh is not None and len(bh) > 1:
        m['B&H (%)'] = round((bh.iloc[-1] / bh.iloc[0] - 1) * 100, 2)
        m['Alpha vs B&H'] = round(m['Return (%)'] - m['B&H (%)'], 2)
    return m

# ───────────────────────────────────────────────────────────
# 5. BREAKDOWN HELPERS
# ───────────────────────────────────────────────────────────
def mode_breakdown(trades_df):
    if trades_df.empty or 'mode' not in trades_df.columns:
        return pd.DataFrame()
    t = trades_df.dropna(subset=['return_pct'])
    rows = []
    for name, g in t.groupby('mode'):
        w, l = g[g.return_pct > 0], g[g.return_pct <= 0]
        gl = abs(l.pnl_usd.sum())
        rows.append({
            'Mode': name, 'Trades': len(g),
            'WinRate%': round(len(w) / len(g) * 100, 1) if len(g) else 0,
            'NetPnL$': round(g.pnl_usd.sum(), 2),
            'AvgRet%': round(g.return_pct.mean(), 2),
            'PF': round(w.pnl_usd.sum() / gl, 2) if gl > 0 else np.inf,
            'TP1%': round(g.tp1.mean() * 100, 0) if 'tp1' in g else np.nan,
        })
    return pd.DataFrame(rows).set_index('Mode')

def reason_breakdown(trades_df):
    if trades_df.empty or 'reason' not in trades_df.columns:
        return pd.DataFrame()
    t = trades_df.dropna(subset=['return_pct'])
    rows = []
    for name, g in t.groupby('reason'):
        rows.append({'ExitReason': name, 'Trades': len(g),
                     'NetPnL$': round(g.pnl_usd.sum(), 2),
                     'AvgRet%': round(g.return_pct.mean(), 2)})
    return pd.DataFrame(rows).set_index('ExitReason')

def annual_breakdown(equity):
    if equity.empty:
        return pd.DataFrame()
    d = equity.to_frame('Eq'); d['Y'] = d.index.year
    rows = []
    for y, g in d.groupby('Y'):
        dd = ((g.Eq - g.Eq.cummax()) / g.Eq.cummax()).min()
        rows.append({'Year': y, 'Start$': round(g.Eq.iloc[0], 2), 'End$': round(g.Eq.iloc[-1], 2),
                     'Ret%': round((g.Eq.iloc[-1] / g.Eq.iloc[0] - 1) * 100, 2),
                     'MaxDD%': round(dd * 100, 2)})
    return pd.DataFrame(rows).set_index('Year')

def yearly_runs(df, cfg, trail_override=None, min_bars=600):
    """Independent fresh-capital run per calendar year (context warmup included)."""
    out = []
    for y in sorted(df.index.year.unique()):
        idx = np.where(df.index.year == y)[0]
        if len(idx) < min_bars:
            continue
        s = max(0, idx[0] - WARMUP)
        sub = df.iloc[s:idx[-1] + 1]
        tr, eq, bh = run_backtest(sub, cfg, INITIAL_CAPITAL, trail_override)
        eq_y = eq[eq.index.year == y]
        bh_y = bh[bh.index.year == y]
        tr_y = tr[tr.exit_date.dt.year == y] if (not tr.empty and 'exit_date' in tr) else tr
        m = calculate_metrics(eq_y, tr_y, bh_y)
        m = {'Year': y, **m}
        out.append(m)
    return pd.DataFrame(out).set_index('Year') if out else pd.DataFrame()

def extreme_trades(trades_df, top_n=5):
    if trades_df.empty or 'return_pct' not in trades_df.columns:
        return pd.DataFrame(), pd.DataFrame()
    v = trades_df.dropna(subset=['exit_date', 'return_pct']).sort_values('return_pct', ascending=False)
    cols = [c for c in ['asset', 'entry_date', 'exit_date', 'mode', 'pnl_usd', 'return_pct', 'bars_held', 'tp1'] if c in v.columns]
    return v.head(top_n)[cols], v.tail(top_n)[cols]

# ───────────────────────────────────────────────────────────
# 6. PORTFOLIO (50/30/20 BTC/ETH/SOL)
# ───────────────────────────────────────────────────────────
def run_portfolio(dfs, cfg, weights, capital=INITIAL_CAPITAL, apply_overrides=False):
    res, eqs = {}, {}
    for name, df in dfs.items():
        ov = TRAIL_OVERRIDES_V13.get(name) if apply_overrides else None
        tr, eq, bh = run_backtest(df, cfg, capital * weights[name], ov)
        res[name] = {'trades': tr, 'equity': eq, 'bh': bh}
        eqs[name] = eq.rename(name)
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in dfs:
        comb[name] = comb[name].fillna(capital * weights[name])
    return res, comb.sum(axis=1)

# ───────────────────────────────────────────────────────────
# 7. PLOTTING
# ───────────────────────────────────────────────────────────
def plot_results(btc_df, curves, outfile='backtest_results_chart.png'):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=True,
                                   gridspec_kw={'height_ratios': [1.6, 1.0]})
    ax1.plot(btc_df.index, btc_df.Close, color='#2D3748', lw=1.1, alpha=0.8, label='BTC-USD')
    ax1.plot(btc_df.index, btc_df.EMA50, '--', color='#3182CE', lw=1.0, label='EMA50')
    ax1.plot(btc_df.index, btc_df.EMA200, '--', color='#E53E3E', lw=1.0, label='EMA200')
    ax1.set_yscale('log'); ax1.set_title('Price (log) — Real OHLCV', fontweight='bold')
    ax1.legend(loc='upper left'); ax1.grid(alpha=0.3, which='both', linestyle=':')

    colors = {'V13 BTC': '#2B6CB0', 'Baseline BTC': '#A0AEC0', 'BTC Buy&Hold': '#718096', 'V13 Portfolio 50/30/20': '#D69E2E'}
    for label, ser in curves.items():
        if ser is not None:
            ax2.plot(ser.index, ser.values, lw=1.8,
                     label=f"{label} (${ser.iloc[-1]:,.0f})",
                     color=colors.get(label, None))
    ax2.axhline(INITIAL_CAPITAL, color='gray', ls=':', lw=0.8)
    ax2.set_yscale('log'); ax2.set_title('Equity (log) — $1,000 start', fontweight='bold')
    ax2.legend(loc='upper left'); ax2.grid(alpha=0.3, linestyle='--')
    plt.tight_layout(); plt.savefig(outfile, dpi=150, bbox_inches='tight'); plt.close()
    print(f"[CHART] saved -> {outfile}")

# ───────────────────────────────────────────────────────────
# 8. REPORT PRINTER
# ───────────────────────────────────────────────────────────
def print_metrics(title, m):
    print("=" * 74); print(title); print("=" * 74)
    if not m:
        print("  (no data)\n"); return
    for k, v in m.items():
        print(f"  {k:<16}: {v:>12}")
    print()

def print_conclusions(base_m, v13_m, v13_modes, yearly_df, cfg_name='V13'):
    print("=" * 74); print("🧠 AUTO-CONCLUSIONS"); print("=" * 74)
    if base_m and v13_m:
        print(f"  • Real-OHLCV baseline : Ret {base_m['Return (%)']}% | MaxDD {base_m['MaxDD (%)']}% | PF {base_m['PF']} | WR {base_m['WinRate (%)']}%")
        print(f"  • {cfg_name:<21}: Ret {v13_m['Return (%)']}% | MaxDD {v13_m['MaxDD (%)']}% | PF {v13_m['PF']} | WR {v13_m['WinRate (%)']}%")
        d_ret = v13_m['Return (%)'] - base_m['Return (%)']
        d_dd = v13_m['MaxDD (%)'] - base_m['MaxDD (%)']
        print(f"  • Delta               : Return {d_ret:+.1f}% | MaxDD {d_dd:+.1f}% (less negative = better)")
    if v13_modes is not None and not v13_modes.empty and 'PF' in v13_modes.columns:
        ranked = v13_modes.sort_values('PF', ascending=False)
        best, worst = ranked.index[0], ranked.index[-1]
        print(f"  • Best mode: {best} (PF {ranked.loc[best, 'PF']}) | Worst: {worst} (PF {ranked.loc[worst, 'PF']})")
    if yearly_df is not None and not yearly_df.empty and 'Alpha vs B&H' in yearly_df.columns:
        beat = (yearly_df['Alpha vs B&H'] > 0).sum()
        print(f"  • Beat Buy&Hold in {beat}/{len(yearly_df)} calendar years")
        if 'Return (%)' in yearly_df.columns:
            worst_y = yearly_df.sort_values('Return (%)').index[0]
            print(f"  • Hardest year: {worst_y} (Ret {yearly_df.loc[worst_y, 'Return (%)']}%, "
                  f"B&H {yearly_df.loc[worst_y].get('B&H (%)', 'n/a')}%)")
    print()

# ═══════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 v13 ENGINE — Real OHLCV + Partial TP + Filters + Dynamic Sizing\n")

    btc = add_indicators(load_real_data('BTC_USD_4h.csv'), vol_q=V13_CFG['vol_q'])
    eth = add_indicators(load_real_data('ETH_USD_4h.csv'), vol_q=V13_CFG['vol_q'])
    sol = add_indicators(load_real_data('SOL_USD_4h.csv'), vol_q=V13_CFG['vol_q'])
    dfs = {'BTC': btc, 'ETH': eth, 'SOL': sol}
    weights = {'BTC': 0.50, 'ETH': 0.30, 'SOL': 0.20}   # user allocation 50/30/20

    # ── STEP 1: BASELINE (v9 rules on real data) ──
    b_tr, b_eq, b_bh = run_backtest(btc, BASELINE_CFG)
    b_m = calculate_metrics(b_eq, b_tr, b_bh)
    print_metrics("📊 1. BASELINE (v9 rules, REAL OHLCV) — BTC", b_m)

    # ── STEP 2: PARAMETER SWEEP (data-driven selection on BTC) ──
    print("=" * 74); print("🔬 2. PARAMETER SWEEP — BTC full period"); print("=" * 74)
    G_PARAMS = dict(tp1_trigger_atr=3.0, tp1_be_floor_atr=0.0,
                    init_risk_atr=3.5, init_risk_modes=('TREND',))
    sweep = {
        'G_base':          make_cfg(**G_PARAMS),
        'K_trendADX25':    make_cfg(**G_PARAMS, trend_adx_min=25.0),
        'L_volMult1.3':    make_cfg(**G_PARAMS, vol_filter_mult=1.3),
        'M_candleQuality': make_cfg(**G_PARAMS, entry_candle_quality=True),
        'N_cooldown6':     make_cfg(**G_PARAMS, cooldown_bars=6),
        'O_rsiMax68':      make_cfg(**G_PARAMS, entry_rsi_max=68.0),
        'P_KM+N_combo':    make_cfg(**G_PARAMS, trend_adx_min=25.0,
                                    entry_candle_quality=True, cooldown_bars=6),
    }
    rows = []
    for name, cfg_v in sweep.items():
        tr_s, eq_s, bh_s = run_backtest(btc, cfg_v, trail_override=TRAIL_OVERRIDES_V13.get('BTC'))
        m_s = calculate_metrics(eq_s, tr_s, bh_s)
        calmar = m_s['Return (%)'] / abs(m_s['MaxDD (%)']) if m_s.get('MaxDD (%)') else np.nan
        rows.append({'Variant': name,
                     **{k: m_s.get(k) for k in ['Return (%)', 'MaxDD (%)', 'Sharpe', 'PF', 'WinRate (%)', 'Trades']},
                     'Calmar': round(calmar, 2)})
    sweep_df = pd.DataFrame(rows).set_index('Variant')
    print(sweep_df.to_string()); print()
    best_name = sweep_df.sort_values(['Calmar', 'Sharpe'], ascending=False).index[0]
    FINAL_CFG = sweep[best_name]
    print(f"[SELECTED] Best variant by Calmar->Sharpe: {best_name}\n")

    # ── STEP 2b: V13 FINAL CONFIG ──
    v_tr, v_eq, v_bh = run_backtest(btc, FINAL_CFG, trail_override=TRAIL_OVERRIDES_V13.get('BTC'))
    v_m = calculate_metrics(v_eq, v_tr, v_bh)
    print_metrics(f"🚀 2b. V13 FINAL ({best_name}) — BTC", v_m)

    # ── STEP 3: MODE & EXIT-REASON BREAKDOWN (V13 BTC) ──
    print("=" * 74); print("🎯 3. V13 MODE BREAKDOWN — BTC"); print("=" * 74)
    v_modes = mode_breakdown(v_tr); print(v_modes.to_string()); print()
    print("=" * 74); print("🚪 3b. V13 EXIT-REASON BREAKDOWN — BTC"); print("=" * 74)
    print(reason_breakdown(v_tr).to_string()); print()

    # ── STEP 4: YEARLY INDEPENDENT RUNS (fresh $1,000 each year) ──
    print("=" * 74); print("📅 4. YEARLY INDEPENDENT RUNS — BTC (fresh $1,000 per year)"); print("=" * 74)
    yr_base = yearly_runs(btc, BASELINE_CFG)
    yr_v13 = yearly_runs(btc, FINAL_CFG, trail_override=TRAIL_OVERRIDES_V13.get('BTC'))
    print("--- Baseline ---"); print(yr_base.to_string()); print()
    print("--- V13 ---");     print(yr_v13.to_string()); print()

    # ── STEP 5: PER-ASSET V13 RESULTS ──
    print("=" * 74); print("🌐 5. V13 PER-ASSET RESULTS (full period)"); print("=" * 74)
    asset_rows = []
    for name, df in dfs.items():
        tr, eq, bh = run_backtest(df, FINAL_CFG, INITIAL_CAPITAL, TRAIL_OVERRIDES_V13.get(name))
        m = calculate_metrics(eq, tr, bh)
        asset_rows.append({'Asset': name, **{k: m.get(k) for k in ['Return (%)', 'MaxDD (%)', 'Sharpe', 'Trades', 'WinRate (%)', 'PF', 'B&H (%)', 'Alpha vs B&H']}})
    print(pd.DataFrame(asset_rows).set_index('Asset').to_string()); print()

    # ── STEP 6: PORTFOLIO 50/30/20 ──
    port_res, port_eq = run_portfolio(dfs, FINAL_CFG, weights, apply_overrides=True)
    port_base_res, port_base_eq = run_portfolio(dfs, BASELINE_CFG, weights)
    all_tr = pd.concat([d['trades'].assign(asset=n) for n, d in port_res.items()], ignore_index=True)
    p_m = calculate_metrics(port_eq, all_tr)
    print_metrics("💼 6. V13 PORTFOLIO 50/30/20 (BTC/ETH/SOL)", p_m)
    print("=" * 74); print("💼 6b. PORTFOLIO MODE BREAKDOWN (all assets)"); print("=" * 74)
    p_modes = mode_breakdown(all_tr); print(p_modes.to_string()); print()

    # ── STEP 7: TOP/WORST TRADES (portfolio) ──
    best_df, worst_df = extreme_trades(all_tr, 5)
    print("=" * 74); print("🏆 7. TOP 5 BEST TRADES (PORTFOLIO)"); print("=" * 74)
    print(best_df.to_string(index=False)); print()
    print("=" * 74); print("⚠️ 7b. TOP 5 WORST TRADES (PORTFOLIO)"); print("=" * 74)
    print(worst_df.to_string(index=False)); print()

    # ── STEP 8: ANNUAL COMPOUNDED BREAKDOWN (portfolio) ──
    print("=" * 74); print("📆 8. PORTFOLIO ANNUAL (compounded)"); print("=" * 74)
    print(annual_breakdown(port_eq).to_string()); print()

    # ── CONCLUSIONS ──
    print_conclusions(b_m, v_m, v_modes, yr_v13, best_name)

    # ── SAVE ARTIFACTS ──
    os.makedirs('data', exist_ok=True)
    v_tr.assign(asset='BTC').to_csv('data/v13_trades_btc.csv', index=False)
    all_tr.to_csv('data/v13_trades.csv', index=False)
    port_eq.rename('PortfolioEquity').to_csv('data/v13_equity.csv')
    yr_v13.reset_index().to_csv('data/v13_summary.csv', index=False)
    print("[SAVED] data/v13_trades.csv | data/v13_trades_btc.csv | data/v13_equity.csv | data/v13_summary.csv")

    plot_results(btc, {
        'V13 BTC': v_eq,
        'Baseline BTC': b_eq,
        'BTC Buy&Hold': b_bh,
        'V13 Portfolio 50/30/20': port_eq,
    })
