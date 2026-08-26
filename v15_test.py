"""
V15 EXPERIMENT — can better regime-riding beat B&H in bull windows?
====================================================================
User hypothesis: "identify the market regime, ride breakouts, cut losses,
avoid chop -> always beat Buy&Hold."

v14 already does all of that. The residual leak in bull windows:
  L1: after a stop-out we wait for a NEW Donchian high to re-enter
      -> miss the middle of the move.
  L2: the ATR trail ejects us on normal corrections inside STRONG_BULL.

Variants tested here (all measured vs B&H on identical windows):
  BASE  = v14 selected (P2)
  R1    = + fast EMA20-reclaim re-entry after stop-outs
  R2    = + STRONG_BULL rides only the wide catastrophic trail
  R3    = both

Usage: .venv/bin/python v15_test.py
"""
import numpy as np
import pandas as pd

from v14 import (load_real_data, add_indicators, run_backtest,
                 make_cfg, TRAIL_OVERRIDES_V14, WARMUP)

FILES = {'BTC': 'BTC_USD_4h.csv',
         'ETH': 'ETH_USD_4h.csv',
         'SOL': 'SOL_USD_4h.csv'}
ROLL_MONTHS, ROLL_STEP = 12, 3

VARIANTS = {
    'BASE':        make_cfg(adaptive_trail=False, pyramid_enabled=True),
    'R1_reentry':  make_cfg(adaptive_trail=False, pyramid_enabled=True,
                            reentry_ema20=True),
    'R2_widestop': make_cfg(adaptive_trail=False, pyramid_enabled=True,
                            strong_wide_stop=True),
    'R3_both':     make_cfg(adaptive_trail=False, pyramid_enabled=True,
                            reentry_ema20=True, strong_wide_stop=True),
}

def load_all():
    return {n: add_indicators(load_real_data(f)) for n, f in FILES.items()}

def slice_window(df, start_ts, end_ts=None):
    pos = int(df.index.searchsorted(start_ts))
    s = max(0, pos - WARMUP)
    e = len(df) if end_ts is None else int(df.index.searchsorted(end_ts, side='right'))
    return df.iloc[s:e]

def ret(series):
    return round((series.iloc[-1] / series.iloc[0] - 1) * 100, 2)

def maxdd(series):
    return round(float(((series - series.cummax()) / series.cummax()).min() * 100), 2)

def eval_window(name, df_full, start_ts, end_ts=None):
    sub = slice_window(df_full, start_ts, end_ts)
    if len(sub) <= WARMUP + 100:
        return None
    eval_start = sub.index[WARMUP]
    if eval_start > start_ts + pd.Timedelta(days=7):
        return None
    row = {'Asset': name, 'Start': eval_start.date(),
           'End': sub.index[-1].date()}
    bh_ret = None
    for vname, cfg in VARIANTS.items():
        _, eq, bh = run_backtest(sub, cfg, 1000.0, TRAIL_OVERRIDES_V14.get(name))
        eq_w = eq[eq.index >= eval_start]
        row[f'{vname} (%)'] = ret(eq_w)
        row[f'{vname} DD'] = maxdd(eq_w)
        if bh_ret is None:
            bh_ret = ret(bh[bh.index >= eval_start])
            row['B&H (%)'] = bh_ret
            row['BH DD'] = maxdd(bh[bh.index >= eval_start])
        row[f'{vname} beats'] = int(ret(eq_w) > bh_ret)
    return row

def run_years(dfs):
    rows = []
    for name, df in dfs.items():
        for y in sorted(df.index.year.unique()):
            start = max(df.index[WARMUP], pd.Timestamp(y, 1, 1))
            end = pd.Timestamp(y + 1, 1, 1)
            if start >= end or start >= df.index[-1]:
                continue
            m = eval_window(name, df, start, end)
            if m and m['End'] and (pd.Timestamp(m['End']) - pd.Timestamp(m['Start'])).days >= 60:
                m['Year'] = y
                rows.append(m)
    return pd.DataFrame(rows)

def run_rolling(dfs):
    rows = []
    for name, df in dfs.items():
        start = df.index[WARMUP]
        while True:
            end = start + pd.DateOffset(months=ROLL_MONTHS)
            if end > df.index[-1]:
                break
            m = eval_window(name, df, start, end)
            if m:
                rows.append(m)
            start = start + pd.DateOffset(months=ROLL_STEP)
    return pd.DataFrame(rows)

