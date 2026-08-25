"""
LONG/SHORT ENGINE — adds SHORT entries in BEAR markets
======================================================
Question answered: "what if we also SHORT in bearish markets?"

Engine (run_ls) extends the v13 engine:
  LONG side : identical to v13 (STRONG_BULL_TREND / TREND / DIP) — untouched.
  SHORT side: active ONLY in BEAR regime:
    • SHORT_TREND — breakdown: Close <= Donchian30Low, ADX > trend_adx_min,
                    above-avg volume, RSI > short_rsi_min (no shorting oversold).
                    Exit: ATR trail from below + initial risk cap + partial TP1
                          + cover when Close reclaims EMA200.
    • SHORT_RIP   — mirror of DIP: below EMA200, RSI > 100-dip_rsi_max,
                    red candle, volume spike. Fixed ATR target/stop.
  Accounting: 1x cash-backed shorts, same fee+slippage per side.

Comparison per window:  Long/Short  vs  Long-only (v13)  vs  Buy&Hold.
Usage: python3 short_engine.py
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from main import (load_real_data, add_indicators, run_backtest,
                  calculate_metrics, make_cfg, V13_CFG,
                  TRAIL_OVERRIDES_V13, WARMUP, FEE_SLIP, INITIAL_CAPITAL)
from period_compare import (FILES, FIXED_MONTHS, ROLL_MONTHS, ROLL_STEP,
                            slice_window, bh_drawdown)

# ── LS configuration: v13 longs + bear-market shorts ──────────────────
LS_CFG = make_cfg(
    use_rip=True,          # enable SHORT_RIP (mirror of DIP)
    short_use_adx=True,    # SHORT_TREND needs ADX > trend_adx_min
    short_rsi_min=45.0,    # don't initiate shorts while oversold
)

# ── data with Donchian-low channel ────────────────────────────────────
def load_all_ls():
    dfs = {}
    for name, f in FILES.items():
        df = add_indicators(load_real_data(f), vol_q=V13_CFG['vol_q'])
        df['Donchian30Low'] = df.Low.rolling(30).min().shift(1)
        dfs[name] = df
    return dfs

# ══════════════════════════════════════════════════════════════════════
# LONG/SHORT BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════
def run_ls(df, cfg, capital=INITIAL_CAPITAL, trail_override=None):
    """v13 longs + BEAR-regime shorts. Returns (trades_df, equity, buyhold)."""
    ts, tt = (trail_override if trail_override else (cfg['trail_strong'], cfg['trail_trend']))
    rip_enabled = cfg.get('use_rip', False)

    cash = capital
    in_pos = False
    direction = 0                 # +1 long, -1 short, 0 flat
    mode = None
    entry_px = units = extreme_px = invested = be_px = 0.0
    tp1_done = False
    entry_i = 0
    trade_pnl = 0.0
    last_loss_i = -10 ** 9
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]

    def close_trade(i, raw_exit_px, reason):
        nonlocal cash, in_pos, units, trade_pnl, last_loss_i, direction
        if direction > 0:                                   # LONG close
            final_px = raw_exit_px * (1 - FEE_SLIP)
            leg_pnl = units * (final_px - entry_px)
            cash += units * final_px
        else:                                               # SHORT close (cover)
            final_px = raw_exit_px * (1 + FEE_SLIP)
            leg_pnl = units * (entry_px - final_px)
            cash -= units * final_px
        trade_pnl += leg_pnl
        if trade_pnl < 0:
            last_loss_i = i
        trades[-1].update({
            'exit_date': df.index[i], 'exit': final_px,
            'pnl_usd': round(trade_pnl, 4),
            'return_pct': round(trade_pnl / invested * 100, 4) if invested else 0.0,
            'bars_held': i - entry_i, 'tp1': tp1_done, 'reason': reason,
        })
        in_pos, units, trade_pnl, direction = False, 0.0, 0.0, 0

    for i in range(WARMUP, len(df)):
        r = df.iloc[i]

        if not in_pos:
            cooldown_ok = (cfg.get('cooldown_bars', 0) <= 0) or (i - last_loss_i > cfg['cooldown_bars'])
            vol_ok = True
            if cfg['use_vol_filter']:
                vol_ok = (not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * cfg['vol_filter_mult']

            entered, mode, direction = False, 'CASH', 0

            if cooldown_ok and r.Regime != 'BEAR':
                # ── LONG side: exact v13 logic ──
                ok_candle = (not cfg.get('entry_candle_quality', False)) or (r.Close > (r.High + r.Low) / 2)
                rsi_ok = r.RSI < cfg.get('entry_rsi_max', 100.0)
                if ok_candle and rsi_ok:
                    if r.Regime == 'STRONG_BULL' and r.Close >= r.Donchian30 and vol_ok:
                        entered, mode, direction = True, 'STRONG_BULL_TREND', 1
                    elif (r.Regime == 'BULL' and r.Close >= r.Donchian30 and vol_ok
                          and (not cfg['use_adx_filter'] or r.ADX > cfg['trend_adx_min'])):
                        entered, mode, direction = True, 'TREND', 1
                    elif (r.Regime == 'SIDEWAYS' and r.Close > r.EMA200 and r.RSI < cfg['dip_rsi_max']
                          and r.Close > r.Open
                          and (cfg['dip_vol_mult'] <= 0 or
                               ((not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * cfg['dip_vol_mult']))):
                        entered, mode, direction = True, 'DIP', 1

            elif cooldown_ok and r.Regime == 'BEAR':
                # ── SHORT side: bear-market shorts ──
                rsi_ok_s = r.RSI > cfg.get('short_rsi_min', 0.0)
                adx_ok_s = (not cfg.get('short_use_adx', True)) or r.ADX > cfg['trend_adx_min']
                dlow_ok = (not np.isnan(r.Donchian30Low)) and r.Close <= r.Donchian30Low
                if dlow_ok and adx_ok_s and rsi_ok_s and vol_ok:
                    entered, mode, direction = True, 'SHORT_TREND', -1
                elif (rip_enabled and r.Close < r.EMA200
                      and r.RSI > (100.0 - cfg['dip_rsi_max'])
                      and r.Close < r.Open
                      and (not np.isnan(r.VolSMA20)) and r.Volume > r.VolSMA20 * cfg['dip_vol_mult']):
                    entered, mode, direction = True, 'SHORT_RIP', -1

            if entered:
                alloc = cfg['strong_alloc'] if mode == 'STRONG_BULL_TREND' else cfg['base_alloc']
                if cfg['dyn_sizing'] and bool(r.HighVol):
                    alloc = min(alloc, cfg['highvol_alloc'])
                invested = cash * alloc
                if direction > 0:
                    entry_px = r.Close * (1 + FEE_SLIP)
                    cash -= invested
                else:
                    entry_px = r.Close * (1 - FEE_SLIP)
                    cash += invested                        # short-sale proceeds held
                units = invested / entry_px
                extreme_px = entry_px
                tp1_done = False
                be_px = (entry_px * (1 + FEE_SLIP) / (1 - FEE_SLIP)) if direction > 0 \
                    else (entry_px * (1 - FEE_SLIP) / (1 + FEE_SLIP))
                entry_i, trade_pnl, in_pos = i, 0.0, True
                trades.append({'entry_date': df.index[i], 'entry': entry_px,
                               'mode': mode, 'dir': 'LONG' if direction > 0 else 'SHORT'})

        else:
            extreme_px = max(extreme_px, r.High) if direction > 0 else min(extreme_px, r.Low)
            exit_now, exit_px, reason = False, None, None

            if mode in ('DIP', 'SHORT_RIP'):
                if direction > 0:
                    target_px = entry_px + cfg['dip_tp_atr'] * r.ATR
                    stop_px = entry_px - cfg['dip_sl_atr'] * r.ATR
                    if r.Low <= stop_px:
                        exit_now, exit_px, reason = True, min(stop_px, r.Close), 'dip_stop'
                    elif r.High >= target_px:
                        exit_now, exit_px, reason = True, target_px, 'dip_target'
                else:
                    target_px = entry_px - cfg['dip_tp_atr'] * r.ATR
                    stop_px = entry_px + cfg['dip_sl_atr'] * r.ATR
                    if r.High >= stop_px:
                        exit_now, exit_px, reason = True, max(stop_px, r.Close), 'rip_stop'
                    elif r.Low <= target_px:
                        exit_now, exit_px, reason = True, target_px, 'rip_target'
            else:
                m = ts if mode == 'STRONG_BULL_TREND' else tt
                if direction > 0:
                    # ── LONG trailing logic (v13) ──
                    raw_trail = extreme_px - m * r.ATR
                    if tp1_done:
                        stop_px = max(raw_trail, be_px + cfg.get('tp1_be_floor_atr', 0.0) * r.ATR)
                    else:
                        stop_px = raw_trail
                        risk_capped = (cfg.get('init_risk_atr', 0) > 0
                                       and mode in cfg.get('init_risk_modes',
                                                           ('STRONG_BULL_TREND', 'TREND')))
                        if risk_capped:
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
                            if r.High >= trigger:
                                sell_u = units * cfg['tp1_fraction']
                                fill = trigger * (1 - FEE_SLIP)
                                cash += sell_u * fill
                                trade_pnl += sell_u * (fill - entry_px)
                                units -= sell_u
                                tp1_done = True
                        ema_ref = r.EMA200 if mode == 'STRONG_BULL_TREND' else r.EMA50
                        if r.Close < ema_ref:
                            exit_now, exit_px, reason = True, r.Close, 'ema_exit'
                else:
                    # ── SHORT trailing logic (mirror) ──
                    raw_trail = extreme_px + m * r.ATR
                    if tp1_done:
                        stop_px = min(raw_trail, be_px - cfg.get('tp1_be_floor_atr', 0.0) * r.ATR)
                    else:
                        stop_px = raw_trail
                        risk_capped = cfg.get('init_risk_atr', 0) > 0
                        if risk_capped:
                            stop_px = min(stop_px, entry_px + cfg['init_risk_atr'] * r.ATR)
                    if r.High >= stop_px:
                        exit_now = True
                        exit_px = max(stop_px, r.Close)
                        if tp1_done and stop_px <= raw_trail:
                            reason = 'be_stop'
                        elif (not tp1_done) and risk_capped \
                                and stop_px <= entry_px + cfg['init_risk_atr'] * r.ATR:
                            reason = 'risk_cap'
                        else:
                            reason = 'atr_trail'
                    else:
                        if cfg['tp1_enabled'] and not tp1_done:
                            trigger = entry_px - cfg['tp1_trigger_atr'] * r.ATR
                            if r.Low <= trigger:
                                buy_u = units * cfg['tp1_fraction']
                                fill = trigger * (1 + FEE_SLIP)
                                cash -= buy_u * fill
                                trade_pnl += buy_u * (entry_px - fill)
                                units -= buy_u
                                tp1_done = True
                        ema_ref = r.EMA200                  # cover when trend reclaims EMA200
                        if r.Close > ema_ref:
                            exit_now, exit_px, reason = True, r.Close, 'ema_exit'

            if exit_now:
                close_trade(i, exit_px, reason)

        eq_val.append(cash + direction * units * r.Close)
        eq_idx.append(df.index[i])

    if in_pos and trades:
        close_trade(len(df) - 1, df.Close.iloc[-1], 'end_of_test')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[WARMUP] * df.Close.iloc[WARMUP:]
    bh.index = df.index[WARMUP:]
    return pd.DataFrame(trades), equity, bh


# ══════════════════════════════════════════════════════════════════════
# PERIOD COMPARISON:  Long/Short  vs  Long-only  vs  Buy&Hold
# ══════════════════════════════════════════════════════════════════════
def eval_window_ls(name, df_full, start_ts, end_ts=None):
    sub = slice_window(df_full, start_ts, end_ts)
    if len(sub) <= WARMUP + 100:
        return None
    eval_start = sub.index[WARMUP]
    if eval_start > start_ts + pd.Timedelta(days=7):
        return None
    ov = TRAIL_OVERRIDES_V13.get(name)
    tr_l, eq_l, _ = run_backtest(sub, V13_CFG, INITIAL_CAPITAL, ov)       # long-only
    tr_s, eq_s, bh = run_ls(sub, LS_CFG, INITIAL_CAPITAL, ov)             # long/short
    eq_l_w, eq_s_w = eq_l[eq_l.index >= eval_start], eq_s[eq_s.index >= eval_start]
    m_l, m_s = calculate_metrics(eq_l_w, tr_l, None), calculate_metrics(eq_s_w, tr_s, bh)
    if not m_l or not m_s:
        return None
    return {'Asset': name,
            'Start': eval_start.date(), 'End': sub.index[-1].date(),
            'Days': (sub.index[-1] - eval_start).days,
            'LS Ret (%)': m_s['Return (%)'],
            'Long Ret (%)': m_l['Return (%)'],
            'B&H (%)': m_s['B&H (%)'],
            'LS α vs BH': round(m_s['Return (%)'] - m_s['B&H (%)'], 2),
            'LS−Long Δpp': round(m_s['Return (%)'] - m_l['Return (%)'], 2),
            'LS MaxDD (%)': m_s['MaxDD (%)'],
            'Long MaxDD (%)': m_l['MaxDD (%)'],
            'BH_MaxDD (%)': bh_drawdown(bh),
            'LS Trades': m_s['Trades'], 'LS WR (%)': m_s['WinRate (%)'],
            'LS Sharpe': m_s['Sharpe']}

def run_fixed(dfs):
    rows = []
    for name, df in dfs.items():
        end = df.index[-1]
        wins = [(f'{mo:>2}M', end - pd.DateOffset(months=mo)) for mo in FIXED_MONTHS]
        wins.append(('Full', df.index[WARMUP]))
        for label, start in wins:
            m = eval_window_ls(name, df, start)
            if m:
                m['Window'] = label
                rows.append(m)
    out = pd.DataFrame(rows)
    w_order = [f'{mo:>2}M' for mo in FIXED_MONTHS] + ['Full']
    out['_w'] = out['Window'].map({w: i for i, w in enumerate(w_order)})
    return (out.sort_values(['_w', 'Asset']).drop(columns='_w').reset_index(drop=True))

def run_years(dfs):
    rows = []
    for name, df in dfs.items():
        for y in sorted(df.index.year.unique()):
            start = max(df.index[WARMUP], pd.Timestamp(y, 1, 1))
            end = pd.Timestamp(y + 1, 1, 1)
            if start >= end or start >= df.index[-1]:
                continue
            m = eval_window_ls(name, df, start, end)
            if m and m['Days'] >= 60:
                m['Year'] = y
                rows.append(m)
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def run_rolling(dfs):
    rows = []
    for name, df in dfs.items():
        start = df.index[WARMUP]
        while True:
            end = start + pd.DateOffset(months=ROLL_MONTHS)
            if end > df.index[-1]:
                break
            m = eval_window_ls(name, df, start, end)
            if m:
                m['WinEnd'] = end
                rows.append(m)
            start = start + pd.DateOffset(months=ROLL_STEP)
    return pd.DataFrame(rows)

def rolling_summary(roll):
    rows = []
    for name, g in roll.groupby('Asset'):
        ls_beat = (g['LS α vs BH'] > 0).sum()
        lo_beat = (g['Long Ret (%)'] - g['B&H (%)'] > 0).sum()
        ls_beats_lo = (g['LS−Long Δpp'] > 0).sum()
        rows.append({'Asset': name, 'Windows': len(g),
                     'LS beats BH': f'{ls_beat}/{len(g)}',
                     'Long beats BH': f'{lo_beat}/{len(g)}',
                     'LS beats Long': f'{ls_beats_lo}/{len(g)}',
                     'Avg LS Ret %': round(g['LS Ret (%)'].mean(), 1),
                     'Avg Long Ret %': round(g['Long Ret (%)'].mean(), 1),
                     'Avg B&H Ret %': round(g['B&H (%)'].mean(), 1),
                     'Avg LS α pp': round(g['LS α vs BH'].mean(), 1),
                     'Avg LS DD %': round(g['LS MaxDD (%)'].mean(), 1),
                     'Avg Long DD %': round(g['Long MaxDD (%)'].mean(), 1),
                     'Avg BH DD %': round(g['BH_MaxDD (%)'].mean(), 1)})
    return pd.DataFrame(rows).set_index('Asset')

# ── chart ─────────────────────────────────────────────────────────────
def tri_bars(ax, labels, series, title):
    x = np.arange(len(labels)); n = len(series); w = 0.8 / n
    colors = {'Long/Short': '#2F855A', 'Long-only': '#2B6CB0', 'Buy&Hold': '#A0AEC0'}
    for j, (lab, vals) in enumerate(series.items()):
        ax.bar(x - 0.4 + w * (j + 0.5), vals, w * 0.92, label=lab,
               color=colors.get(lab))
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.axhline(0, color='gray', lw=0.8)
    ax.set_title(title, fontweight='bold'); ax.legend(); ax.grid(alpha=0.3, axis='y')

def plot_report(fixed, years, roll, outfile='short_comparison_chart.png'):
    fig, axes = plt.subplots(3, 1, figsize=(14, 12),
                             gridspec_kw={'height_ratios': [1, 1, 1.15]})
    b = fixed[fixed.Asset == 'BTC']
    tri_bars(axes[0], list(b.Window),
             {'Long/Short': b['LS Ret (%)'].values,
              'Long-only': b['Long Ret (%)'].values,
              'Buy&Hold': b['B&H (%)'].values},
             'BTC — fixed windows: Long/Short vs Long-only vs Buy&Hold (%)')
    yb = years[years.Asset == 'BTC'] if not years.empty else pd.DataFrame()
    if not yb.empty:
        tri_bars(axes[1], [str(v) for v in yb.Year],
                 {'Long/Short': yb['LS Ret (%)'].values,
                  'Long-only': yb['Long Ret (%)'].values,
                  'Buy&Hold': yb['B&H (%)'].values},
                 'BTC — calendar-year runs: Long/Short vs Long-only vs Buy&Hold (%)')
    else:
        axes[1].text(0.5, 0.5, 'no yearly data', ha='center', va='center')
    hues = {'BTC': '#2B6CB0', 'ETH': '#D69E2E', 'SOL': '#2F855A'}
    for name, g in roll.groupby('Asset'):
        axes[2].plot(g.WinEnd, g['LS α vs BH'].values, lw=1.9, color=hues.get(name),
                     label=f'{name} L/S')
        axes[2].plot(g.WinEnd, (g['Long Ret (%)'] - g['B&H (%)']).values, lw=1.1,
                     ls='--', alpha=0.55, color=hues.get(name), label=f'{name} long-only')
    axes[2].axhline(0, color='gray', lw=0.9, ls=':')
    axes[2].set_title(f'Rolling {ROLL_MONTHS}M alpha vs Buy&Hold (pp) — L/S solid, long-only dashed',
                      fontweight='bold')
    axes[2].legend(ncol=3, fontsize=8); axes[2].grid(alpha=0.3, linestyle='--')
    fig.tight_layout(); fig.savefig(outfile, dpi=150, bbox_inches='tight'); plt.close(fig)
    print(f"[CHART] saved -> {outfile}")

# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print('=' * 78)
    print('🐻 LONG/SHORT — adding SHORTs in BEAR regimes | L/S vs Long-only vs B&H')
    print('=' * 78)
    dfs = load_all_ls()
    for name, df in dfs.items():
        pct = (df.Regime == 'BEAR').mean() * 100
        print(f"   {name}: BEAR regime on {pct:.1f}% of bars")

    fixed = run_fixed(dfs)
    print('\n' + '=' * 78)
    print('1️⃣  FIXED WINDOWS — fresh $1,000 each')
    print('=' * 78)
    fshow = fixed.copy()
    fshow['Start'] = fshow['Start'].astype(str); fshow['End'] = fshow['End'].astype(str)
    print(fshow.to_string(index=False))

    print('\n--- LS alpha vs B&H (pp) ---')
    w_order = [f'{mo:>2}M' for mo in FIXED_MONTHS] + ['Full']
    print(fixed.pivot_table(index='Window', columns='Asset',
                            values='LS α vs BH').reindex(w_order).to_string())
    print('\n--- LS minus Long-only (pp; + = shorts added value) ---')
    print(fixed.pivot_table(index='Window', columns='Asset',
                            values='LS−Long Δpp').reindex(w_order).to_string())

    years = run_years(dfs)
    print('\n' + '=' * 78)
    print('2️⃣  CALENDAR-YEAR RUNS — L/S vs Long-only vs B&H')
    print('=' * 78)
    if years.empty:
        print('  (no data)')
    else:
        yshow = years.copy()
        yshow['Start'] = yshow['Start'].astype(str)
        yshow['End'] = yshow['End'].astype(str)
        print(yshow.to_string(index=False))

    roll = run_rolling(dfs)
    summ = rolling_summary(roll)
    print('\n' + '=' * 78)
    print(f'3️⃣  ROLLING {ROLL_MONTHS}M WINDOWS SUMMARY')
    print('=' * 78)
    print(summ.to_string())
    print('\n--- Best 5 rolling windows for L/S (by LS−Long delta) ---')
    top = roll.nlargest(5, 'LS−Long Δpp')
    tshow = top[['Asset', 'Start', 'End', 'LS Ret (%)', 'Long Ret (%)',
                 'B&H (%)', 'LS−Long Δpp', 'LS MaxDD (%)']].copy()
    tshow['Start'] = tshow['Start'].astype(str); tshow['End'] = tshow['End'].astype(str)
    print(tshow.to_string(index=False))

    print('\n' + '=' * 78)
    print('🧠 VERDICT')
    print('=' * 78)
    for name, g in fixed.groupby('Asset'):
        add = (g['LS−Long Δpp'] > 0).sum()
        print(f"  • {name}: shorts improved Long-only in {add}/{len(g)} fixed windows | "
              f"LS avg DD {g['LS MaxDD (%)'].mean():.1f}% vs Long {g['Long MaxDD (%)'].mean():.1f}%")
    if not years.empty:
        for name, g in years.groupby('Asset'):
            add = (g['LS−Long Δpp'] > 0).sum()
            print(f"  • {name}: shorts helped in {add}/{len(g)} calendar years")
    print()

    os.makedirs('data', exist_ok=True)
    fixed.to_csv('data/ls_fixed.csv', index=False)
    years.to_csv('data/ls_years.csv', index=False)
    roll.to_csv('data/ls_rolling12m.csv', index=False)
    summ.to_csv('data/ls_rolling_summary.csv')
    ls_trades = {}
    for name, df in dfs.items():
        tr, _, _ = run_ls(df, LS_CFG, INITIAL_CAPITAL, TRAIL_OVERRIDES_V13.get(name))
        ls_trades[name] = tr.assign(asset=name)
    pd.concat(ls_trades.values(), ignore_index=True).to_csv('data/ls_trades_full.csv', index=False)
    print('[SAVED] data/ls_fixed.csv | data/ls_years.csv | data/ls_rolling12m.csv | '
          'data/ls_rolling_summary.csv | data/ls_trades_full.csv')

    plot_report(fixed, years, roll)
