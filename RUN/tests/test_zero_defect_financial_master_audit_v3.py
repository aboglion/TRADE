import time
import pytest
from unittest.mock import MagicMock

from tests import make_candle_series
from src.core.enums import RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Regime,
)
from src.orchestrator import BotOrchestrator
from src.services.order_manager import OrderManager
from src.services.reconciliation_service import ReconciliationService
from src.services.portfolio_service import PortfolioService
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy


def test_bot_state_from_dict_numeric_string_safety():
    """Verify that BotState.from_dict casts string numbers to float for safe arithmetic."""
    raw_dict = {
        "version": 1,
        "is_running": True,
        "session_initial_value_usd": "10500.75",
        "session_fees": {"USDT": "15.50", "BNB": "0.05"},
        "session_initial_prices": {"BTC/USDT": "65000.50", "ETH/USDT": "3500.25"},
        "last_processed_candle_ts": {"BTC/USDT": 1700000000000},
    }
    state = BotState.from_dict(raw_dict)
    assert isinstance(state.session_initial_value_usd, float)
    assert state.session_initial_value_usd == pytest.approx(10500.75)
    assert state.session_initial_value_usd - 500.0 == pytest.approx(10000.75)

    assert isinstance(state.session_fees["USDT"], float)
    assert state.session_fees["USDT"] + 5.0 == pytest.approx(20.50)
    assert state.session_fees["BNB"] == pytest.approx(0.05)

    assert isinstance(state.session_initial_prices["BTC/USDT"], float)
    assert state.session_initial_prices["BTC/USDT"] == pytest.approx(65000.50)


def test_orchestrator_closed_position_clears_locked_and_value():
    """Verify that in-cycle position closure zeroes out locked and value_usd."""
    mock_config = MagicMock()
    mock_config.run_mode = RunMode.DRY_RUN
    mock_config.exchange.market_type = "spot"
    mock_config.risk.min_seconds_between_orders = 0

    mock_gateway = MagicMock()
    mock_candle_svc = MagicMock()
    mock_portfolio_svc = MagicMock()
    mock_strategy = MagicMock()
    mock_state_store = MagicMock()
    mock_risk = MagicMock()
    state = BotState()

    orchestrator = BotOrchestrator(
        config=mock_config,
        gateway=mock_gateway,
        candle_service=mock_candle_svc,
        portfolio_service=mock_portfolio_svc,
        strategy=mock_strategy,
        risk_manager=mock_risk,
        state_store=mock_state_store,
        state=state,
    )

    mock_order_mgr = MagicMock()
    orchestrator._order_manager = mock_order_mgr

    # Position had 1.0 SOL total with 0.2 locked
    sol_holding = AssetHolding(
        symbol="SOL",
        free=0.8,
        locked=0.2,
        total=1.0,
        value_usd=150.0,
    )
    usdt_holding = AssetHolding(
        symbol="USDT",
        free=1000.0,
        locked=0.0,
        total=1000.0,
        value_usd=1000.0,
    )
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"SOL": sol_holding, "USDT": usdt_holding},
        total_value_usd=1150.0,
    )

    intent = OrderIntent(
        client_order_id="close_sol_test",
        symbol="SOL/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=1.0,
        estimated_price=150.0,
    )
    mock_order_mgr.execute.return_value = OrderResult(
        client_order_id="close_sol_test",
        exchange_order_id="dry_close_sol",
        status=OrderStatus.FILLED,
        filled_amount=1.0,
        average_price=150.0,
        fees=0.15,
        fee_currency="USDT",
    )
    mock_order_mgr.check_open_orders_on_exchange.return_value = []
    mock_order_mgr.check_pending_orders.return_value = []
    mock_risk.is_kill_switch_active.return_value = False
    mock_risk.approve_order.return_value = (True, "")

    plan = MagicMock()
    plan.orders = [intent]
    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"short_leverage": 1.0, "effective_leverage": 0.0}

    mock_candle_svc.get_full_history.return_value = [
        MagicMock(timestamp_ms=1000, close=150.0)
    ]
    mock_candle_svc.get_new_closed_candles.return_value = []
    mock_portfolio_svc.get_portfolio.return_value = portfolio
    mock_strategy.compute_signals.return_value = decision
    mock_portfolio_svc.compute_rebalance_plan.return_value = plan

    orchestrator._config.strategy.assets = {"SOL": MagicMock(pair="SOL/USDT")}
    orchestrator._config.strategy.warmup_candles = 1

    orchestrator.run_once(force=True)

    sol = portfolio.holdings["SOL"]
    assert sol.total == 0.0
    assert sol.free == 0.0
    assert sol.locked == 0.0
    assert sol.value_usd == 0.0


