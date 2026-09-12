"""
DEBUG: מנתח circuit breaker trips ו-ladder activation בתוך engine.run_dynamic_adaptive_engine
גישה: Monkey-patch את הלוגיקה הפנימית כדי לעקוב אחרי כל יום
"""
import os, sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/uns/TRADE/BACK_TEST')
import engine

CAPITAL = 2000.0

# ─────────────────────────────────────────────────────────────────────
# נגדיר גרסת debug שמשכפלת את הלוגיקה הפנימית של run_dynamic_adaptive_engine
# ─────────────────────────────────────────────────────────────────────
def run_debug_engine(ladder_steps=(1.0, 2.0, 4.0, 10.0, 20.0), initial_capital=CAPITAL):
    weights           = engine.DEFAULT_WEIGHTS
    momentum_cutoff_pct = None
    bull_leverage     = 3.5
    mid_leverage      = 2.4
    min_leverage      = 1.4
    conviction_leverage = bull_leverage
    base_leverage     = min_leverage
    safe_cash_weight  = 0.60
    safe_spot_weight  = 0.30
    safe_micro_weight = 0.10
    cash_apr          = 0.04
    flash_wick_limit  = -0.038
    clamp_binance_brackets = False

    # ── נתונים (כמו engine) ──
    hybrid_base, _, _ = engine.run_rebalanced_hybrid_engine(
        initial_capital=initial_capital, core_ratio=0.80, weights=weights)

    bhs = {}
    for name, f in engine.FILES.items():
        df   = engine.add_indicators(engine.load_real_data(f))
        cfg  = engine.BEST_CFGS.get(name, engine.BEST_CFGS['BTC'])
        trail = (7.5, 5.0) if name == 'SOL' else engine.TRAIL_OVERRIDES_V14.get(name)
        _, _, bh = engine.run_backtest(df, cfg, initial_capital * weights[name], trail, fee_side=0.00125)
        bhs[name] = bh.rename(name)
    bh_comb = pd.concat(bhs.values(), axis=1).ffill().sum(axis=1)

    btc_df         = engine.load_real_data(engine.FILES['BTC'])
    btc_daily      = btc_df['Close'].resample('D').last().dropna()
    btc_high_daily = btc_df['High'].resample('D').max().dropna()
    btc_low_daily  = btc_df['Low'].resample('D').min().dropna()
    btc_open_daily = btc_df['Open'].resample('D').first().dropna()

    ema9_daily  = btc_daily.ewm(span=9,  adjust=False).mean()
    ema20_daily = btc_daily.ewm(span=20, adjust=False).mean()
    ema50_daily = btc_daily.ewm(span=50, adjust=False).mean()
    sma150      = btc_daily.rolling(150).mean()

    btc_5d_high    = btc_high_daily.rolling(5).max()
    btc_pb_from_5d = (btc_daily - btc_5d_high) / btc_5d_high

    tr1    = btc_high_daily - btc_low_daily
    tr2    = (btc_high_daily - btc_daily.shift(1)).abs()
    tr3    = (btc_low_daily  - btc_daily.shift(1)).abs()
    tr     = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14  = tr.rolling(14).mean()
    atr_pct = atr14 / btc_daily
    intraday_max_dip = (btc_low_daily - btc_open_daily) / btc_open_daily

    high_diff = btc_high_daily.diff()
    low_diff  = -btc_low_daily.diff()
    pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
    atr_safe = atr14.replace(0, np.nan)
    pos_di = (100 * pd.Series(pos_dm, index=btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
    neg_di = (100 * pd.Series(neg_dm, index=btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
    denom  = (pos_di + neg_di).replace(0, np.nan)
    dx     = (100 * (pos_di - neg_di).abs() / denom).fillna(0.0)
    adx_daily = dx.ewm(alpha=1/14, min_periods=14).mean()

    is_bull = (btc_daily > sma150).fillna(False)

    common_idx = hybrid_base.index.intersection(bh_comb.index).intersection(is_bull.index)
    for s in [ema9_daily, ema20_daily, atr_pct, adx_daily, btc_pb_from_5d, intraday_max_dip]:
        common_idx = common_idx.intersection(s.index)
    common_idx = common_idx.sort_values()

    hy_aligned           = hybrid_base.loc[common_idx]
    bh_aligned           = bh_comb.loc[common_idx]
    is_bull_aligned      = is_bull.loc[common_idx]
    btc_daily_aligned    = btc_daily.loc[common_idx]
    ema20_aligned        = ema20_daily.loc[common_idx]
    atr_pct_aligned      = atr_pct.loc[common_idx]
    pb_5d_aligned        = btc_pb_from_5d.loc[common_idx]
    intraday_dip_aligned = intraday_max_dip.loc[common_idx]

    cap    = initial_capital
    vals   = [cap]
    daily_cash_yield = (1.0 + cash_apr)**(1.0/365.25) - 1.0
    bull_peak = bh_aligned.iloc[0]
    bars_since_circuit_trip = 999

    cb_events     = []   # כל circuit breaker trip
    ladder_events = []   # כל יום שהסולם פועל (גם אם לא cap-ים)
    ladder_capped = []   # רק ימים שהסולם הוריד בפועל את הlev
    daily_lev     = []   # כל יום: lev שנבחר

    for i in range(1, len(common_idx)):
        d_prev = common_idx[i-1]
        d_curr = common_idx[i]

        r_hy = hy_aligned.loc[d_curr] / hy_aligned.loc[d_prev]
        r_bh = bh_aligned.loc[d_curr] / bh_aligned.loc[d_prev]

        bull = is_bull_aligned.loc[d_prev]
        if bull:
            if bh_aligned.loc[d_prev] > bull_peak:
                bull_peak = bh_aligned.loc[d_prev]

            atr_p        = atr_pct_aligned.loc[d_prev] if pd.notna(atr_pct_aligned.loc[d_prev]) else 0.035
            close_prev   = btc_daily_aligned.loc[d_prev]
            bh_pullback  = (bh_aligned.loc[d_prev] - bull_peak) / bull_peak if bull_peak > 0 else 0.0
            under_ema    = close_prev < ema20_aligned.loc[d_prev]
            intraday_dip = intraday_dip_aligned.loc[d_curr] if pd.notna(intraday_dip_aligned.loc[d_curr]) else 0.0

            # Select leverage (Legacy mode)
            if bh_pullback < -0.08 or under_ema:
                selected_lev = 1.0
            elif bh_pullback < -0.04:
                selected_lev = min_leverage
            elif atr_p < 0.024:
                selected_lev = bull_leverage
            elif atr_p < 0.036:
                selected_lev = mid_leverage
            else:
                selected_lev = min_leverage

            lev_before_ladder = selected_lev
            in_ladder_window = (ladder_steps is not None and
                                1 <= bars_since_circuit_trip <= len(ladder_steps))
            if in_ladder_window:
                ladder_cap = ladder_steps[bars_since_circuit_trip - 1]
                ladder_events.append({'date': d_curr,
                                      'bar': bars_since_circuit_trip,
                                      'cap': ladder_cap,
                                      'lev_before': lev_before_ladder})
                if lev_before_ladder > ladder_cap:
                    selected_lev = ladder_cap
                    ladder_capped.append({'date': d_curr,
                                          'bar': bars_since_circuit_trip,
                                          'cap': ladder_cap,
                                          'lev_orig': lev_before_ladder,
                                          'lev_final': selected_lev,
                                          'cap_$': round(cap, 2)})

            daily_lev.append({'date': d_curr, 'lev': selected_lev, 'bull': True,
                               'bars_trip': bars_since_circuit_trip})

            # Flash CB
            if selected_lev > 1.0 and intraday_dip < flash_wick_limit:
                cb_events.append({'date': d_curr,
                                  'dip%': round(intraday_dip*100, 3),
                                  'limit%': round(flash_wick_limit*100, 1),
                                  'lev': selected_lev,
                                  'cap_$': round(cap, 2),
                                  'prev_bars_since': bars_since_circuit_trip})
                bars_since_circuit_trip = 1
                excess   = min(0.0, (r_bh - 1.0) - flash_wick_limit)
                r_bh_lev = 1.0 + (flash_wick_limit * selected_lev) + excess
            else:
                bars_since_circuit_trip += 1
                r_bh_lev = 1.0 + (r_bh - 1.0) * selected_lev

            daily_funding_cost = 0.0003 * max(0.0, selected_lev - 1.0)
            r_bh_lev -= daily_funding_cost
            port_r = 0.70 * r_bh_lev + 0.30 * r_hy
        else:
            bars_since_circuit_trip += 1
            port_r = r_bh
            daily_lev.append({'date': d_curr, 'lev': 0, 'bull': False,
                               'bars_trip': bars_since_circuit_trip})

        cap = max(0.0, cap * port_r)
        vals.append(cap)

    dyn_eq = pd.Series(vals, index=common_idx)
    return dyn_eq, cb_events, ladder_events, ladder_capped, daily_lev

# ── הרצה ──
print("=" * 100)
print("🔍 DEBUG: Circuit Breaker Trips & Ladder Activation (ladder=[1,2,4,10,20])")
print("=" * 100)

eq_a, cb_a, lev_wins_a, lev_capped_a, daily_a = run_debug_engine((1.0, 2.0, 4.0, 10.0, 20.0))
print(f"\n📌 תוצאה: ${eq_a.iloc[-1]:,.2f} ({(eq_a.iloc[-1]/CAPITAL-1)*100:+.1f}%)")
print(f"\n⚡ Circuit Breaker Trips: {len(cb_a)}")
if cb_a:
    print(pd.DataFrame(cb_a).to_string(index=False))
else:
    print("   ⚠️  אפס! הflaash CB לא הופעל מעולם בתקופה זו")

print(f"\n🪜 ימים בחלון הסולם (bars_since_trip ≤ {5}): {len(lev_wins_a)}")
if lev_wins_a:
    df_wins = pd.DataFrame(lev_wins_a)
    print(df_wins.to_string(index=False))

print(f"\n🛑 ימים שהסולם cap-ד את ה-lev בפועל: {len(lev_capped_a)}")
if lev_capped_a:
    print(pd.DataFrame(lev_capped_a).to_string(index=False))
else:
    print("   ✅ הסולם תמיד ≥ selected_lev — אין ימים שנחסמו")

print(f"\n{'='*100}")
print("📊 התפלגות selected_lev (ימי bull):")
df_daily = pd.DataFrame(daily_a)
bull_days = df_daily[df_daily['bull']]
print(bull_days['lev'].value_counts().sort_index())

print(f"\nימי bull בתוך חלון הסולם (bars_since_trip 1-5):")
in_window = bull_days[bull_days['bars_trip'].between(1, 5)]
print(f"  סה\"כ {len(in_window)} ימים | selected_lev:\n{in_window['lev'].value_counts().sort_index()}")
