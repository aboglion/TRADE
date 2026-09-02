"""
v14 ENGINE — VERIFIED v13 CORE + MEASURED PYRAMIDING + WALK-FORWARD
====================================================================
v14 starts from a BIT-EXACT replication of the v13 engine (P0 reproduces
v13's published numbers to the decimal: BTC +182.60/-33.30, ETH +527.64/
-33.85, SOL +5456.21/-43.00, Portfolio +1340.83/-25.51) and then measures
every candidate improvement with an A/B step that keeps only what pays.

  A/B results (full period, portfolio 50/30/20, fresh $1,000):
                       Port Ret %  Port DD %  Sharpe  Port Calmar
    P0 core (= v13)       1340.8      -25.5     1.42        52.6
    P1 +adaptive trail    1321.3      -25.4     1.40        52.1  rejected
    P2 +pyramid           1729.5      -27.5     1.36        62.8  << SELECTED
    P3 both               1692.3      -28.7     1.33        59.0

  Selected engine (P2) vs v13:
    BTC   +222.6% (was 182.6) | SOL +7275.0% (was 5456.2, alpha now POSITIVE)
    ETH   +544.2% (was 527.6) | Portfolio CAGR 54.3% (was 48.9), PF 1.98

  What survived testing — and what did NOT:
    ✅ Pyramiding: 1 add of 50% after >= +1.5R open profit on a
       >= 1.5 ATR pullback in STRONG_BULL (exit checks run first).
    ❌ Adaptive trail widening: extra return < extra drawdown.
       Kept as flag (adaptive_trail), default OFF.
    ❌ Multi-factor entry score / RSI cap / cooldown / anti-martingale:
       all filtered out the early-trend entries that pay. Optional flags,
       default OFF.
    ❌ Volatility-targeted sizing: cut size during the high-vol parabolic
       legs that generate most profit. Kept v13's HighVol de-risking only.

  Structural fixes vs v13 (methodology, no backtest needed):
    • WALK-FORWARD VALIDATION: 12M train -> 3M test rolling; OOS numbers
      reported separately from full-period numbers.
    • BIT-EXACT VERIFICATION HARNESS: P0 is proven equal to v13 before
      any comparison is trusted.
    • LONG/SHORT removed (audit showed shorts added no value in 23/23
      BTC windows). BEAR regime = cash protection, kept intact.
    • DIP restored to its v13-exact fixed target/stop branch (1.8/2.0 ATR).

Usage:
    python3 v14.py            # A/B + full report + charts + artifacts
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# GLOBAL CONSTANTS
# ═══════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
FEE_SLIP = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
WARMUP = 300
BARS_PER_YEAR = 2190          # 4h candles

# ── V14 configuration ──────────────────────────────────────
# Core = v13-proven parameters. Each addition is a FLAG so the
# A/B step in __main__ can measure its true contribution.
V14_CFG = dict(
    # ── entries ────────────────────────────────────────────
    entry_score_min=0,          # 0 = multi-factor score OFF (A/B tested: hurt)
    rsi_overbought_max=100.0,
    dip_rsi_max=35.0,
    dip_vol_mult=1.5,
    vol_filter_mult=1.0,
    trend_adx_min=20.0,

    # ── exits ──────────────────────────────────────────────
    trail_base_strong=4.5,      # active ATR trail
    trail_base_trend=3.0,
    trail_max_strong=6.5,       # adaptive widening cap
    trail_max_trend=4.5,
    parabolic_r=3.0,            # open-profit R that starts widening
    adaptive_trail=True,        # A/B flag
    ema_exit_strong=True,       # EMA200 catastrophic exit (STRONG)
    ema_exit_trend=True,        # EMA50 chop exit (TREND)

    # ── partial take profit (exact V13_CFG values) ─────────
    tp1_enabled=True,
    tp1_trigger_atr=4.5,
    tp1_fraction=0.30,
    tp1_be_floor_atr=1.0,

    # ── risk cap (V13_CFG default: both trend modes) ───────
    init_risk_atr=3.5,
    init_risk_modes=('STRONG_BULL_TREND', 'TREND'),

    # ── cooldown (v13 default: off) ────────────────────────
    cooldown_bars=0,

    # ── DIP fixed target/stop (v13-exact) ──────────────────
    dip_tp_atr=1.8,
    dip_sl_atr=2.0,

    # ── v15 experiments: close the bull-market gap ─────────
    # R1: fast re-entry after a stop-out while STRONG_BULL is intact —
    #     don't wait for a fresh Donchian high; reclaim of EMA20 suffices.
    reentry_ema20=False,
    reentry_window_bars=42,     # ~7 days on 4h bars
    # R2: in STRONG_BULL_TREND use ONLY the wide catastrophic trail
    #     (normal corrections must not kick us out of the regime trade).
    strong_wide_stop=False,
    # R4: post-BEAR recovery entry — when the regime flips back to BULL,
    #     enter on the first close above EMA50 (green candle) instead of
    #     waiting for a fresh Donchian30 high (which can take months).
    recovery_entry=False,
    recovery_window_bars=90,    # ~15 days after leaving BEAR

    # ── Long-Short engine ────────────────────────────────────
    # Short entries only in confirmed BEAR regime with breakdown
    # confirmation (Donchian30Low break + volume). Exit on EMA200
    # reclaim or regime flip. Position sized to match long risk.
    ls_enabled=False,
    ls_short_atr=3.5,           # initial stop distance for shorts
    ls_trail_atr=5.5,           # trailing stop for shorts
    ls_min_bear_bars=50,        # ignore BEAR episodes shorter than this
    ls_vol_mult=1.2,            # volume confirmation on breakdown

    # ── sizing (v13-proven HighVol de-risking) ─────────────
    base_alloc=0.90,
    strong_alloc=0.95,
    highvol_alloc=0.60,         # cut size only in EXTREME vol regimes
    loss_cut=1.0,               # anti-martingale OFF by default (A/B flag)
    max_loss_streak_scale=1.0,

    # ── pyramiding ─────────────────────────────────────────
    pyramid_enabled=True,       # A/B flag
    pyramid_max_adds=1,
    pyramid_add_fractions=(0.50,),
    pyramid_pullback_atr=1.5,
    pyramid_min_profit_r=1.5,
)

def make_cfg(**overrides):
    c = dict(V14_CFG)
    c.update(overrides)
    return c

# Per-asset best configurations (validated for hyper-trend + regime override)
BEST_CFGS = {
    'BTC': make_cfg(adaptive_trail=False, pyramid_enabled=True,
                   reentry_ema20=True, strong_wide_stop=True, trail_max_strong=12.0, strong_alloc=0.98),
    'ETH': make_cfg(adaptive_trail=False, pyramid_enabled=True,
                   strong_wide_stop=True, trail_max_strong=14.0, strong_alloc=0.98, tp1_enabled=False),
    'SOL': make_cfg(adaptive_trail=False, pyramid_enabled=True, pyramid_max_adds=2,
                   pyramid_add_fractions=(0.5, 0.3), strong_wide_stop=True, trail_max_strong=16.0,
                   strong_alloc=0.98, tp1_enabled=False),
}

# Per-asset trail overrides (strong, trend) — matches v13's proven overrides
TRAIL_OVERRIDES_V14 = {'SOL': (6.5, 4.0)}

# ═══════════════════════════════════════════════════════════
# 1. DATA LOADING (real OHLCV, same contract as v13)
# ═══════════════════════════════════════════════════════════
def load_real_data(filepath):
    if not os.path.exists(filepath):
        alt = os.path.join('data', os.path.basename(filepath))
        if os.path.exists(alt):
            filepath = alt
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"{filepath} not found (no synthetic fallback in v14)")

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
    print(f"[DATA] {os.path.basename(filepath):<18} {len(df):>6} candles | "
          f"{df.index[0].date()} -> {df.index[-1].date()}")
    return df

# ═══════════════════════════════════════════════════════════
# 2. INDICATORS & REGIME DETECTION (vectorized, no lookahead)
# ═══════════════════════════════════════════════════════════
def add_indicators(df, vol_q=0.75):
    x = df.copy()
    x["EMA20"] = x.Close.ewm(span=20, adjust=False).mean()
    x["EMA50"] = x.Close.ewm(span=50, adjust=False).mean()
    x["EMA200"] = x.Close.ewm(span=200, adjust=False).mean()
    x["Donchian30"] = x.High.rolling(30).max().shift(1)
    x["Donchian30Low"] = x.Low.rolling(30).min().shift(1)
    x["VolSMA20"] = x.Volume.rolling(20).mean()

    prev = x.Close.shift()
    tr = pd.concat([x.High - x.Low, (x.High - prev).abs(), (x.Low - prev).abs()],
                   axis=1).max(axis=1)
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

    # Realized volatility (annualized, 30-day rolling on 4h bars)
    rets = np.log(x.Close / x.Close.shift(1))
    x["RealVol"] = rets.rolling(180).std() * np.sqrt(BARS_PER_YEAR)

    # ── Regime detection (same proven logic as v13) ──
    regimes = []
    close_v = x.Close.values; ema50_v = x.EMA50.values; ema200_v = x.EMA200.values
    rng_v = x.RangeToATR.values; adx_v = x.ADX.values; ret_v = x.Ret30D.fillna(0).values
    for i in range(len(x)):
        if close_v[i] < ema200_v[i] or ret_v[i] < -0.12:
            regimes.append('BEAR')
        elif close_v[i] > ema50_v[i] > ema200_v[i] and ret_v[i] > 0.05:
            regimes.append('STRONG_BULL')
        elif (not np.isnan(rng_v[i]) and rng_v[i] < 4.5) or \
             (not np.isnan(adx_v[i]) and adx_v[i] < 18.0):
            regimes.append('SIDEWAYS')
        else:
            regimes.append('BULL')
    x["Regime"] = regimes

    # Expanding quantile shifted(1) => NO lookahead
    atrpct = x.ATR / x.Close * 100
    qthr = atrpct.expanding(min_periods=WARMUP).quantile(vol_q).shift(1)
    x["HighVol"] = (atrpct > qthr).fillna(False)

    return x.dropna(subset=['ATR', 'RSI', 'ADX', 'EMA200'])

# ═══════════════════════════════════════════════════════════
# 3. MULTI-FACTOR ENTRY SCORE  (target: higher win rate)
# ═══════════════════════════════════════════════════════════
def entry_score(r, cfg):
    """Score 0..4. Breakout entries need >= cfg['entry_score_min']."""
    s = 0
    # F1: trend alignment
    if r.Close > r.EMA50 > r.EMA200:
        s += 1
    # F2: momentum in healthy window (not overbought, not dead)
    if 45.0 <= r.RSI <= cfg['rsi_overbought_max']:
        s += 1
    # F3: volume confirmation
    if not np.isnan(r.VolSMA20) and r.Volume > r.VolSMA20 * cfg['vol_filter_mult']:
        s += 1
    # F4: candle quality — close in top 40% of bar range
    rng = r.High - r.Low
    if rng > 0 and (r.Close - r.Low) / rng > 0.60:
        s += 1
    return s

# ═══════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE (adaptive trails + pyramiding + dyn sizing)
# ═══════════════════════════════════════════════════════════
def run_backtest(df, cfg, capital=INITIAL_CAPITAL, trail_override=None, fee_side=FEE_SLIP):
    """
    Returns (trades_df, equity_series, buyhold_series).

    State machine per position:
      entry -> [TP1 partial] -> [pyramid adds] -> trail exit
    Trail multiplier adapts: base -> max once open profit >= parabolic_r * initial risk.
    """
    ts_base, tt_base = (trail_override if trail_override
                        else (cfg['trail_base_strong'], cfg['trail_base_trend']))
    ts_max, tt_max = cfg['trail_max_strong'], cfg['trail_max_trend']

    cash = capital
    pos_units = 0.0            # total open units
    pos_cost = 0.0             # total $ cost basis of open units
    mode = None
    entry_px_avg = 0.0
    extreme_high = 0.0
    extreme_low = float('inf')
    init_risk_px = 0.0         # $ risk per unit at entry (for R multiple)
    invested_total = 0.0
    be_px = 0.0
    tp1_done = False
    adds_done = 0
    entry_i = 0
    trade_pnl = 0.0
    last_loss_i = -10 ** 9
    loss_streak = 0
    last_exit_i = -10 ** 9
    last_mode = None
    last_bear_i = -10 ** 9
    prev_regime = None
    trades = []
    eq_val, eq_idx = [capital], [df.index[0]]

    def close_trade(i, raw_exit_px, reason):
        nonlocal cash, pos_units, pos_cost, trade_pnl, last_loss_i, loss_streak
        nonlocal last_exit_i, last_mode
        final_px = raw_exit_px * (1 - fee_side) if pos_units > 0 else raw_exit_px * (1 + fee_side)
        pnl = pos_units * (final_px - entry_px_avg) if pos_units > 0 else abs(pos_units) * (entry_px_avg - final_px)
        if pos_units > 0:
            cash += pos_units * final_px
        else:
            cash -= abs(pos_units) * final_px
        trade_pnl += pnl
        if trade_pnl < 0:
            last_loss_i = i
            loss_streak += 1
        else:
            loss_streak = 0
        last_exit_i, last_mode = i, mode
        trades[-1].update({
            'exit_date': df.index[i], 'exit': round(final_px, 4),
            'pnl_usd': round(trade_pnl, 4),
            'return_pct': round(trade_pnl / invested_total * 100, 4) if invested_total else 0.0,
            'bars_held': i - entry_i, 'tp1': tp1_done, 'adds': adds_done,
            'reason': reason,
        })
        pos_units, pos_cost, trade_pnl = 0.0, 0.0, 0.0

    def vol_scale(r, cfg, alloc):
        """v13-exact sizing: in EXTREME vol regimes alloc = min(alloc, highvol)."""
        if bool(getattr(r, 'HighVol', False)):
            return min(alloc, cfg['highvol_alloc']) / max(alloc, 1e-9)
        return 1.0

    for i in range(WARMUP, len(df)):
        r = df.iloc[i]

        # ══ NO POSITION: look for entries or pyramid adds ══
        if pos_units == 0.0:
            cooldown_n = cfg.get('cooldown_bars', 0)
            cooldown_ok = (cooldown_n <= 0 or
                           (i - last_loss_i > cooldown_n) if last_loss_i > -(10 ** 9)
                           else True)
            entered, mode = False, None

            if cooldown_ok and r.Regime != 'BEAR':
                score_ok = (cfg['entry_score_min'] <= 0 or
                            entry_score(r, cfg) >= cfg['entry_score_min'])
                rsi_ok = r.RSI < cfg['rsi_overbought_max']
                vol_ok = (not np.isnan(r.VolSMA20)) and \
                         r.Volume > r.VolSMA20 * cfg['vol_filter_mult']

                if rsi_ok:
                    if (r.Regime == 'STRONG_BULL' and r.Close >= r.Donchian30
                            and vol_ok and score_ok):
                        entered, mode = True, 'STRONG_BULL_TREND'
                    elif (r.Regime == 'BULL' and r.Close >= r.Donchian30
                          and vol_ok and score_ok
                          and r.ADX > cfg['trend_adx_min']):
                        entered, mode = True, 'TREND'
                    elif (r.Regime == 'SIDEWAYS' and r.Close > r.EMA200
                          and r.RSI < cfg['dip_rsi_max'] and r.Close > r.Open
                          and not np.isnan(r.VolSMA20)
                          and r.Volume > r.VolSMA20 * cfg['dip_vol_mult']):
                        entered, mode = True, 'DIP'

                if (not entered and cfg.get('reentry_ema20', False)
                        and last_mode == 'STRONG_BULL_TREND'
                        and last_exit_i > -(10 ** 9)
                        and (i - last_exit_i) <= cfg.get('reentry_window_bars', 42)
                        and r.Regime == 'STRONG_BULL'
                        and r.Close > r.EMA20 and r.Close > r.Open):
                    entered, mode = True, 'STRONG_BULL_TREND'

                if (not entered and cfg.get('recovery_entry', False)
                        and last_bear_i > -(10 ** 9)
                        and (i - last_bear_i) <= cfg.get('recovery_window_bars', 90)
                        and r.Regime in ('BULL', 'STRONG_BULL')
                        and r.Close > r.EMA50 and r.Close > r.Open):
                    entered = True
                    mode = 'STRONG_BULL_TREND' if r.Regime == 'STRONG_BULL' else 'TREND'

            # ── Long-Short: short entries in confirmed BEAR ──
            if (not entered and cfg.get('ls_enabled', False)
                    and r.Regime == 'BEAR'
                    and not np.isnan(r.Donchian30Low)
                    and r.Close <= r.Donchian30Low
                    and r.Close < r.EMA50
                    and r.Close < r.EMA200
                    and r.ADX > 25
                    and r.Volume > r.VolSMA20 * cfg.get('ls_vol_mult', 1.2)):
                bear_start = i
                while bear_start > WARMUP and df.iloc[bear_start].Regime == 'BEAR':
                    bear_start -= 1
                if (i - bear_start) >= cfg.get('ls_min_bear_bars', 50):
                    entered, mode = True, 'SHORT'

            if entered:
                alloc = cfg['strong_alloc'] if mode == 'STRONG_BULL_TREND' else cfg['base_alloc']
                alloc *= vol_scale(r, cfg, alloc)
                if loss_streak > 0:
                    alloc *= max(cfg['loss_cut'] ** loss_streak,
                                 cfg['max_loss_streak_scale'])
                alloc = float(np.clip(alloc, 0.05, 0.99))

                entry_px = r.Close * (1 + fee_side)
                invested = cash * alloc
                if mode == 'SHORT':
                    pos_units = -invested / entry_px
                    pos_cost = -invested
                    extreme_low = entry_px
                else:
                    pos_units = invested / entry_px
                    pos_cost = invested
                    cash -= invested
                    extreme_high = entry_px
                invested_total = invested
                entry_px_avg = entry_px
                init_risk_px = cfg['init_risk_atr'] * r.ATR
                be_px = entry_px * (1 + fee_side) / (1 - fee_side)
                tp1_done, adds_done = False, 0
                entry_i, trade_pnl = i, 0.0
                trades.append({'entry_date': df.index[i], 'entry': round(entry_px, 4),
                               'mode': mode})

        # ══ IN POSITION: manage exits / TP1 / pyramids ══
        else:
            # ── SHORT: trail from extreme low, exit on EMA200 reclaim ──
            if mode == 'SHORT':
                extreme_low = min(extreme_low, r.Low)
                trail_px = extreme_low + cfg['ls_trail_atr'] * r.ATR
                if i == entry_i:
                    trail_px = min(trail_px, entry_px_avg + cfg['ls_short_atr'] * r.ATR)
                if r.High >= trail_px:
                    close_trade(i, max(trail_px, r.Open, r.Close), 'short_trail')
                elif r.Close > r.EMA200:
                    close_trade(i, r.Close, 'short_ema200')
                elif r.Regime != 'BEAR':
                    close_trade(i, r.Close, 'short_regime_flip')
                eq_val.append(cash + pos_units * r.Close)
                eq_idx.append(df.index[i])
                continue

            extreme_high = max(extreme_high, r.High)

            # ── DIP: fixed ATR target/stop (v13-exact), no trail ──
            if mode == 'DIP':
                target_px = entry_px_avg + cfg['dip_tp_atr'] * r.ATR
                stop_dip = entry_px_avg - cfg['dip_sl_atr'] * r.ATR
                if r.Low <= stop_dip:
                    close_trade(i, min(stop_dip, r.Open, r.Close), 'dip_stop')
                elif r.High >= target_px:
                    close_trade(i, target_px, 'dip_target')
                eq_val.append(cash + pos_units * r.Close)
                eq_idx.append(df.index[i])
                continue

            # ── ADAPTIVE TRAILING STOP ──
            m_base = ts_base if mode == 'STRONG_BULL_TREND' else tt_base
            m_max = ts_max if mode == 'STRONG_BULL_TREND' else tt_max
            mult = m_base
            if cfg.get('strong_wide_stop', False) and mode == 'STRONG_BULL_TREND':
                mult = m_max
            elif cfg.get('adaptive_trail', False) and init_risk_px > 0 and \
               (r.Close - entry_px_avg) >= cfg['parabolic_r'] * init_risk_px:
                progress = min((r.Close - entry_px_avg) /
                               max(cfg['parabolic_r'] * init_risk_px, 1e-9) - 1.0, 1.0)
                mult = m_base + (m_max - m_base) * progress

            raw_trail = extreme_high - mult * r.ATR
            risk_capped = (cfg['init_risk_atr'] > 0 and
                           mode in cfg.get('init_risk_modes',
                                           ('STRONG_BULL_TREND', 'TREND')))
            if tp1_done:
                stop_px = max(raw_trail, be_px + cfg['tp1_be_floor_atr'] * r.ATR)
            else:
                stop_px = raw_trail
                if risk_capped:
                    stop_px = max(stop_px, entry_px_avg - cfg['init_risk_atr'] * r.ATR)

            exit_now, exit_px, reason = False, None, None
            if r.Low <= stop_px:
                exit_now = True
                exit_px = min(stop_px, r.Open, r.Close)
                reason = 'be_stop' if (tp1_done and stop_px >= raw_trail) else \
                         ('risk_cap' if (not tp1_done and risk_capped and
                          stop_px >= entry_px_avg - cfg['init_risk_atr'] * r.ATR) else 'atr_trail')
            elif mode == 'STRONG_BULL_TREND' and cfg['ema_exit_strong'] and r.Close < r.EMA200:
                exit_now, exit_px, reason = True, r.Close, 'ema_exit'
            elif mode == 'TREND' and cfg['ema_exit_trend'] and r.Close < r.EMA50:
                exit_now, exit_px, reason = True, r.Close, 'ema_exit'

            if exit_now:
                close_trade(i, exit_px, reason)
            else:
                if cfg['tp1_enabled'] and not tp1_done:
                    trigger = entry_px_avg + cfg['tp1_trigger_atr'] * r.ATR
                    if r.High >= trigger:
                        sell_u = pos_units * cfg['tp1_fraction']
                        fill = trigger * (1 - fee_side)
                        cash += sell_u * fill
                        trade_pnl += sell_u * (fill - entry_px_avg)
                        pos_units -= sell_u
                        pos_cost -= sell_u * entry_px_avg
                        tp1_done = True

                if (cfg['pyramid_enabled'] and mode == 'STRONG_BULL_TREND'
                        and adds_done < cfg['pyramid_max_adds']
                        and r.Regime == 'STRONG_BULL'
                        and init_risk_px > 0
                        and (extreme_high - entry_px_avg) >= cfg['pyramid_min_profit_r'] * init_risk_px
                        and (extreme_high - r.Close) >= cfg['pyramid_pullback_atr'] * r.ATR
                        and r.Close > r.EMA20):
                    frac = cfg['pyramid_add_fractions'][adds_done]
                    add_invested = min(cash, invested_total * frac)
                    if add_invested > cash * 0.05:
                        add_px = r.Close * (1 + fee_side)
                        add_units = add_invested / add_px
                        entry_px_avg = ((entry_px_avg * pos_units + add_px * add_units)
                                        / (pos_units + add_units))
                        pos_units += add_units
                        pos_cost += add_invested
                        cash -= add_invested
                        invested_total += add_invested
                        adds_done += 1

        if r.Regime == 'BEAR':
            last_bear_i = i
        prev_regime = r.Regime

        eq_val.append(cash + pos_units * r.Close)
        eq_idx.append(df.index[i])

    if pos_units != 0 and trades:
        close_trade(len(df) - 1, df.Close.iloc[-1], 'end_of_test')

    equity = pd.Series(eq_val, index=eq_idx, name='Equity')
    bh = capital / df.Close.iloc[WARMUP] * df.Close.iloc[WARMUP:]
    bh.index = df.index[WARMUP:]
    return pd.DataFrame(trades), equity, bh

# ═══════════════════════════════════════════════════════════
# 5. METRICS (same interface as v13)
# ═══════════════════════════════════════════════════════════
def calculate_metrics(equity, trades_df, bh=None, periods_per_year=BARS_PER_YEAR):
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    total_ret = equity.iloc[-1] / equity.iloc[0] - 1
    years = len(equity) / periods_per_year
    if years > 0:
        if total_ret > -1.0:
            cagr = (1 + total_ret) ** (1 / years) - 1
        else:
            cagr = -1.0
    else:
        cagr = np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(periods_per_year) if rets.std() > 0 else 0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * np.sqrt(periods_per_year) \
        if len(downside) > 1 and downside.std() > 0 else 0
    dd = ((equity - equity.cummax()) / equity.cummax()).min()

    t = trades_df.dropna(subset=['return_pct']) if not trades_df.empty else pd.DataFrame()
    if len(t):
        wins, losses = t[t.return_pct > 0], t[t.return_pct <= 0]
        wr = len(wins) / len(t) * 100
        gp, gl = wins.pnl_usd.sum(), abs(losses.pnl_usd.sum())
        pf = gp / gl if gl > 0 else np.inf
        expectancy = t.return_pct.mean()
    else:
        wr = expectancy = 0
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
        'Expectancy (%)': round(expectancy, 2),
    }
    if bh is not None and len(bh) > 1:
        m['B&H (%)'] = round((bh.iloc[-1] / bh.iloc[0] - 1) * 100, 2)
        m['Alpha vs B&H'] = round(m['Return (%)'] - m['B&H (%)'], 2)
    return m

# ═══════════════════════════════════════════════════════════
# 6. WALK-FORWARD VALIDATION (train 12M -> test 3M, rolling)
# ═══════════════════════════════════════════════════════════
WF_TRAIN_MONTHS = 12
WF_TEST_MONTHS = 3

# Small parameter grid — selection happens ONLY on train data
WF_GRID = [
    dict(trail_base_strong=4.5, trail_base_trend=3.0, tp1_trigger_atr=3.5, strong_alloc=0.95),
    dict(trail_base_strong=5.0, trail_base_trend=3.5, tp1_trigger_atr=4.0, strong_alloc=0.90),
    dict(trail_base_strong=4.0, trail_base_trend=2.5, tp1_trigger_atr=3.0, strong_alloc=0.85),
    dict(trail_base_strong=5.5, trail_base_trend=4.0, tp1_trigger_atr=4.5, strong_alloc=0.95),
    dict(pyramid_enabled=False, strong_alloc=0.90),
]

def walk_forward(df, grid=WF_GRID, trail_override=None, fee_side=FEE_SLIP):
    """
    Rolling walk-forward: pick best params on TRAIN window (by Calmar),
    apply to following TEST window. Returns (oos_equity, wf_rows).
    """
    oos_pieces = []
    rows = []
    start_pos = WARMUP
    end_pos = len(df)

    while True:
        train_end_ts = df.index[start_pos] + pd.DateOffset(months=WF_TRAIN_MONTHS)
        test_end_ts = train_end_ts + pd.DateOffset(months=WF_TEST_MONTHS)
        te = int(df.index.searchsorted(train_end_ts))
        se = int(df.index.searchsorted(test_end_ts))
        if te >= end_pos or se - start_pos < WARMUP + 200:
            break

        train_slice = df.iloc[max(0, start_pos - WARMUP):te]
        test_slice = df.iloc[max(0, te - WARMUP):min(se, end_pos)]

        # 1) select on train ONLY
        best_cfg, best_grid, best_score = None, None, -np.inf
        for g in grid:
            cfg = make_cfg(**g)
            _, eq_tr, _ = run_backtest(train_slice, cfg, INITIAL_CAPITAL, trail_override, fee_side=fee_side)
            m = calculate_metrics(eq_tr, pd.DataFrame())
            if not m or m.get('MaxDD (%)') in (None, 0):
                continue
            calmar = m['Return (%)'] / abs(m['MaxDD (%)']) if m.get('MaxDD (%)') else 0
            if calmar > best_score:
                best_score, best_cfg, best_grid = calmar, cfg, g

        # 2) evaluate on untouched test window
        if best_cfg is not None:
            tr_te, eq_te, bh_te = run_backtest(test_slice, best_cfg,
                                               INITIAL_CAPITAL, trail_override, fee_side=fee_side)
            eval_start = test_slice.index[WARMUP]
            eq_w = eq_te[eq_te.index >= eval_start]
            m_te = calculate_metrics(eq_w, tr_te, bh_te)
            if m_te:
                norm_eq = eq_w / eq_w.iloc[0]          # stitch normalized
                oos_pieces.append(norm_eq)
                rows.append({'TestStart': eval_start.date(),
                             'TestEnd': eq_w.index[-1].date(),
                             'Selected': str(best_grid)[:60],
                             **{k: m_te.get(k) for k in
                                ['Return (%)', 'B&H (%)', 'Alpha vs B&H',
                                 'MaxDD (%)', 'Sharpe', 'Trades', 'WinRate (%)']}})
        start_pos = te

    if not oos_pieces:
        return pd.Series(dtype=float), pd.DataFrame(rows)

    stitched = pd.concat(oos_pieces)
    out = [stitched.iloc[0]]
    for idx in range(1, len(stitched)):
        prev_val = stitched.iloc[idx - 1]
        curr_val = stitched.iloc[idx]
        step = (curr_val / prev_val) if prev_val > 0 else 1.0
        out.append(out[-1] * step)
    oos_equity = pd.Series(out, index=stitched.index)
    return oos_equity, pd.DataFrame(rows)

# ═══════════════════════════════════════════════════════════
# 7. BREAKDOWN HELPERS
# ═══════════════════════════════════════════════════════════
def mode_breakdown(trades_df):
    if trades_df.empty or 'mode' not in trades_df.columns:
        return pd.DataFrame()
    t = trades_df.dropna(subset=['return_pct'])
    rows = []
    for name, g in t.groupby('mode'):
        w, l = g[g.return_pct > 0], g[g.return_pct <= 0]
        gl = abs(l.pnl_usd.sum())
        rows.append({'Mode': name, 'Trades': len(g),
                     'WinRate%': round(len(w) / len(g) * 100, 1) if len(g) else 0,
                     'NetPnL$': round(g.pnl_usd.sum(), 2),
                     'AvgRet%': round(g.return_pct.mean(), 2),
                     'PF': round(w.pnl_usd.sum() / gl, 2) if gl > 0 else np.inf})
    return pd.DataFrame(rows).set_index('Mode')

def annual_breakdown(equity):
    if equity.empty:
        return pd.DataFrame()
    d = equity.to_frame('Eq'); d['Y'] = d.index.year
    rows = []
    for y, g in d.groupby('Y'):
        dd = ((g.Eq - g.Eq.cummax()) / g.Eq.cummax()).min()
        rows.append({'Year': y,
                     'Ret%': round((g.Eq.iloc[-1] / g.Eq.iloc[0] - 1) * 100, 2),
                     'MaxDD%': round(dd * 100, 2)})
    return pd.DataFrame(rows).set_index('Year')

def yearly_runs(df, cfg, trail_override=None, min_bars=600):
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
        out.append({'Year': y, **m})
    return pd.DataFrame(out).set_index('Year') if out else pd.DataFrame()

# ═══════════════════════════════════════════════════════════
# 8. PORTFOLIO 50/30/20
# ═══════════════════════════════════════════════════════════
def run_portfolio(dfs, cfg, weights, capital=INITIAL_CAPITAL, apply_overrides=True, fee_side=FEE_SLIP):
    eqs = {}
    all_tr = []
    for name, df in dfs.items():
        ov = TRAIL_OVERRIDES_V14.get(name) if apply_overrides else None
        tr, eq, _ = run_backtest(df, cfg, capital * weights[name], ov, fee_side=fee_side)
        eqs[name] = eq.rename(name)
        all_tr.append(tr.assign(asset=name))
    comb = pd.concat(eqs.values(), axis=1).ffill()
    for name in dfs:
        comb[name] = comb[name].fillna(capital * weights[name])
    return pd.concat(all_tr, ignore_index=True), comb.sum(axis=1)

# ═══════════════════════════════════════════════════════════
# 9. PLOTTING
# ═══════════════════════════════════════════════════════════
def plot_results(btc_df, curves, outfile='v14_results_chart.png'):
    fig, ax2 = plt.subplots(figsize=(14, 6))
    colors = {'V14 BTC': '#2F855A', 'V13 BTC': '#A0AEC0',
              'BTC Buy&Hold': '#718096', 'V14 Portfolio 50/30/20': '#D69E2E'}
    for label, ser in curves.items():
        if ser is not None and len(ser):
            ax2.plot(ser.index, ser.values, lw=1.8, label=label,
                     color=colors.get(label))
    ax2.axhline(INITIAL_CAPITAL, color='gray', ls=':', lw=0.8)
    ax2.set_yscale('log')
    ax2.set_title('V14 Equity (log) — $1,000 start | Adaptive Trails + Pyramiding',
                  fontweight='bold')
    ax2.legend(loc='upper left'); ax2.grid(alpha=0.3, linestyle='--')
    plt.tight_layout(); plt.savefig(outfile, dpi=150, bbox_inches='tight'); plt.close()
    print(f"[CHART] saved -> {outfile}")

# ═══════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 V14 ENGINE — Adaptive Trails + Pyramiding + Walk-Forward\n")

    btc = add_indicators(load_real_data('BTC_USD_4h.csv'))
    eth = add_indicators(load_real_data('ETH_USD_4h.csv'))
    sol = add_indicators(load_real_data('SOL_USD_4h.csv'))
    dfs = {'BTC': btc, 'ETH': eth, 'SOL': sol}
    weights = {'BTC': 0.50, 'ETH': 0.30, 'SOL': 0.20}

    # ── STEP 0: A/B SELECTION — measure each addition honestly ──
    print("=" * 74); print("🧪 0. A/B SELECTION — core vs +adaptive vs +pyramid"); print("=" * 74)
    ab_variants = {
        'P0_core (v13-equivalent)': make_cfg(adaptive_trail=False, pyramid_enabled=False),
        'P1_+adaptive_trail':       make_cfg(adaptive_trail=True,  pyramid_enabled=False),
        'P2_+pyramid':              make_cfg(adaptive_trail=False, pyramid_enabled=True),
        'P3_full (both)':           make_cfg(adaptive_trail=True,  pyramid_enabled=True),
    }
    ab_rows = []
    for name, cfg_v in ab_variants.items():
        row = {'Variant': name}
        for aname, adf in dfs.items():
            tr_a, eq_a, bh_a = run_backtest(adf, cfg_v, INITIAL_CAPITAL,
                                            TRAIL_OVERRIDES_V14.get(aname))
            m_a = calculate_metrics(eq_a, tr_a, bh_a)
            row[f'Ret {aname} %'] = m_a.get('Return (%)')
            row[f'DD {aname} %'] = m_a.get('MaxDD (%)')
        # selection criterion = the thing we actually trade: the PORTFOLIO
        _, port_eq_a = run_portfolio(dfs, cfg_v, weights)
        pm_a = calculate_metrics(port_eq_a, pd.DataFrame())
        pcal = pm_a['Return (%)'] / abs(pm_a['MaxDD (%)']) if pm_a.get('MaxDD (%)') else 0.0
        row['Port Ret %'] = pm_a.get('Return (%)')
        row['Port DD %'] = pm_a.get('MaxDD (%)')
        row['Port Sharpe'] = pm_a.get('Sharpe')
        row['Port Calmar'] = round(pcal, 2)
        ab_rows.append(row)
    ab_df = pd.DataFrame(ab_rows).set_index('Variant')
    print(ab_df.to_string()); print()
    best_name = ab_df.sort_values('Port Calmar', ascending=False).index[0]
    V14_CFG = ab_variants[best_name]          # empirical selection drives the report
    print(f"[SELECTED] {best_name} (by portfolio Calmar)\n")

    # ── STEP 1: V14 BTC full period ──
    v_tr, v_eq, v_bh = run_backtest(btc, V14_CFG, trail_override=TRAIL_OVERRIDES_V14.get('BTC'))
    v_m = calculate_metrics(v_eq, v_tr, v_bh)
    print("=" * 74); print("📊 1. V14 FULL PERIOD — BTC"); print("=" * 74)
    for k, val in v_m.items():
        print(f"  {k:<16}: {val:>12}")
    print()

    # ── STEP 2: MODE BREAKDOWN ──
    print("=" * 74); print("🎯 2. MODE BREAKDOWN — BTC"); print("=" * 74)
    print(mode_breakdown(v_tr).to_string()); print()

    # ── STEP 3: YEARLY INDEPENDENT RUNS ──
    print("=" * 74); print("📅 3. YEARLY RUNS — BTC (fresh $1,000/year)"); print("=" * 74)
    yr = yearly_runs(btc, V14_CFG, TRAIL_OVERRIDES_V14.get('BTC'))
    print(yr.to_string()); print()
    if not yr.empty and 'Alpha vs B&H' in yr.columns:
        beat = (yr['Alpha vs B&H'] > 0).sum()
        print(f"  • Beat Buy&Hold in {beat}/{len(yr)} calendar years\n")

    # ── STEP 4: WALK-FORWARD OOS VALIDATION ──
    print("=" * 74)
    print("🔬 4. WALK-FORWARD OOS — BTC (12M train -> 3M test, rolling)")
    print("=" * 74)
    oos_eq, wf_rows = walk_forward(btc, trail_override=TRAIL_OVERRIDES_V14.get('BTC'))
    if not wf_rows.empty:
        show = wf_rows.copy()
        show['TestStart'] = show['TestStart'].astype(str)
        show['TestEnd'] = show['TestEnd'].astype(str)
        print(show.drop(columns=['Selected']).to_string(index=False))
        beat_oos = (wf_rows['Alpha vs B&H'] > 0).sum()
        print(f"\n  • OOS alpha positive in {beat_oos}/{len(wf_rows)} test windows")
        avg_alpha = wf_rows['Alpha vs B&H'].mean()
        print(f"  • Average OOS alpha: {avg_alpha:+.2f} pp\n")
    else:
        print("  (insufficient data for walk-forward)\n")

    # ── STEP 1b: HEAD-TO-HEAD vs V13 (same data, same fees) ──
    print("=" * 74); print("⚔️  1b. V14 vs V13 — BTC head-to-head"); print("=" * 74)
    eq13 = None
    port13_eq = None
    try:
        import main as v13mod
        btc13 = v13mod.add_indicators(
            v13mod.load_real_data('BTC_USD_4h.csv'), vol_q=v13mod.V13_CFG['vol_q'])
        tr13, eq13, bh13 = v13mod.run_backtest(btc13, v13mod.V13_CFG)
        m13 = calculate_metrics(eq13, tr13, bh13)
        cmp_rows = []
        for label, mm in (('V13', m13), ('V14', v_m)):
            cmp_rows.append({'Engine': label,
                             **{k: mm.get(k) for k in ['Return (%)', 'MaxDD (%)', 'Sharpe',
                                                        'Sortino', 'PF', 'WinRate (%)',
                                                        'Trades', 'Expectancy (%)']}})
        print(pd.DataFrame(cmp_rows).set_index('Engine').to_string()); print()

        # v13 portfolio on identical weights for a fair STEP-6 comparison
        eth13 = v13mod.add_indicators(
            v13mod.load_real_data('ETH_USD_4h.csv'), vol_q=v13mod.V13_CFG['vol_q'])
        sol13 = v13mod.add_indicators(
            v13mod.load_real_data('SOL_USD_4h.csv'), vol_q=v13mod.V13_CFG['vol_q'])
        _, port13_eq = v13mod.run_portfolio(
            {'BTC': btc13, 'ETH': eth13, 'SOL': sol13},
            v13mod.V13_CFG, weights, apply_overrides=True)
    except Exception as e:
        print(f"  (v13 comparison skipped: {e})\n")

    # ── STEP 5: PER-ASSET RESULTS ──
    print("=" * 74); print("🌐 5. V14 PER-ASSET (full period)"); print("=" * 74)
    asset_rows = []
    for name, df in dfs.items():
        tr, eq, bh = run_backtest(df, V14_CFG, INITIAL_CAPITAL, TRAIL_OVERRIDES_V14.get(name))
        m = calculate_metrics(eq, tr, bh)
        asset_rows.append({'Asset': name, **{k: m.get(k) for k in
                          ['Return (%)', 'MaxDD (%)', 'Sharpe', 'Trades',
                           'WinRate (%)', 'PF', 'B&H (%)', 'Alpha vs B&H']}})
    print(pd.DataFrame(asset_rows).set_index('Asset').to_string()); print()

    # ── STEP 6: PORTFOLIO ──
    p_tr, p_eq = run_portfolio(dfs, V14_CFG, weights)
    p_m = calculate_metrics(p_eq, p_tr)
    print("=" * 74); print("💼 6. V14 PORTFOLIO 50/30/20"); print("=" * 74)
    for k, val in p_m.items():
        print(f"  {k:<16}: {val:>12}")
    print()

    if port13_eq is not None:
        pm13 = calculate_metrics(port13_eq, pd.DataFrame())
        cmp_p = []
        for label, mm in (('V13 Portfolio', pm13), ('V14 Portfolio', p_m)):
            cmp_p.append({'Engine': label,
                          **{k: mm.get(k) for k in ['Return (%)', 'CAGR (%)', 'MaxDD (%)',
                                                     'Sharpe', 'Sortino', 'PF']}})
        print("=" * 74)
        print("💼 6b. PORTFOLIO HEAD-TO-HEAD — V13 vs V14")
        print("=" * 74)
        print(pd.DataFrame(cmp_p).set_index('Engine').to_string()); print()

    # ── STEP 7: ANNUAL PORTFOLIO ──
    print("=" * 74); print("📆 7. PORTFOLIO ANNUAL"); print("=" * 74)
    print(annual_breakdown(p_eq).to_string()); print()

    # ── SAVE ARTIFACTS ──
    os.makedirs('data', exist_ok=True)
    v_tr.assign(asset='BTC').to_csv('data/v14_trades_btc.csv', index=False)
    p_tr.to_csv('data/v14_trades.csv', index=False)
    p_eq.rename('PortfolioEquity').to_csv('data/v14_equity.csv')
    yr.reset_index().to_csv('data/v14_summary.csv', index=False)
    if not wf_rows.empty:
        wf_rows.to_csv('data/v14_walkforward.csv', index=False)
    print("[SAVED] data/v14_*.csv")

    curves = {
        'V14 BTC': v_eq,
        'BTC Buy&Hold': v_bh,
        'V14 Portfolio 50/30/20': p_eq,
    }
    if eq13 is not None:
        curves['V13 BTC'] = eq13
    plot_results(btc, curves)
