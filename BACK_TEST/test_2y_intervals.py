import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/uns/TRADE/BACK_TEST')
import engine

print("=" * 100)
print("🚀 BACK_TEST: 2-YEAR MULTI-INTERVAL DEEP AUDIT & BUY & HOLD BENCHMARK COMPARISON")
print("   Initial Capital per interval: $2,000.00 | Realistic Compounding & Zero-Floor Bankruptcy Guard")
print("=" * 100)

# Run full dynamic adaptive production engine (with Re-Entry Ladder & Flash Defense)
dyn_eq, hy_base, bh_comb = engine.run_dynamic_adaptive_engine(initial_capital=2000.0)

# Asset raw prices for individual B&H
dfs = {name: engine.load_real_data(engine.FILES[name]) for name in ['BTC', 'ETH', 'SOL']}
btc_p = dfs['BTC']['Close'].resample('D').last().dropna()
eth_p = dfs['ETH']['Close'].resample('D').last().dropna()
sol_p = dfs['SOL']['Close'].resample('D').last().dropna()

end_dt = dyn_eq.index[-1]
start_2y = end_dt - pd.DateOffset(years=2)

def evaluate_interval(name, start_date, end_date, initial_cap=2000.0):
    st = pd.to_datetime(start_date)
    en = pd.to_datetime(end_date)
    
    sub_eq = dyn_eq.loc[(dyn_eq.index >= st) & (dyn_eq.index <= en)]
    sub_bh = bh_comb.loc[(bh_comb.index >= st) & (bh_comb.index <= en)]
    sub_btc = btc_p.loc[(btc_p.index >= st) & (btc_p.index <= en)]
    sub_eth = eth_p.loc[(eth_p.index >= st) & (eth_p.index <= en)]
    sub_sol = sol_p.loc[(sol_p.index >= st) & (sol_p.index <= en)]
    
    if len(sub_eq) < 2:
        return None
        
    # Strategy step-by-step compounding with zero-floor enforcement
    strat_cap = initial_cap
    strat_cap_series = [strat_cap]
    strat_rets = sub_eq.pct_change().dropna()
    for r in strat_rets:
        if strat_cap <= 0.0:
            strat_cap = 0.0
        else:
            strat_cap = max(0.0, strat_cap * (1.0 + r))
        strat_cap_series.append(strat_cap)
    eq_s = pd.Series(strat_cap_series, index=sub_eq.index)
    
    # B&H Basket step-by-step compounding with zero-floor enforcement
    bh_cap = initial_cap
    bh_cap_series = [bh_cap]
    bh_rets = sub_bh.pct_change().dropna()
    for r in bh_rets:
        if bh_cap <= 0.0:
            bh_cap = 0.0
        else:
            bh_cap = max(0.0, bh_cap * (1.0 + r))
        bh_cap_series.append(bh_cap)
    bh_s = pd.Series(bh_cap_series, index=sub_bh.index)
    
    strat_final = eq_s.iloc[-1]
    strat_ret = (strat_final / initial_cap - 1.0) * 100.0
    strat_dd = ((eq_s - eq_s.cummax()) / eq_s.cummax()).min() * 100.0
    strat_sharpe = (strat_rets.mean() / strat_rets.std() * np.sqrt(365)) if strat_rets.std() > 0 else 0.0
    
    bh_final = bh_s.iloc[-1]
    bh_ret = (bh_final / initial_cap - 1.0) * 100.0
    bh_dd = ((bh_s - bh_s.cummax()) / bh_s.cummax()).min() * 100.0
    bh_sharpe = (bh_rets.mean() / bh_rets.std() * np.sqrt(365)) if bh_rets.std() > 0 else 0.0
    
    btc_ret = (sub_btc.iloc[-1] / sub_btc.iloc[0] - 1.0) * 100.0 if len(sub_btc) > 1 else 0.0
    eth_ret = (sub_eth.iloc[-1] / sub_eth.iloc[0] - 1.0) * 100.0 if len(sub_eth) > 1 else 0.0
    sol_ret = (sub_sol.iloc[-1] / sub_sol.iloc[0] - 1.0) * 100.0 if len(sub_sol) > 1 else 0.0
    
    alpha = strat_ret - bh_ret
    
    return {
        'Period': name,
        'Dates': f"{st.strftime('%Y-%m-%d')} -> {en.strftime('%Y-%m-%d')}",
        'Days': (en - st).days,
        'Strat Final ($)': round(strat_final, 2),
        'Strat Ret (%)': round(strat_ret, 2),
        'Strat MaxDD (%)': round(strat_dd, 2),
        'Strat Sharpe': round(strat_sharpe, 2),
        'B&H Final ($)': round(bh_final, 2),
        'B&H Ret (%)': round(bh_ret, 2),
        'B&H MaxDD (%)': round(bh_dd, 2),
        'Alpha vs B&H (%)': round(alpha, 2),
        'BTC B&H (%)': round(btc_ret, 2),
        'ETH B&H (%)': round(eth_ret, 2),
        'SOL B&H (%)': round(sol_ret, 2)
    }