def test_orchestrator_reduce_only_closed_2022_no_phantom_quote():
    """Verify that -2022 recovery (closed_ prefix) zeroes holding without mutating quote balance."""
    mock_config = MagicMock()
    mock_config.run_mode = RunMode.LIVE
    mock_config.exchange.market_type = "future"
    mock_config.risk.min_seconds_between_orders = 0

    mock_gateway = MagicMock()
    mock_candle_svc = MagicMock()
    mock_portfolio_svc = MagicMock()
    mock_strategy = MagicMock()
    mock_state_store = MagicMock()
    mock_risk = MagicMock()
    state = BotState()

    orchestrator = BotOrchestrator(
        config=mock_config,
        gateway=mock_gateway,
        candle_service=mock_candle_svc,
        portfolio_service=mock_portfolio_svc,
        strategy=mock_strategy,
        risk_manager=mock_risk,
        state_store=mock_state_store,
        state=state,
    )

    mock_order_mgr = MagicMock()
    orchestrator._order_manager = mock_order_mgr

    eth_holding = AssetHolding(
        symbol="ETH",
        free=0.0,
        locked=0.0,
        total=2.0,
        value_usd=7000.0,
        entry_price=3500.0,
        leverage=2.0,
    )
    usdt_holding = AssetHolding(
        symbol="USDT",
        free=5000.0,
        locked=0.0,
        total=5000.0,
        value_usd=5000.0,
    )
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"ETH": eth_holding, "USDT": usdt_holding},
        total_value_usd=12000.0,
    )

    intent = OrderIntent(
        client_order_id="close_eth_reduce_only",
        symbol="ETH/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=2.0,
        estimated_price=3500.0,
        reduce_only=True,
    )
    # Return exchange_order_id starting with closed_ (from -2022 handling)
    mock_order_mgr.execute.return_value = OrderResult(
        client_order_id="close_eth_reduce_only",
        exchange_order_id="closed_binance_2022",
        status=OrderStatus.FILLED,
        filled_amount=2.0,
        average_price=3500.0,
        fees=0.0,
        fee_currency="USDT",
    )
    mock_order_mgr.check_open_orders_on_exchange.return_value = []
    mock_order_mgr.check_pending_orders.return_value = []
    mock_risk.is_kill_switch_active.return_value = False
    mock_risk.approve_order.return_value = (True, "")

    plan = MagicMock()
    plan.orders = [intent]
    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"short_leverage": 1.0, "effective_leverage": 0.0}

    mock_candle_svc.get_full_history.return_value = [
        MagicMock(timestamp_ms=1000, close=3500.0)
    ]
    mock_candle_svc.get_new_closed_candles.return_value = []
    mock_portfolio_svc.get_portfolio.return_value = portfolio
    mock_strategy.compute_signals.return_value = decision
    mock_portfolio_svc.compute_rebalance_plan.return_value = plan

    orchestrator._config.strategy.assets = {"ETH": MagicMock(pair="ETH/USDT")}
    orchestrator._config.strategy.warmup_candles = 1

    orchestrator.run_once(force=True)

    eth = portfolio.holdings["ETH"]
    assert eth.total == 0.0
    assert eth.free == 0.0
    # USDT free and total should remain exact 5000.0 (no phantom credit)
    usdt = portfolio.holdings["USDT"]
    assert usdt.free == 5000.0
    assert usdt.total == 5000.0


