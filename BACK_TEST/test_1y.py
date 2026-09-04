import pandas as pd
from engine import run_macro_portfolio

macro_eq, macro_tr, macro_comb = run_macro_portfolio()
cutoff_date = macro_eq.index[-1] - pd.Timedelta(days=365)
recent_trades = macro_tr[macro_tr['entry_date'] >= cutoff_date]
print(recent_trades[['asset', 'entry_date', 'exit_date', 'mode', 'return_pct_net']].to_string())
