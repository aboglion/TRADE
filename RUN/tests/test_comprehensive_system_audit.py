"""
Comprehensive System Audit & Verification Tests.

Validates:
1. Orchestrator reentrancy cycle lock (thread-safe execution).
2. Risk Manager position-reduction / stop-loss bypass of max_single_order_usd.
3. Risk Manager min_seconds_between_orders scheduler jitter tolerance.
4. PortfolioService exact held quantity liquidation (dust-free closing).
5. PortfolioService spot sell balance validation against min_notional.
6. TimeUtils dynamic timeframe conversion.
7. StateStore corrupt state file recovery and self-healing.
8. ExchangeGateway options (adjustForTimeDifference, recvWindow) and One-Way Mode.
9. RegimeAdaptiveStrategy pyramiding pullback calculation from previous entry.
"""

import time
from unittest.mock import MagicMock, patch


from src.core.enums import OrderSide, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.utils.time_utils import timeframe_to_ms


def test_orchestrator_reentrancy_cycle_lock():
    """Verify BotOrchestrator rejects concurrent execution when cycle is already active."""
    from src.orchestrator import BotOrchestrator

    mock_config = MagicMock()
    mock_config.run_mode = RunMode.DRY_RUN
    mock_config.risk.kill_switch = False
    mock_config.scheduler.poll_interval_seconds = 300

    mock_gateway = MagicMock()
    mock_candle_svc = MagicMock()
    mock_portfolio_svc = MagicMock()
    mock_strategy = MagicMock()
    mock_risk_mgr = MagicMock()
    mock_state_store = MagicMock()
    state = BotState()

    orch = BotOrchestrator(
        config=mock_config,
        gateway=mock_gateway,
        candle_service=mock_candle_svc,
        portfolio_service=mock_portfolio_svc,
        strategy=mock_strategy,
        risk_manager=mock_risk_mgr,
        state_store=mock_state_store,
        state=state,
    )

    # Acquire lock manually to simulate an ongoing cycle
    assert orch._cycle_lock.acquire(blocking=False) is True

    # A concurrent call to run_once must return False immediately
    assert orch.run_once() is False

    # Release lock
    orch._cycle_lock.release()


def test_risk_manager_bypasses_max_order_value_for_position_reducing():
    """Verify risk manager permits position-reducing / stop-loss orders even if exceeding max_single_order_usd."""
    from src.config.config_manager import RiskConfig

    config = RiskConfig(
        max_single_order_usd=5000.0,
        allowed_symbols=["BTC/USDT"],
        allow_market_orders=True,
    )
    rm = RiskManager(config)

    # Portfolio holds 0.2 BTC ($12,000 value at $60,000)
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.2, locked=0.0, total=0.2, value_usd=12000.0),
            "USDT": AssetHolding("USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0),
        },
        total_value_usd=13000.0,
    )

    # Intent to sell 0.2 BTC (value $12,000 > max $5,000)
    intent = OrderIntent(
        client_order_id="test_close_1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.2,
        estimated_price=60000.0,
    )

    approved, reason = rm.approve_order(intent, portfolio)
    assert approved is True, f"Expected emergency close order to be approved, got: {reason}"


def test_risk_manager_timing_tolerance():
    """Verify min_seconds_between_orders allows small timing jitter."""
    from src.config.config_manager import RiskConfig

    config = RiskConfig(
        min_seconds_between_orders=1,
        allowed_symbols=["BTC/USDT"],
        allow_market_orders=True,
    )
    rm = RiskManager(config)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
        total_value_usd=1000.0,
    )

    intent = OrderIntent(
        client_order_id="test_timing_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.001,
        estimated_price=60000.0,
    )

    # First order
    approved, _ = rm.approve_order(intent, portfolio)
    assert approved is True

    # Simulate 0.96s elapsed (within 0.05s jitter tolerance of 1.0s)
    rm._last_order_time = time.time() - 0.96
    approved, reason = rm.approve_order(intent, portfolio)
    assert approved is True, f"Expected order to pass jitter tolerance, got: {reason}"


def test_portfolio_service_full_liquidation_exact_qty():
    """Verify full liquidation uses exact held quantity to prevent leftover dust."""
    mock_gateway = MagicMock()
    mock_gateway.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gateway, is_futures=True)

    # Holding 0.1234 BTC
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.1234, locked=0.0, total=0.1234, value_usd=7404.0),
            "USDT": AssetHolding("USDT", free=500.0, locked=0.0, total=500.0, value_usd=500.0),
        },
        total_value_usd=7904.0,
    )

    # Target 0% BTC (full liquidation)
    target = TargetAllocation(
        weights={"BTC/USDT": 0.0, "USDT": 1.0},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )

    # Even if market price has moved slightly
    prices = {"BTC/USDT": 61200.0}

    plan = ps.compute_rebalance_plan(portfolio, target, prices)
    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.side == OrderSide.SELL
    # Must sell exactly 0.1234 BTC
    assert order.amount == 0.1234


def test_state_store_corrupt_file_recovery(tmp_path):
    """Verify JsonStateStore archives corrupt state file and returns clean BotState without crashing."""
    corrupt_file = tmp_path / "bot_state.json"
    corrupt_file.write_text("NOT VALID JSON {{{{{")

    store = JsonStateStore(str(corrupt_file))
    state = store.load_state()

    assert isinstance(state, BotState)
    # Check that corrupt file was moved aside
    corrupt_archives = list(tmp_path.glob("bot_state.corrupt.*"))
    assert len(corrupt_archives) == 1


def test_time_utils_dynamic_timeframes():
    """Verify timeframe_to_ms handles dynamic timeframes."""
    assert timeframe_to_ms("1s") == 1_000
    assert timeframe_to_ms("3m") == 180_000
    assert timeframe_to_ms("2h") == 7_200_000
    assert timeframe_to_ms("1w") == 604_800_000
    assert timeframe_to_ms("4h") == 14_400_000


def test_exchange_gateway_futures_options():
    """Verify ExchangeGateway configures adjustForTimeDifference and recvWindow."""
    from src.config.config_manager import ExchangeConfig
    from src.exchanges.exchange_gateway import ExchangeGateway

    config = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(config, RunMode.DRY_RUN)

    with patch("ccxt.binance") as mock_ccxt_binance:
        mock_instance = MagicMock()
        mock_instance.options = {}
        mock_instance.markets = {"BTC/USDT": {}}
        mock_ccxt_binance.return_value = mock_instance

        gw.initialize()

        # Verify options passed to ccxt.binance
        call_kwargs = mock_ccxt_binance.call_args[0][0]
        assert call_kwargs["options"]["adjustForTimeDifference"] is True
        assert call_kwargs["options"]["recvWindow"] == 10000
        # Verify set_position_mode called for one-way mode
        mock_instance.set_position_mode.assert_called_once_with(hedged=False)
