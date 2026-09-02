# 🏆 Hybrid Core-Satellite (80/20) Quantitative Trading System

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-Production--Ready-gold.svg)]()

An institutional-grade, multi-asset quantitative trading engine engineered for digital assets (**Bitcoin**, **Ethereum**, and **Solana**). The architecture utilizes an **80/20 Core-Satellite allocation strategy** with **Quarterly Portfolio Rebalancing** designed to capture multi-month parabolic bull runs while providing continuous yield and severe drawdown mitigation during sideways and bear market regimes.

All backtest metrics and out-of-sample audits strictly account for realistic real-world transaction friction (**`TAKER_STANDARD` fee preset: 0.125% per side = 0.25% round-trip cost**).

---

## 🎯 Quantitative Philosophy & Mission

This system was engineered to solve the fundamental flaw of traditional cryptocurrency investing: **catastrophic drawdowns during bear market regimes**. While passive Buy & Hold exposes capital to **-60% to -89% drawdowns**, this quantitative framework prioritizes **drawdown shielding, capital protection, and consistent risk-adjusted alpha**.

---

## 🏛️ Strategy Framework Overview

```
                          ┌──────────────────────────────────────────┐
                          │   TOTAL PORTFOLIO CAPITAL ($2,000 Base)  │
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

1. **80% Core Macro Allocation:**
   - Designed to ride multi-month parabolic bull trends while exiting cleanly into cash when market regimes flip bearish (using 200-period EMA baselines and dynamic Donchian channels).
   - Utilizes position pyramiding on confirmed breakouts and calibrated ATR trailing stops (~3.5x to 4.5x ATR).

2. **20% Satellite Micro Allocation:**
   - Designed to harvest active yield during macro consolidation and sideways regimes using high-frequency fractal indicator scaling and dynamic RSI reversion.

3. **Systematic Quarterly Portfolio Rebalancing:**
   - Rebalances assets (40% BTC / 30% ETH / 30% SOL) and Core/Satellite splits every quarter (`QE` frequency) to eliminate concentration drift and lock in profits.

---

## 📊 Proven Empirical Out-of-Sample Performance

### Pure Out-of-Sample Results (2024-04 $\rightarrow$ 2026-08 - Unseen Test Window):
* **Initial Capital:** $2,000.00
* **Strategy Final Value:** **$2,708.06** (**+35.40% Net Return**)
* **Buy & Hold Final Value:** **$1,243.37** (**-37.83% Loss**)
* **Net Profit Advantage over Hold:** **+$1,464.69 Extra Profit** (**+73.23% Net Alpha**)
* **Max Drawdown:** **-27.39%** (vs Buy & Hold **-68.89%**)
* **Sharpe Ratio:** **0.60** (vs Buy & Hold **-0.28**)

---

## 🛠️ Repository Structure

```
├── main.py                                  # Primary production entry point & launcher
├── run_hybrid_portfolio.py                  # Core-Satellite 80/20 hybrid portfolio engine
├── run_true_oos_validation.py               # Strict leak-free Out-of-Sample validation runner
├── engine.py                                # Core technical indicators, trade execution & risk controls
├── micro_engine.py                          # Micro strategy satellite indicator engine
├── generate_dashboard.py                    # Interactive HTML dashboard generator (dashboard.html)
├── tests/                                   # Pytest unit & regression test suite
│   └── test_engine.py
├── data/                                    # Real 4-Hour historical OHLCV data
│   ├── BTC_USD_4h.csv
│   ├── ETH_USD_4h.csv
│   └── SOL_USD_4h.csv
├── requirements.txt                         # Pinned dependency requirements
├── Makefile                                 # Clean automation targets
├── LICENSE                                  # MIT License
└── dashboard.html                           # Standalone interactive dashboard UI (ApexCharts)
```

---

## 🚀 Getting Started & Execution Guide

### Installation & Setup
```bash
# Clone repository
git clone https://github.com/aboglion/trade.git
cd trade

# Create virtual environment and install requirements
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Running Automated Test Suite
```bash
make test
```

### Running Genuine Leak-Free OOS Validation
```bash
make oos
```

### Generating Production Interactive Dashboard
```bash
make dashboard
```

### Cleaning Temporary Bytecode & Caches
```bash
make clean
```

---

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
