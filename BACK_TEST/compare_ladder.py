"""
השוואה נכונה: ladder_steps א=[1,2,4,10,20] vs ב=[1,3,6,10,20]
משתמש ב-run_dynamic_adaptive_20x_engine + evaluate_interval כמו test_2y_intervals.py
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/uns/TRADE/BACK_TEST')
import engine

CAPITAL = 2000.0

CONFIGS = {
    "A [1,2,4,10,20]": (1.0, 2.0, 4.0, 10.0, 20.0),
    "B [1,3,6,10,20]": (1.0, 3.0, 6.0, 10.0, 20.0),
}

INTERVALS = {
    "8 רבעונים (3M)": [
        ('Q1',  '2024-08-24','2024-11-24'),
        ('Q2',  '2024-11-24','2025-02-24'),
        ('Q3',  '2025-02-24','2025-05-24'),
        ('Q4',  '2025-05-24','2025-08-24'),
        ('Q5',  '2025-08-24','2025-11-24'),
        ('Q6',  '2025-11-24','2026-02-24'),
        ('Q7',  '2026-02-24','2026-05-24'),
        ('Q8',  '2026-05-24','2026-08-24'),
    ],
    "4 חצאי שנה (6M)": [
        ('H1','2024-08-24','2025-02-24'),
        ('H2','2025-02-24','2025-08-24'),
        ('H3','2025-08-24','2026-02-24'),
        ('H4','2026-02-24','2026-08-24'),
    ],
    "שנתיים מלאות": [
        ('Full 2Y','2024-08-24','2026-08-24'),
    ],
}

def evaluate_interval(dyn_eq, bh_comb, st, en, initial_cap=CAPITAL):
    """זהה ל-test_2y_intervals.py"""
    st_dt = pd.to_datetime(st)
    en_dt = pd.to_datetime(en)
    sub_eq = dyn_eq.loc[(dyn_eq.index >= st_dt) & (dyn_eq.index <= en_dt)]
    sub_bh = bh_comb.loc[(bh_comb.index >= st_dt) & (bh_comb.index <= en_dt)]
    if len(sub_eq) < 2:
        return None

    # Strategy compounding (exact replica of test_2y_intervals.py)
    strat_cap = initial_cap
    strat_rets = sub_eq.pct_change().dropna()
    series = [strat_cap]
    for r in strat_rets:
        strat_cap = max(0.0, strat_cap * (1.0 + r))
        series.append(strat_cap)
    eq_s = pd.Series(series, index=sub_eq.index)

    bh_cap = initial_cap
    bh_rets = sub_bh.pct_change().dropna()
    bh_series = [bh_cap]
    for r in bh_rets:
        bh_cap = max(0.0, bh_cap * (1.0 + r))
        bh_series.append(bh_cap)
    bh_s = pd.Series(bh_series, index=sub_bh.index)

    final    = eq_s.iloc[-1]
    ret      = (final / initial_cap - 1.0) * 100.0
    mdd      = ((eq_s - eq_s.cummax()) / eq_s.cummax()).min() * 100.0
    sharpe   = (strat_rets.mean() / strat_rets.std() * np.sqrt(365)) if strat_rets.std() > 0 else 0.0
    calmar   = ret / abs(mdd) if mdd != 0 else 0.0
    bh_final = bh_s.iloc[-1]
    bh_ret   = (bh_final / initial_cap - 1.0) * 100.0
    alpha    = ret - bh_ret
    return dict(final=final, ret=ret, mdd=mdd, sharpe=sharpe, calmar=calmar,
                bh_final=bh_final, bh_ret=bh_ret, alpha=alpha)

print("=" * 120)
print("🔬 השוואת ladder_steps: א=[1,2,4,10,20]  vs  ב=[1,3,6,10,20]")
print(f"   פונקציה: run_dynamic_adaptive_20x_engine | הון: ${CAPITAL:,.0f} | תקופה: אוג׳ 2024 → אוג׳ 2026")
print(f"   מתודה: כמו test_2y_intervals.py — returns מהסדרה המלאה, compounding על $2,000")
print("=" * 120)

# ── הרצת שני המנועים ──
results = {}
for label, ladder in CONFIGS.items():
    print(f"\n⏳  מריץ: {label} ...")
    dyn_eq, hy_base, bh_comb = engine.run_dynamic_adaptive_20x_engine(
        initial_capital=CAPITAL,
        ladder_steps=ladder
    )
    results[label] = (dyn_eq, bh_comb)
    # Spot-check Full 2Y inline
    m = evaluate_interval(dyn_eq, bh_comb, '2024-08-24', '2026-08-24')
    if m:
        print(f"   ✅ Full 2Y: ${m['final']:,.2f} ({m['ret']:+.1f}%) | MaxDD: {m['mdd']:.2f}%")

# ── הדפסת טבלאות ──
for group_name, intervals in INTERVALS.items():
    print(f"\n{'═'*120}")
    print(f"📊  {group_name}")
    print(f"{'═'*120}")
    print(f"{'תקופה':<14} | {'קונפיג':<26} | {'סוף ($)':>14} | {'תשואה (%)':>12} | {'MaxDD (%)':>10} | {'Sharpe':>8} | {'Calmar':>8} | {'vs B&H':>10} | {'הפרש vs A':>12}")
    print("-" * 120)

    for (iname, st, en) in intervals:
        ref_final = None
        bh_shown  = False
        row_data  = {}

        for label, (dyn_eq, bh_comb) in results.items():
            m = evaluate_interval(dyn_eq, bh_comb, st, en)
            if m is None:
                continue
            row_data[label] = m
            if ref_final is None:
                ref_final = m['final']

        for i, (label, m) in enumerate(row_data.items()):
            diff_vs_a = ""
            if i > 0 and ref_final:
                d = m['final'] - ref_final
                diff_vs_a = f"{d:+,.1f} ({100*d/ref_final:+.2f}%)"

            print(f"{iname:<14} | {label:<26} | {m['final']:>14,.2f} | {m['ret']:>+11.2f}% | {m['mdd']:>9.2f}% | {m['sharpe']:>8.3f} | {m['calmar']:>8.2f} | {m['alpha']:>+9.1f}% | {diff_vs_a:>12}")

            if not bh_shown and i == 0:
                print(f"{'':14} | {'📌 B&H Basket':<26} | {m['bh_final']:>14,.2f} | {m['bh_ret']:>+11.2f}% |{'':10}|{'':8}|{'':8}|{'':10}|{'':12}")
                bh_shown = True

        print("-" * 120)

# ── WIN RATE ──
print(f"\n{'═'*120}")
print("🏆  סיכום: כמה אינטרוולים כל קונפיג מנצח?")
print(f"{'═'*120}")
wins = {label: 0 for label in CONFIGS}
ties = 0
total = 0
all_ivs = [iv for grp in INTERVALS.values() for iv in grp]

for (iname, st, en) in all_ivs:
    rets = {}
    for label, (dyn_eq, bh_comb) in results.items():
        m = evaluate_interval(dyn_eq, bh_comb, st, en)
        if m:
            rets[label] = m['final']
    if len(rets) == len(CONFIGS):
        vals = list(rets.values())
        if abs(vals[0] - vals[1]) < 0.10:
            ties += 1
        else:
            wins[max(rets, key=rets.get)] += 1
        total += 1

for label, w in wins.items():
    print(f"  {label:<40} → ניצח {w}/{total} ({100*w/total:.0f}%)")
print(f"  תיקו: {ties}/{total}")
print(f"\n{'='*120}")
print("✅ הושלם!")
