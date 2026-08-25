"""
PERIOD ROBUSTNESS — Strategy vs Buy&Hold across time windows
============================================================
Runs the v13 engine (plus the v9 baseline for reference) over:
  1. Fixed lookback windows ending at the last candle (3M/6M/12M/18M/24M/36M/Full)
  2. Independent calendar-year runs (fresh $1,000 each)
  3. Rolling 12-month windows (step: 3 months)
and compares EVERY window against plain Buy&Hold on the exact same window.

Usage:   python3 period_compare.py
Outputs: console tables + data/period_*.csv + period_comparison_chart.png
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from main import (load_real_data, add_indicators, run_backtest,
                  calculate_metrics, V13_CFG, BASELINE_CFG,
                  TRAIL_OVERRIDES_V13, WARMUP)

INITIAL_CAPITAL = 1000.0
FILES = {'BTC': 'BTC_USD_4h.csv',
         'ETH': 'ETH_USD_4h.csv',
         'SOL': 'SOL_USD_4h.csv'}
FIXED_MONTHS = [3, 6, 12, 18, 24, 36]
ROLL_MONTHS, ROLL_STEP = 12, 3

METRIC_COLS = ['Return (%)', 'B&H (%)', 'Alpha vs B&H',
               'MaxDD (%)', 'BH_MaxDD (%)', 'Sharpe',
               'Trades', 'WinRate (%)']

# ── helpers ───────────────────────────────────────────────────────────
def load_all():
    dfs = {}
    for name, f in FILES.items():
        dfs[name] = add_indicators(load_real_data(f), vol_q=V13_CFG['vol_q'])
    return dfs

def slice_window(df, start_ts, end_ts=None):
    """Slice with WARMUP bars of context BEFORE the evaluation start."""
    pos = int(df.index.searchsorted(start_ts))
    s = max(0, pos - WARMUP)
    e = len(df) if end_ts is None else int(df.index.searchsorted(end_ts, side='right'))
    return df.iloc[s:e]

def bh_drawdown(bh):
    dd = (bh - bh.cummax()) / bh.cummax()
    return round(float(dd.min() * 100), 2)

def eval_window(name, df_full, start_ts, end_ts=None, cfg=V13_CFG):
    """Fresh-capital run on ONE window -> metric row (None if window invalid)."""
    sub = slice_window(df_full, start_ts, end_ts)
    if len(sub) <= WARMUP + 100:
        return None
    eval_start = sub.index[WARMUP]
    if eval_start > start_ts + pd.Timedelta(days=7):   # requested start predates data
        return None
    tr, eq, bh = run_backtest(sub, cfg, INITIAL_CAPITAL,
                              TRAIL_OVERRIDES_V13.get(name))
    eq_w = eq[eq.index >= eval_start]                  # trim warmup region
    m = calculate_metrics(eq_w, tr, bh)
    if not m:
        return None
    m.update({'Asset': name,
              'Start': eval_start.date(), 'End': sub.index[-1].date(),
              'Days': (sub.index[-1] - eval_start).days,
              'BH_MaxDD (%)': bh_drawdown(bh)})
    return m

# ── 1. FIXED LOOKBACK WINDOWS ────────────────────────────────────────
def run_fixed(dfs):
    rows = []
    for name, df in dfs.items():
        end = df.index[-1]
        wins = [(f'{mo:>2}M', end - pd.DateOffset(months=mo)) for mo in FIXED_MONTHS]
        wins.append(('Full', df.index[WARMUP]))
        for label, start in wins:
            for tag, cfg in (('V13', V13_CFG), ('v9', BASELINE_CFG)):
                m = eval_window(name, df, start, None, cfg)
                if m:
                    m['Window'], m['Cfg'] = label, tag
                    rows.append(m)
    out = pd.DataFrame(rows)
    w_order = [f'{mo:>2}M' for mo in FIXED_MONTHS] + ['Full']
    out['_w'] = out['Window'].map({w: i for i, w in enumerate(w_order)})
    out = (out.sort_values(['_w', 'Asset', 'Cfg'])
              .drop(columns='_w')
              .reset_index(drop=True))
    return out[['Asset', 'Window', 'Cfg', 'Start', 'End', 'Days'] + METRIC_COLS]

# ── 2. CALENDAR-YEAR INDEPENDENT RUNS ────────────────────────────────
def run_calendar_years(dfs):
    rows = []
    for name, df in dfs.items():
        for y in sorted(df.index.year.unique()):
            start = max(df.index[WARMUP], pd.Timestamp(y, 1, 1))
            end = pd.Timestamp(y + 1, 1, 1)
            if start >= end or start >= df.index[-1]:
                continue
            m = eval_window(name, df, start, end, V13_CFG)
            if m and m['Days'] >= 60:
                m['Year'] = y
                rows.append(m)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out[['Asset', 'Year', 'Start', 'End'] + METRIC_COLS]

# ── 3. ROLLING 12-MONTH WINDOWS ──────────────────────────────────────
def run_rolling(dfs, months=ROLL_MONTHS, step=ROLL_STEP):
    rows = []
    for name, df in dfs.items():
        start = df.index[WARMUP]
        end_all = df.index[-1]
        while True:
            end = start + pd.DateOffset(months=months)
            if end > end_all:
                break
            m = eval_window(name, df, start, end, V13_CFG)
            if m:
                m['WinEnd'] = end
                rows.append(m)
            start = start + pd.DateOffset(months=step)
    return pd.DataFrame(rows)

def rolling_summary(roll_df):
    rows = []
    for name, g in roll_df.groupby('Asset'):
        beat = (g['Alpha vs B&H'] > 0).sum()
        rows.append({
            'Asset': name, 'Windows': len(g),
            'Beat B&H': f'{beat}/{len(g)}',
            'Beat %': round(beat / len(g) * 100, 1),
            'Avg StratRet %': round(g['Return (%)'].mean(), 1),
            'Avg B&H Ret %': round(g['B&H (%)'].mean(), 1),
            'Avg Alpha pp': round(g['Alpha vs B&H'].mean(), 1),
            'Median Alpha pp': round(g['Alpha vs B&H'].median(), 1),
            'Worst Alpha pp': round(g['Alpha vs B&H'].min(), 1),
            'Avg StratDD %': round(g['MaxDD (%)'].mean(), 1),
            'Avg BH_DD %': round(g['BH_MaxDD (%)'].mean(), 1),
        })
    return pd.DataFrame(rows).set_index('Asset')

# ── CHART ────────────────────────────────────────────────────────────
def grouped_bars(ax, labels, a, b, la, lb, title):
    x = np.arange(len(labels)); w = 0.38
    ax.bar(x - w / 2, a, w, label=la, color='#2B6CB0')
    ax.bar(x + w / 2, b, w, label=lb, color='#A0AEC0')
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.axhline(0, color='gray', lw=0.8)
    ax.set_title(title, fontweight='bold')
    ax.legend(); ax.grid(alpha=0.3, axis='y')

def plot_report(fixed_df, years_df, roll_df, outfile='period_comparison_chart.png'):
    fig, axes = plt.subplots(3, 1, figsize=(14, 12),
                             gridspec_kw={'height_ratios': [1, 1, 1.15]})
    btc_f = fixed_df[(fixed_df.Asset == 'BTC') & (fixed_df.Cfg == 'V13')]
    grouped_bars(axes[0], list(btc_f.Window), btc_f['Return (%)'].values,
                 btc_f['B&H (%)'].values, 'Strategy (V13)', 'Buy&Hold',
                 'BTC — fixed windows ending at latest candle: Strategy vs Buy&Hold (%)')

    btc_y = years_df[years_df.Asset == 'BTC'] if not years_df.empty else pd.DataFrame()
    if not btc_y.empty:
        grouped_bars(axes[1], [str(y) for y in btc_y.Year], btc_y['Return (%)'].values,
                     btc_y['B&H (%)'].values, 'Strategy (V13)', 'Buy&Hold',
                     'BTC — independent calendar-year runs: Strategy vs Buy&Hold (%)')
    else:
        axes[1].text(0.5, 0.5, 'no yearly data', ha='center', va='center')

    for name, g in roll_df.groupby('Asset'):
        axes[2].plot(g.WinEnd, g['Alpha vs B&H'].values, lw=1.8, label=name)
    axes[2].axhline(0, color='gray', lw=0.9, ls='--')
    axes[2].set_title(f'Rolling {ROLL_MONTHS}M windows — Alpha vs Buy&Hold (pp)',
                      fontweight='bold')
    axes[2].legend(); axes[2].grid(alpha=0.3, linestyle='--')

    fig.tight_layout()
    fig.savefig(outfile, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"[CHART] saved -> {outfile}")

# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print('=' * 78)
    print('📊 PERIOD ROBUSTNESS — Strategy (V13) vs Buy&Hold, multiple windows')
    print('=' * 78)
    dfs = load_all()

    # 1 ── fixed windows -------------------------------------------------
    fixed = run_fixed(dfs)
    print('\n' + '=' * 78)
    print('1️⃣  FIXED WINDOWS (ending at latest candle) — fresh $1,000 each')
    print('=' * 78)
    show = fixed.copy()
    show['Start'] = show['Start'].astype(str); show['End'] = show['End'].astype(str)
    print(show.to_string(index=False))

    print('\n--- Alpha vs B&H (pp) — V13 only ---')
    piv = (fixed[fixed.Cfg == 'V13']
           .pivot_table(index='Window', columns='Asset', values='Alpha vs B&H'))
    w_order = [f'{mo:>2}M' for mo in FIXED_MONTHS] + ['Full']
    print(piv.reindex(w_order).to_string())

    # 2 ── calendar years --------------------------------------------------
    years = run_calendar_years(dfs)
    print('\n' + '=' * 78)
    print('2️⃣  CALENDAR-YEAR RUNS (fresh $1,000 per year) — V13 vs B&H')
    print('=' * 78)
    if not years.empty:
        yshow = years.copy()
        yshow['Start'] = yshow['Start'].astype(str)
        yshow['End'] = yshow['End'].astype(str)
        print(yshow.to_string(index=False))
    else:
        print('  (no data)')

    # 3 ── rolling 12M -----------------------------------------------------
    roll = run_rolling(dfs)
    summ = rolling_summary(roll)
    print('\n' + '=' * 78)
    print(f'3️⃣  ROLLING {ROLL_MONTHS}M WINDOWS (step {ROLL_STEP}M) — SUMMARY (V13)')
    print('=' * 78)
    print(summ.to_string())
    print('\n--- Worst rolling windows (bottom 10 by alpha) ---')
    worst = roll.nsmallest(10, 'Alpha vs B&H')
    wshow = worst[['Asset', 'Start', 'End', 'Return (%)', 'B&H (%)',
                   'Alpha vs B&H', 'MaxDD (%)', 'BH_MaxDD (%)']].copy()
    wshow['Start'] = wshow['Start'].astype(str); wshow['End'] = wshow['End'].astype(str)
    print(wshow.to_string(index=False))

    # ── verdict -----------------------------------------------------------
    print('\n' + '=' * 78)
    print('🧠 VERDICT')
    print('=' * 78)
    for name, g in fixed[fixed.Cfg == 'V13'].groupby('Asset'):
        beat = (g['Alpha vs B&H'] > 0).sum()
        print(f"  • {name}: beat B&H in {beat}/{len(g)} fixed windows | "
              f"avg alpha {g['Alpha vs B&H'].mean():+.1f} pp | "
              f"avg DD {g['MaxDD (%)'].mean():.1f}% vs B&H {g['BH_MaxDD (%)'].mean():.1f}%")
    if not years.empty:
        for name, g in years.groupby('Asset'):
            beat = (g['Alpha vs B&H'] > 0).sum()
            print(f"  • {name}: beat B&H in {beat}/{len(g)} calendar years")
    print()

    # ── save artifacts ────────────────────────────────────────────────
    os.makedirs('data', exist_ok=True)
    fixed.to_csv('data/period_fixed.csv', index=False)
    years.to_csv('data/period_years.csv', index=False)
    roll.to_csv('data/period_rolling12m.csv', index=False)
    summ.to_csv('data/period_rolling_summary.csv')
    print('[SAVED] data/period_fixed.csv | data/period_years.csv | '
          'data/period_rolling12m.csv | data/period_rolling_summary.csv')

    plot_report(fixed, years, roll)
