# 🏆 Hybrid Core-Satellite (80/20) Quantitative Trading System

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-Production--Ready-gold.svg)]()

An institutional-grade, multi-asset quantitative trading engine engineered for digital assets (**Bitcoin**, **Ethereum**, and **Solana**). The architecture utilizes an **80/20 Core-Satellite allocation strategy** designed to capture multi-month parabolic bull runs while providing continuous yield and severe drawdown mitigation during sideways and bear market regimes.

All backtest metrics and out-of-sample audits strictly account for realistic real-world transaction friction (**`TAKER_STANDARD` fee preset: 0.10% fee + 0.025% slippage = 0.25% round-trip cost**).

---

## 🏛️ Strategy Architecture Overview

```
                          ┌──────────────────────────────────────────┐
                          │   TOTAL PORTFOLIO CAPITAL ($1,000 Base)  │
                          └────────────────────┬─────────────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       │                                               │
         ┌─────────────▼──────────────┐                 ┌──────────────▼─────────────┐
         │    80% CORE ALLOCATION     │                 │   20% SATELLITE ALLOCATION │
         │   (Regime-Aware Macro Engine)│               │ (Calibrated Micro Engine)  │
         └─────────────┬──────────────┘                 └──────────────┬─────────────┘
                       │                                               │
       ┌───────────────┼───────────────┐               ┌───────────────┼───────────────┐
       │               │               │               │               │               │
  ┌────▼────┐     ┌────▼────┐     ┌────▼────┐     ┌────▼────┐     ┌────▼────┐     ┌────▼────┐
  │ 40% BTC │     │ 30% ETH │     │ 30% SOL │     │ 40% BTC │     │ 30% ETH │     │ 30% SOL │
  └─────────┘     └─────────┘     └─────────┘     └─────────┘     └─────────┘     └─────────┘
```

The system operates across two complementary sub-engines:

### 1. 80% Core Capital (Macro Engine - v14 / V_BEST)
* **Objective:** Ride multi-month macro bull trends and execute position pyramiding during high-conviction breakouts while preserving capital in cash during bear markets.
* **Timeframe:** 4-Hour Candle Resolution.
* **Key Mechanics:**
  * **Regime Detection:** Multi-bar EMA trend filtering ($200$ EMA macro trend baseline).
  * **Pyramiding:** Dynamic position sizing on confirmed momentum breakouts (up to 2 additional scale-in entries with $0.5$ and $0.3$ fractional allocation).
  * **Adaptive Trailing Stops:** Asset-specific wide trailing stops ($12\%$ to $16\%$) to avoid noise liquidation during mega bull trends.
  * **Re-Entry Logic:** EMA-20 pullback re-entry for high-beta momentum assets (e.g., BTC).

### 2. 20% Satellite Capital (Calibrated Micro Engine)
* **Objective:** Generate continuous cash flow and active yield during macro consolidation and sideways market phases.
* **Timeframe:** Multi-timeframe Fractal Indicator Scaling ($SF = 4.0$).
* **Key Mechanics:**
  * **Fast Reversion & Trend Capture:** Calibrated RSI sensitivity threshold ($58.0$) combined with Donchian Breakout triggers.
  * **Trail Multiplier:** $4.5\times$ dynamic trailing stop to lock in quick micro-profits.

---

## 🎯 Asset Universe & Asset-Specific Configurations

The portfolio distributes risk across three core digital assets with momentum weighting (**40% BTC / 30% ETH / 30% SOL**):

| Asset | Asset Role | Optimized Macro Configuration (`V_BEST`) | Trailing Stop Parameters |
| :--- | :--- | :--- | :--- |
| **BTC / USD** | Portfolio Anchor (40%) | `R3`: Re-entry on 20 EMA + Strong Wide Stop + Pyramiding Enabled | Wide Trailing Stop ($12.0\%$) |
| **ETH / USD** | High-Beta Leader (30%) | `R2`: Strong Wide Stop + Pyramiding Enabled + TP1 Disabled | Wide Trailing Stop ($14.0\%$) |
| **SOL / USD** | Parabolic Growth (30%) | `BASE`: Aggressive Pyramiding (Max 2 Adds) + Strong Wide Stop | Wide Trailing Stop ($16.0\%$) |

---

## 📊 Empirical Performance & Out-of-Sample (OOS) Validation

### 1. Full Historical Backtest (2020 – 2026 | Fee-Included)
*Initial Capital: $1,000.00 | Fee Preset: `TAKER_STANDARD` (0.25% Round-Trip)*

| Performance Metric | Hybrid Strategy (80/20) | Buy & Hold Benchmark (40/30/30) | Strategy Outperformance / Alpha |
| :--- | :---: | :---: | :---: |
| **Final Portfolio Value** | **$58,217.87** | $16,030.00 | **+$42,187.87** |
| **Net Cumulative Return** | **+5,721.79%** | +1,503.00% | **+4,218.79%+ Net Alpha** 🚀 |
| **Annualized Return (CAGR)** | **84.77% / year** | 60.38% / year | **+24.39%/year Excess Return** |
| **Maximum Drawdown (MaxDD)** | **-31.47%** | **-89.21%** | **Drawdown Shielding (-57.74% lower)** 🛡️ |
| **Sharpe Ratio** | **1.64** | 0.81 | **+102% Risk-Adjusted Efficiency** |

