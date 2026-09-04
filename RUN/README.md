# Dynamic Regime-Adaptive 2.0x — Live Trading Bot

Production-grade, object-oriented live trading bot for cryptocurrency trading on Binance using CCXT.

## Key Features
- **4-Hour REST Polling**: Operates on closed 4H candles (aligned to 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
- **Macro Regime Switching**: BTC SMA-150 determines Bull vs Bear regime.
- **Spot-Safe Mode**: In Bear regime, holds 100% USDT (no spot shorting risk).
- **Zero Decisions Safety**: State persistence, idempotency via `clientOrderId`, crash recovery, pre-trade risk gating, reconciliation.
- **4 Operating Modes**: `BACKTEST`, `DRY_RUN`, `TESTNET`, `LIVE`.

## Directory Structure
```
RUN/
├── main.py                     # Primary entry point
├── config.example.yaml         # Configuration template
├── .env.example                # Environment variable template
├── Makefile                    # Command shortcuts
├── requirements.txt            # Python dependencies
├── src/
│   ├── core/                   # Enums, models, exceptions, interfaces
│   ├── config/                 # Config loader & validation
│   ├── exchanges/              # CCXT gateway & dry-run simulator
│   ├── data/                   # Candle fetching & gap validation
│   ├── strategy/               # Indicator calculation & regime signals
│   ├── services/               # Portfolio, order manager, risk, reconciliation, state
│   ├── utils/                  # Logging, time, math helpers
│   └── orchestrator.py         # Cycle coordinator
├── scripts/                    # Utility CLI scripts
└── tests/                      # Pytest unit & integration suite
```

## Quick Start

### 1. Setup Environment
```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

### 2. Run Dry Run Cycle
```bash
python3 main.py --mode DRY_RUN --once
```

### 3. Run Test Suite
```bash
pytest tests -v
```

### 4. Check Portfolio Balances
```bash
python3 scripts/show_balances.py
```