def test_order_manager_check_pending_orders_2013_not_found():
    """Verify that OrderNotFound / -2013 removes pending order and records it as FAILED."""
    mock_gw = MagicMock()
    state = BotState()
    state.pending_orders = [
        {"client_order_id": "c1", "exchange_order_id": "e123", "symbol": "BTC/USDT", "status": "submitted"}
    ]

    om = OrderManager(gateway=mock_gw, state=state, run_mode=RunMode.LIVE)
    mock_gw.fetch_order.side_effect = Exception("binance Order does not exist (-2013)")

    om.check_pending_orders()

    assert len(state.pending_orders) == 0
    assert len(state.completed_orders) == 1
    assert state.completed_orders[0]["status"] == OrderStatus.FAILED.value
    assert "not found on exchange" in state.completed_orders[0]["error_message"]


def test_order_manager_cancel_stale_orphan_order_tracked():
    """Verify that cancel_stale_open_orders tracks cancelled orphan orders in completed_orders."""
    mock_gw = MagicMock()
    state = BotState()
    state.pending_orders = []

    om = OrderManager(gateway=mock_gw, state=state, run_mode=RunMode.LIVE)

    orphan = MagicMock()
    orphan.exchange_order_id = "orphan_999"
    orphan.client_order_id = None
    orphan.symbol = "SOLUSDT"
    orphan.timestamp_ms = int((time.time() - 400) * 1000)
    mock_gw.fetch_open_orders.return_value = [orphan]

    canceled = om.cancel_stale_open_orders()
    assert canceled == 1
    assert len(state.completed_orders) == 1
    assert state.completed_orders[0]["exchange_order_id"] == "orphan_999"
    assert state.completed_orders[0]["status"] == OrderStatus.CANCELLED.value


def test_reconciliation_service_orphan_parsing_info_fallback():
    """Verify ReconciliationService falls back to info.origQty and info.side."""
    mock_gw = MagicMock()
    mock_gw.fetch_positions.return_value = []
    state = BotState()
    rec = ReconciliationService(gateway=mock_gw, state=state, run_mode=RunMode.LIVE)

    orphan = MagicMock()
    orphan.exchange_order_id = "exc_orphan_777"
    orphan.client_order_id = "cli_orphan_777"
    orphan.symbol = "AVAXUSDT"
    orphan.status = OrderStatus.OPEN
    orphan.filled_amount = 0.0
    orphan.raw_response = {
        "amount": None,
        "side": None,
        "info": {"origQty": "25.5", "side": "SELL"},
    }
    mock_gw.fetch_open_orders.return_value = [orphan]

    rec.reconcile()

    assert len(state.pending_orders) == 1
    added = state.pending_orders[0]
    assert added["amount"] == 25.5
    assert added["side"] == "sell"
    assert added["symbol"] == "AVAX/USDT"


def test_regime_strategy_zero_division_protection():
    """Verify that RegimeAdaptiveStrategy does not crash when prices are flat or zero."""
    strategy = RegimeAdaptiveStrategy(sma_regime_period=150)
    candles = [
        Candle(
            timestamp_ms=1_600_000_000_000 + i * 14_400_000,
            open=0.0 if i == 0 else 50000.0,
            high=50100.0,
            low=0.0 if i == 0 else 49900.0,
            close=50000.0,
            volume=100.0,
            is_closed=True,
        )
        for i in range(200)
    ]
    candles_by_asset = {"BTC/USDT": candles}
    portfolio = PortfolioSnapshot(
        timestamp_ms=1_600_000_000_000 + 200 * 14_400_000,
        holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
        total_value_usd=1000.0,
    )

    decision = strategy.compute_signals(candles_by_asset, portfolio)
    assert decision is not None
    assert decision.regime in (Regime.BULL, Regime.BEAR)


def test_micro_satellite_zero_division_protection():
    """Verify that MicroSatelliteStrategy does not crash on flat or zero volume history."""
    strategy = MicroSatelliteStrategy()
    candles = [
        Candle(
            timestamp_ms=1_600_000_000_000 + i * 14_400_000,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=0.0,
            is_closed=True,
        )
        for i in range(250)
    ]
    candles_by_asset = {"SOL/USDT": candles}
    portfolio = PortfolioSnapshot(
        timestamp_ms=1_600_000_000_000 + 250 * 14_400_000,
        holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
        total_value_usd=1000.0,
    )

    decision = strategy.compute_signals(candles_by_asset, portfolio)
    assert decision is not None