if __name__ == '__main__':
    print('=' * 78)
    print('🧪 V15 TEST — regime-riding variants vs Buy&Hold')
    print('=' * 78)
    dfs = load_all()

    years = run_years(dfs)
    roll = run_rolling(dfs)

    print('\n--- CALENDAR YEARS ---')
    yshow = years.copy()
    yshow['Start'] = yshow['Start'].astype(str); yshow['End'] = yshow['End'].astype(str)
    cols = ['Asset', 'Year', 'B&H (%)'] + \
           [c for v in VARIANTS for c in (f'{v} (%)', f'{v} DD', f'{v} beats')]
    print(yshow[cols].to_string(index=False))

    print('\n--- SCORECARD ---')
    rows = []
    for label, dfx in (('Years', years), ('Rolling12M', roll)):
        for name, g in dfx.groupby('Asset'):
            r = {'Scope': label, 'Asset': name, 'Windows': len(g),
                 'BH avg %': round(g['B&H (%)'].mean(), 1)}
            for v in VARIANTS:
                wins = int(g[f'{v} beats'].sum())
                r[f'{v} wins'] = f'{wins}/{len(g)}'
                r[f'{v} avg%'] = round(g[f'{v} (%)'].mean(), 1)
                r[f'{v} avgDD'] = round(g[f'{v} DD'].mean(), 1)
            rows.append(r)
    sc = pd.DataFrame(rows)
    print(sc.to_string(index=False))

    # full-period check too
    print('\n--- FULL PERIOD ---')
    frows = []
    for name, df in dfs.items():
        r = {'Asset': name}
        for vname, cfg in VARIANTS.items():
            _, eq, bh = run_backtest(df, cfg, 1000.0, TRAIL_OVERRIDES_V14.get(name))
            r[f'{vname} (%)'] = ret(eq)
            r[f'{vname} DD'] = maxdd(eq)
        r['B&H (%)'] = ret(bh); r['BH DD'] = maxdd(bh)
        frows.append(r)
    print(pd.DataFrame(frows).set_index('Asset').to_string())

    # ── PER-ASSET SELECTED PORTFOLIO (by full-period Calmar) ──
    print('\n--- PER-ASSET SELECTED PORTFOLIO (50/30/20) ---')
    print('  BTC -> R3_both | ETH -> R2_widestop | SOL -> BASE   '
          '(selected by full-period Calmar)')
    SELECTED = {
        'BTC': VARIANTS['R3_both'],
        'ETH': VARIANTS['R2_widestop'],
        'SOL': VARIANTS['BASE'],
    }
    weights = {'BTC': .5, 'ETH': .3, 'SOL': .2}
    eqs = {}
    for name, df in dfs.items():
        _, eq, _ = run_backtest(df, SELECTED[name], 1000 * weights[name],
                                TRAIL_OVERRIDES_V14.get(name))
        eqs[name] = eq.rename(name)
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for n in dfs:
        comb[n] = comb[n].fillna(1000 * weights[n])
    port = comb.sum(axis=1)
    rets_p = port.pct_change().dropna()
    dd_p = ((port - port.cummax()) / port.cummax()).min()
    yrs = len(port) / 2190
    print(f"  Final  : ${port.iloc[-1]:,.0f}")
    print(f"  Return : {(port.iloc[-1]/1000-1)*100:,.1f}%")
    print(f"  CAGR   : {((port.iloc[-1]/1000)**(1/yrs)-1)*100:.1f}%")
    print(f"  MaxDD  : {dd_p*100:.1f}%")
    print(f"  Sharpe : {rets_p.mean()/rets_p.std()*np.sqrt(2190):.2f}")
    d = port.to_frame('Eq'); d['Y'] = d.index.year
    for y, g in d.groupby('Y'):
        gdd = ((g.Eq - g.Eq.cummax()) / g.Eq.cummax()).min() * 100
        print(f"  {y}: {(g.Eq.iloc[-1]/g.Eq.iloc[0]-1)*100:+7.1f}%  DD {gdd:6.1f}%")
    print('  (caveat: per-asset selection is in-sample; validate live)')
    port.rename('PortfolioEquity').to_csv('data/v15_portfolio_equity.csv')

    years.to_csv('data/v15_years.csv', index=False)
    roll.to_csv('data/v15_rolling.csv', index=False)
    sc.to_csv('data/v15_scorecard.csv', index=False)
    print('\n[SAVED] data/v15_*.csv')