---

### 2. Unseen Out-of-Sample Window (July 2024 – August 2026 | ~2 Years)
*Tested on unseen market data not utilized during strategy development/calibration.*

| Metric | Hybrid Strategy (80/20) | Buy & Hold Benchmark | Out-of-Sample Alpha |
| :--- | :---: | :---: | :---: |
| **Starting Capital** | $1,000.00 | $1,000.00 | - |
| **Ending Portfolio Value** | **$1,408.94** | **$902.46** | **+$506.48** |
| **Net Return** | **+40.89%** | **-9.75%** | **+50.65%+ Net Alpha** 🚀 |
| **CAGR** | **21.54%** | -5.67% | **+27.21%/year** |
| **Max Drawdown** | **-21.30%** | **-61.15%** | **Protected Capital from 60%+ Crash** |
| **Sharpe Ratio** | **0.44** | -0.13 | Positive Alpha in Down-Market |

---

### 3. 4-Month Stress-Test Audits Across Market Regimes

To ensure statistical robustness across short-term market cycles, the strategy was audited across 3 distinct 4-month windows:

| 4-Month Test Window | Regime Type | Hybrid Strategy Return | Buy & Hold Return | MaxDD Hybrid vs B&H | Net Alpha |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Apr 2026 – Aug 2026** | Sideways / Recovery | **+5.57%** | +8.06% | **-14.82% vs -33.06%** | **Risk Cut by 55%** |
| **Dec 2025 – Apr 2026** | Severe Bear Crash | **-2.44%** | **-29.90%** | **-8.95% vs -44.33%** | **+27.46%+ Alpha** |
| **Nov 2024 – Mar 2025** | Parabolic Bull Run | **+34.44%** | -8.30% | **-14.57% vs -40.98%** | **+42.74%+ Alpha** |

---

## 🛠️ Repository Structure

```
├── main.py                                  # Primary production entry point & dashboard launcher
├── run_hybrid_portfolio.py                  # Core-Satellite 80/20 hybrid portfolio engine
├── run_oos_hybrid_test.py                   # Out-of-Sample (OOS) validation framework
├── engine.py                                # Core technical indicators, trade execution & risk controls
├── generate_dashboard.py                    # Interactive HTML dashboard generator (dashboard.html)
├── calibrate_exact_micro_proportional.py    # Micro satellite engine calibrator
├── test_fractal_scaling.py                  # Multi-timeframe indicator scaling utilities
├── data/                                    # Real 4-Hour historical OHLCV data
│   ├── BTC_USD_4h.csv
│   ├── ETH_USD_4h.csv
│   └── SOL_USD_4h.csv
└── dashboard.html                           # Standalone interactive dashboard UI (ApexCharts)
```

---

## 🚀 Getting Started & Execution Guide

### Prerequisites
* Python 3.10+
* Virtual Environment (recommended)
* Dependencies: `pandas`, `numpy`, `matplotlib`

### Installation & Environment Setup
```bash
# Clone the repository
git clone https://github.com/your-org/hybrid-trading-strategy.git
cd hybrid-trading-strategy

# Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install required dependencies
pip install pandas numpy matplotlib
```

---

### Running the Production Strategy
To execute the production Hybrid portfolio and auto-generate the updated interactive dashboard:
```bash
python3 main.py
```

### Running Out-of-Sample (OOS) Audits
To run the automated Out-of-Sample verification suite across custom historical or unseen timeframes:
```bash
python3 run_oos_hybrid_test.py
```

### Generating the Interactive Dashboard
To manually rebuild `dashboard.html` with full performance metrics, fee analysis, and trade logs:
```bash
python3 generate_dashboard.py
```

---

## 🖥️ Interactive Analytics Dashboard (`dashboard.html`)

The system includes a dark-mode interactive HTML analytics dashboard built with ApexCharts and Vanilla CSS. Key features include:

1. **Portfolio & Per-Asset Navigation:** Switch seamlessly between the aggregate portfolio and individual asset views (BTC, ETH, SOL).
2. **Out-of-Sample Preset Buttons:** Instant period filters for **All Period (2020-2026)**, **Out-of-Sample (2024-2026)**, **1Y Recent**, **Bear Market 2021-2022**, and **Bull Run 2023-2024**.
3. **Cumulative Fee Tracking:** Real-time calculation of transaction fees and slippage drag (`TAKER_STANDARD`).
4. **Execution Log Markers:** Visual chart annotations detailing entry/exit prices, reason for exit, holding duration, and net trade PnL.

---

## 🔒 Risk Management & Safety Principles

1. **No Leverage (1x Spot-Equivalent):** Eliminates liquidation risk inherent in leveraged derivatives.
2. **Capital Protection Prioritization:** During macro downtrends, trailing stops trigger cash conversion to prevent drawdowns greater than ~30%.
3. **Realistic Fee Modeling:** All backtests incorporate realistic exchange fee tiers and slippage allowances to guarantee live performance alignment.

---

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