# 1. 8 QUARTERS (3 MONTHS EACH)
quarters_3m = [
    ('Quarter 1 (M1-3)', '2024-08-24', '2024-11-24'),
    ('Quarter 2 (M4-6)', '2024-11-24', '2025-02-24'),
    ('Quarter 3 (M7-9)', '2025-02-24', '2025-05-24'),
    ('Quarter 4 (M10-12)', '2025-05-24', '2025-08-24'),
    ('Quarter 5 (M13-15)', '2025-08-24', '2025-11-24'),
    ('Quarter 6 (M16-18)', '2025-11-24', '2026-02-24'),
    ('Quarter 7 (M19-21)', '2026-02-24', '2026-05-24'),
    ('Quarter 8 (M22-24)', '2026-05-24', '2026-08-24')
]

# 2. 4 HALF-YEARS (6 MONTHS EACH)
half_years_6m = [
    ('Half-Year 1 (M1-6)', '2024-08-24', '2025-02-24'),
    ('Half-Year 2 (M7-12)', '2025-02-24', '2025-08-24'),
    ('Half-Year 3 (M13-18)', '2025-08-24', '2026-02-24'),
    ('Half-Year 4 (M19-24)', '2026-02-24', '2026-08-24')
]

# 3. THREE QUARTERS (9 MONTHS) & TWO-THIRDS (8 MONTHS & 16 MONTHS)
three_quarters_9m = [
    ('3 Quarters - Window 1 (M1-9)', '2024-08-24', '2025-05-24'),
    ('3 Quarters - Window 2 (M10-18)', '2025-05-24', '2026-02-24'),
    ('3 Quarters - Window 3 (M16-24)', '2025-11-24', '2026-08-24')
]

two_thirds_year_8m = [
    ('2/3 Year - Part 1 (M1-8)', '2024-08-24', '2025-04-24'),
    ('2/3 Year - Part 2 (M9-16)', '2025-04-24', '2025-12-24'),
    ('2/3 Year - Part 3 (M17-24)', '2025-12-24', '2026-08-24')
]

two_thirds_2y_16m = [
    ('2/3 of 2Y - Early (M1-16)', '2024-08-24', '2025-12-24'),
    ('2/3 of 2Y - Late (M9-24)', '2025-04-24', '2026-08-24')
]

# 4. FULL 2 YEARS
full_2y = [
    ('Full 2 Years (Complete)', '2024-08-24', '2026-08-24')
]

def run_and_print_group(title, interval_list):
    print(f"\n{'='*100}")
    print(f"📊 {title}")
    print(f"{'='*100}")
    rows = []
    for name, st, en in interval_list:
        res = evaluate_interval(name, st, en, initial_cap=2000.0)
        if res:
            rows.append(res)
    df = pd.DataFrame(rows)
    display_cols = ['Period', 'Dates', 'Strat Final ($)', 'Strat Ret (%)', 'Strat MaxDD (%)', 'B&H Final ($)', 'B&H Ret (%)', 'B&H MaxDD (%)', 'Alpha vs B&H (%)', 'BTC B&H (%)', 'ETH B&H (%)', 'SOL B&H (%)']
    print(df[display_cols].to_string(index=False))
    return df

df_q = run_and_print_group("1. כל רבעון בנפרד (8 רבעונים של 3 חודשים - התחלה עם $2,000 בכל רבעון)", quarters_3m)
df_h = run_and_print_group("2. כל חצי שנה בנפרד (4 חצאי-שנה של 6 חודשים - התחלה עם $2,000 בכל חצי שנה)", half_years_6m)
df_3q = run_and_print_group("3.א. שלושה רבעונים בנפרד (3 רבעונים / 9 חודשים - התחלה עם $2,000)", three_quarters_9m)
df_23y = run_and_print_group("3.ב. שני שליש שנה בנפרד (8 חודשים - 3 מקטעים של 2/3 שנה - התחלה עם $2,000)", two_thirds_year_8m)
df_23_2y = run_and_print_group("3.ג. שני שליש מתוך השנתיים (16 חודשים - התחלה עם $2,000)", two_thirds_2y_16m)
df_full = run_and_print_group("4. כל השנתיים בנפרד (24 חודשים רצופים - התחלה עם $2,000)", full_2y)


# 5. CALENDAR QUARTERS & CALENDAR HALF-YEARS
calendar_quarters = [
    ('2024-Q3 (from 08/24)', '2024-08-24', '2024-09-30'),
    ('2024-Q4', '2024-10-01', '2024-12-31'),
    ('2025-Q1', '2025-01-01', '2025-03-31'),
    ('2025-Q2', '2025-04-01', '2025-06-30'),
    ('2025-Q3', '2025-07-01', '2025-09-30'),
    ('2025-Q4', '2025-10-01', '2025-12-31'),
    ('2026-Q1', '2026-01-01', '2026-03-31'),
    ('2026-Q2', '2026-04-01', '2026-06-30'),
    ('2026-Q3 (to 08/24)', '2026-07-01', '2026-08-24')
]

calendar_half_years = [
    ('2024-H2 (from 08/24)', '2024-08-24', '2024-12-31'),
    ('2025-H1', '2025-01-01', '2025-06-30'),
    ('2025-H2', '2025-07-01', '2025-12-31'),
    ('2026-H1', '2026-01-01', '2026-06-30')
]

df_cq = run_and_print_group("5. רבעונים קלנדריים (Calendar Quarters - התחלה עם $2,000)", calendar_quarters)
df_ch = run_and_print_group("6. חצאי-שנה קלנדריים (Calendar Half-Years - התחלה עם $2,000)", calendar_half_years)

