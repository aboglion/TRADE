"""
Tests for orchestrator — single cycle execution, error handling, dry-run integration.
"""

import time
import pytest
from tests import make_candle_series
from src.config.config_manager import BotConfig, ExchangeConfig, RiskConfig, SchedulerConfig, StateConfig, StrategyConfig
from src.core.enums import Regime, RunMode
from src.core.models import BotState
from src.data.candle_service import CandleService
from src.exchanges.dry_run_exchange import DryRunExchange
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


class FakeProvider:
    """Fake provider supplying candles per symbol."""
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


@pytest.fixture
def orchestrator_setup(tmp_path):
    base_ts = 1_600_000_000_000
    # Create 350 candles for BTC, ETH, SOL
    btc_candles = make_candle_series(base_ts, count=350, base_price=50000.0, trend=10.0)
    eth_candles = make_candle_series(base_ts, count=350, base_price=3000.0, trend=1.0)
    sol_candles = make_candle_series(base_ts, count=350, base_price=100.0, trend=0.1)

    candle_dict = {
        "BTC/USDT": btc_candles,
        "ETH/USDT": eth_candles,
        "SOL/USDT": sol_candles,
    }

    provider = FakeProvider(candle_dict)
    gateway = DryRunExchange(initial_balances={"USDT": 10000.0})
    for sym in candle_dict:
        gateway.set_price(sym, candle_dict[sym][-1].close)

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        state=StateConfig(path=str(tmp_path / "bot_state.json")),
    )

    candle_service = CandleService(provider, timeframe="4h", warmup_candles=300)
    portfolio_service = PortfolioService(gateway)
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

    return orchestrator, gateway, candle_dict


class TestOrchestratorCycle:
    def test_run_once_completes_successfully(self, orchestrator_setup):
        orchestrator, gateway, candle_dict = orchestrator_setup
        success = orchestrator.run_once()
        assert success is True
        assert orchestrator._state.last_run_ts is not None

    def test_state_persisted_after_cycle(self, orchestrator_setup):
        orchestrator, gateway, candle_dict = orchestrator_setup
        orchestrator.run_once()
        # Verify state file exists and can be loaded
        loaded_state = orchestrator._state_store.load_state()
        assert loaded_state.last_run_ts is not None
        assert loaded_state.last_cycle_success is True

    def test_timing_check_on_startup_does_not_warn(self, orchestrator_setup, caplog):
        orchestrator, gateway, candle_dict = orchestrator_setup
        import logging
        caplog.set_level(logging.WARNING)
        success = orchestrator.run_once()
        assert success is True
        # First run must never report timing anomaly
        assert "SCAN TIMING ALERT" not in caplog.text

    def test_timing_check_rapid_cycle_emits_warning(self, orchestrator_setup, caplog):
        orchestrator, gateway, candle_dict = orchestrator_setup
        import logging
        caplog.set_level(logging.WARNING)
        orchestrator.run_once()
        caplog.clear()

        # Second run immediately (< 5 minutes)
        orchestrator.run_once()
        assert "SCAN TIMING ALERT" in caplog.text
        assert "< 5m" in caplog.text

    def test_timing_check_forced_cycle_does_not_warn(self, orchestrator_setup, caplog):
        orchestrator, gateway, candle_dict = orchestrator_setup
        import logging
        caplog.set_level(logging.WARNING)
        orchestrator.run_once()
        caplog.clear()

        # Forced run immediately should NOT trigger timing warning
        orchestrator.run_once(force=True)
        assert "SCAN TIMING ALERT" not in caplog.text

    def test_idle_cycle_clean_execution(self, orchestrator_setup, caplog):
        orchestrator, gateway, candle_dict = orchestrator_setup
        import logging
        # First run processes available candles
        orchestrator.run_once()
        caplog.clear()

        # Second run has no new candles
        caplog.set_level(logging.INFO)
        success = orchestrator.run_once()
        assert success is True
        # Should not spam CYCLE START banner when no new closed candles
        assert "CYCLE START" not in caplog.text
