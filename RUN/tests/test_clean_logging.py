"""
Tests verifying clean logging policy:
- No routine polling/survey spam at INFO level during idle cycles
- Meaningful logs ONLY for:
  1. Faults/errors/exceptions
  2. Final decision to execute actions after all barriers
  3. Git updates
  4. Exchange communication errors and responses
  5. Buy/sell order results
"""

import time
import logging
from tests import make_candle_series
from src.config.config_manager import BotConfig, StateConfig, StrategyConfig, AssetConfig
from src.core.enums import RunMode
from src.core.models import BotState
from src.data.candle_service import CandleService
from src.exchanges.dry_run_exchange import DryRunExchange
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


class MockProvider:
    def __init__(self, candle_dict):
        self._candle_dict = candle_dict

    def fetch_candles(self, symbol, timeframe, since_ms=None, limit=500):
        candles = self._candle_dict.get(symbol, [])
        if since_ms is not None:
            candles = [c for c in candles if c.timestamp_ms >= since_ms]
        if limit:
            candles = candles[:limit]
        return candles

    def fetch_ohlcv_all(self, symbol, timeframe, since_ms=None, limit=1000):
        return self.fetch_candles(symbol, timeframe, since_ms, limit)


def test_idle_cycle_emits_no_survey_spam(tmp_path, caplog):
    """Verify that an idle cycle (no rebalance needed, within threshold) emits no survey spam at INFO level."""
    now_ms = int(time.time() * 1000)
    base_ts = now_ms - (250 * 14_400_000)
    btc_candles = make_candle_series(base_ts, count=249, base_price=50000.0, trend=10.0)
    eth_candles = make_candle_series(base_ts, count=249, base_price=3000.0, trend=1.0)
    sol_candles = make_candle_series(base_ts, count=249, base_price=100.0, trend=0.1)

    candle_dict = {
        "BTC/USDT": btc_candles,
        "ETH/USDT": eth_candles,
        "SOL/USDT": sol_candles,
    }

    provider = MockProvider(candle_dict)
    # Start with 100% cash, matching Safe Haven target (no trades needed)
    gateway = DryRunExchange(initial_balances={"USDT": 10000.0})
    for sym in candle_dict:
        gateway.set_price(sym, candle_dict[sym][-1].close)

    strat_cfg = StrategyConfig(
        assets={
            "BTC": AssetConfig(0.4, "BTC/USDT"),
            "ETH": AssetConfig(0.3, "ETH/USDT"),
            "SOL": AssetConfig(0.3, "SOL/USDT"),
        },
        warmup_candles=200,
        sma_regime_period=150,
    )
    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        state=StateConfig(path=str(tmp_path / "bot_state.json")),
        strategy=strat_cfg,
    )

    candle_service = CandleService(provider, timeframe="4h", warmup_candles=200)
    portfolio_service = PortfolioService(gateway, allow_market_orders=True)
    strategy = RegimeAdaptiveStrategy(
        asset_weights={"BTC": 0.4, "ETH": 0.3, "SOL": 0.3},
        sma_regime_period=150,
        bull_leverage=2.0,
    )
    risk_manager = RiskManager(config.risk)
    state_store = JsonStateStore(config.state.path)
    state = BotState()

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_service,
        portfolio_service=portfolio_service,
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        state=state,
    )

    caplog.clear()
    caplog.set_level(logging.INFO)

    success = orchestrator.run_once()
    assert success is True

    info_text = caplog.text
    # Ensure routine survey logs are NOT logged at INFO
    assert "Found 1 new closed candle" not in info_text
    assert "Portfolio within threshold" not in info_text
    assert "Allocation deviations:" not in info_text
    assert "Imported active position tracking state" not in info_text
    assert "Imported micro position tracking state" not in info_text
    assert "Strategy decision:" not in info_text
    assert "Tactical leverage evaluation:" not in info_text
    assert "CYCLE COMPLETE |" not in info_text


def test_action_decision_and_order_results_logged_at_info(tmp_path, caplog):
    """Verify that when actual trades are executed, action decisions and execution results ARE logged at INFO."""
    now_ms = int(time.time() * 1000)
    base_ts = now_ms - (250 * 14_400_000)
    btc_candles = make_candle_series(base_ts, count=249, base_price=50000.0, trend=10.0)
    eth_candles = make_candle_series(base_ts, count=249, base_price=3000.0, trend=1.0)
    sol_candles = make_candle_series(base_ts, count=249, base_price=100.0, trend=0.1)

    candle_dict = {
        "BTC/USDT": btc_candles,
        "ETH/USDT": eth_candles,
        "SOL/USDT": sol_candles,
    }

    provider = MockProvider(candle_dict)
    # Start with massive BTC balance so rebalance is forced
    gateway = DryRunExchange(initial_balances={"USDT": 1000.0, "BTC": 1.5})
    for sym in candle_dict:
        gateway.set_price(sym, candle_dict[sym][-1].close)

    strat_cfg = StrategyConfig(
        assets={
            "BTC": AssetConfig(0.4, "BTC/USDT"),
            "ETH": AssetConfig(0.3, "ETH/USDT"),
            "SOL": AssetConfig(0.3, "SOL/USDT"),
        },
        warmup_candles=200,
        sma_regime_period=150,
    )
    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        state=StateConfig(path=str(tmp_path / "bot_state.json")),
        strategy=strat_cfg,
    )
    config.risk.allow_market_orders = True
    config.risk.min_seconds_between_orders = 0.0

    candle_service = CandleService(provider, timeframe="4h", warmup_candles=200)
    portfolio_service = PortfolioService(gateway, allow_market_orders=True)
    strategy = RegimeAdaptiveStrategy(
        asset_weights={"BTC": 0.4, "ETH": 0.3, "SOL": 0.3},
        sma_regime_period=150,
        bull_leverage=2.0,
    )
    risk_manager = RiskManager(config.risk)
    state_store = JsonStateStore(config.state.path)
    state = BotState()

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_service,
        portfolio_service=portfolio_service,
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        state=state,
    )

    caplog.clear()
    caplog.set_level(logging.INFO)

    success = orchestrator.run_once()
    assert success is True

    info_text = caplog.text
    # Should log action decision, order execution, and cycle completion
    assert "🎯 ACTION DECISION:" in info_text
    assert "Order executed:" in info_text or "Order result:" in info_text
    assert "CYCLE COMPLETE" in info_text
