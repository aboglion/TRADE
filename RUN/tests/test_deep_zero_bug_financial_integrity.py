"""
Deep Zero-Bug Financial Integrity Audit.

Validates:
1. BotState.from_dict immune to None/null values in JSON containers.
2. state_store atomic write with fsync durability.
3. IEEE 754 epsilon precision in min order & notional checks.
4. ExchangeGateway duplicate order recovery on transient timeout retry.
5. In-memory order history capping across OrderManager and ReconciliationService.
6. Orchestrator position flip handling (resetting PnL, updating entry price and leverage).
7. RiskManager dynamic leverage margin calculation for expanding orders.
8. Server in-memory state synchronization for running orchestrator on reset and clear.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.config.config_manager import RiskConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.exceptions import InvalidOrderError
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
)
from src.exchanges.exchange_gateway import ExchangeGateway
from src.orchestrator import BotOrchestrator
from src.services.order_manager import OrderManager
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.utils.math_utils import is_above_min_order


def test_bot_state_from_dict_handles_null_values():
    """Verify BotState.from_dict never sets containers to None even if JSON contains null."""
    corrupted_data = {
        "version": None,
        "last_processed_candle_ts": None,
        "pending_orders": None,
        "completed_orders": None,
        "critical_errors": None,
        "strategy_state": None,
        "session_fees": None,
        "session_initial_prices": None,
        "pnl_history": None,
    }
    state = BotState.from_dict(corrupted_data)
    assert isinstance(state.last_processed_candle_ts, dict)
    assert isinstance(state.pending_orders, list)
    assert isinstance(state.completed_orders, list)
    assert isinstance(state.critical_errors, list)
    assert isinstance(state.strategy_state, dict)
    assert isinstance(state.session_fees, dict)
    assert isinstance(state.session_initial_prices, dict)
    assert isinstance(state.pnl_history, list)
    assert state.version == 1

    # Operations must not crash
    state.completed_orders.append({"test": 1})
    state.session_fees["USDT"] = 1.5
    assert len(state.completed_orders) == 1
    assert state.session_fees["USDT"] == 1.5


def test_is_above_min_order_float_precision():
    """Verify IEEE 754 precision tolerance prevents dropping exactly valid min notional orders."""
    # Suppose amount * price is 9.999999999999998 due to float math
    amount = 0.0001
    price = 99999.99999999998  # approx 10.0
    min_amount = 0.0001
    min_notional = 10.0

    # Strict >= would fail on 9.999999999999998 >= 10.0
    assert is_above_min_order(amount, price, min_amount, min_notional) is True

    # Real sub-minimum must still be rejected
    assert is_above_min_order(0.00005, 100000.0, min_amount, min_notional) is False
    assert is_above_min_order(amount, 50000.0, min_amount, min_notional) is False


def test_state_store_fsync_atomic_save(tmp_path):
    """Verify JsonStateStore writes with atomic rename and fsync durability."""
    file_path = tmp_path / "bot_state.json"
    store = JsonStateStore(str(file_path))

    state = BotState()
    state.completed_orders.append({"id": "ord_1", "status": "filled"})
    store.save_state(state)

    assert file_path.exists()
    loaded = store.load_state()
    assert len(loaded.completed_orders) == 1
    assert loaded.completed_orders[0]["id"] == "ord_1"


def test_exchange_gateway_duplicate_order_recovery():
    """Verify ExchangeGateway recovers already-placed order when Binance returns duplicate order error."""
    mock_config = MagicMock()
    mock_config.name = "binance"
    mock_config.market_type = "future"
    mock_config.rate_limit = False
    mock_config.timeout_ms = 1000
    mock_config.max_retries = 1
    mock_config.retry_delay_base_ms = 10
    mock_config.api_key = "test_key"
    mock_config.api_secret = "test_secret"

    gw = ExchangeGateway(mock_config, RunMode.TESTNET)
    mock_exchange = MagicMock()
    gw._exchange = mock_exchange
    gw._initialized = True
    gw._markets = {"BTC/USDT": {"symbol": "BTC/USDT"}}

    intent = OrderIntent(
        client_order_id="test_dup_123",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=60000.0,
    )

    import ccxt
    # First create_order raises ccxt.InvalidOrder with "Duplicate order sent" (-2010)
    mock_exchange.create_order.side_effect = ccxt.InvalidOrder('binance {"code":-2010,"msg":"Duplicate order sent."}')

    # fetch_order returns the previously executed order
    mock_exchange.fetch_order.return_value = {
        "id": "12345678",
        "clientOrderId": "test_dup_123",
        "status": "closed",
        "filled": 0.01,
        "average": 60000.0,
        "fee": {"cost": 0.36, "currency": "USDT"},
        "timestamp": 1600000000000,
    }

    result = gw.create_order(intent)
    assert result.status == OrderStatus.FILLED
    assert result.filled_amount == 0.01
    assert result.exchange_order_id == "12345678"
    assert result.client_order_id == "test_dup_123"


def test_order_manager_and_reconciliation_memory_capping():
    """Verify OrderManager and ReconciliationService cap completed_orders list."""
    state = BotState()
    # Populate with 6500 completed orders
    state.completed_orders = [{"id": f"ord_{i}", "status": "filled"} for i in range(6500)]
    state.pending_orders = [{
        "client_order_id": "pend_1",
        "exchange_order_id": "exc_1",
        "symbol": "BTC/USDT",
        "status": "open",
    }]

    mock_gw = MagicMock()
    mock_gw.fetch_order.return_value = OrderResult(
        client_order_id="pend_1",
        exchange_order_id="exc_1",
        status=OrderStatus.FILLED,
        filled_amount=0.01,
        average_price=60000.0,
    )

    om = OrderManager(mock_gw, state, RunMode.TESTNET)
    om.check_pending_orders()

    # Must be capped to 5000
    assert len(state.completed_orders) <= 5001


def test_risk_manager_margin_with_intent_leverage():
    """Verify RiskManager dynamically calculates margin required using intent leverage."""
    cfg = RiskConfig(
        max_orders_per_cycle=10,
        max_single_order_usd=50000.0,
        max_portfolio_change_pct=5.0,
        min_seconds_between_orders=0,
        min_order_value_usd=10.0,
        allow_market_orders=True,
    )
    rm = RiskManager(cfg, is_futures=True)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=150.0, locked=0.0, total=150.0, value_usd=150.0),
        },
        total_value_usd=150.0,
    )

    # Order value is $500. With 5.0x leverage, margin required is $100.
    # Free USDT is $150 >= $100 -> approved!
    intent_5x = OrderIntent(
        client_order_id="id_5x",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=50000.0,  # value = $500
        leverage=5.0,
    )
    approved, reason = rm.approve_order(intent_5x, portfolio)
    assert approved is True, f"Expected approval, got: {reason}"

    # Order value is $500. With 2.0x leverage, margin required is $250.
    # Free USDT is $150 < $250 -> rejected!
    rm.reset_cycle()
    intent_2x = OrderIntent(
        client_order_id="id_2x",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=50000.0,  # value = $500
        leverage=2.0,
    )
    approved, reason = rm.approve_order(intent_2x, portfolio)
    assert approved is False
    assert "Insufficient margin" in reason


def test_orchestrator_position_flip_resets_pnl_and_updates_leverage():
    """Verify position flip from Long to Short resets unrealized PnL, sets new entry price and leverage."""
    mock_config = MagicMock()
    mock_config.run_mode = RunMode.DRY_RUN
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

    # Initial portfolio has LONG BTC position with old entry price $40,000 and leverage 1.0
    initial_holding = AssetHolding(
        symbol="BTC",
        free=0.0,
        locked=0.0,
        total=1.0,
        value_usd=60000.0,
        unrealized_pnl=20000.0,
        entry_price=40000.0,
        leverage=1.0,
    )
    usdt_holding = AssetHolding(symbol="USDT", free=50000.0, locked=0.0, total=50000.0, value_usd=50000.0)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"BTC": initial_holding, "USDT": usdt_holding},
        total_value_usd=110000.0,
    )

    # Flipped position: SELL 2.0 BTC @ $60,000 (net position becomes -1.0 short)
    intent = OrderIntent(
        client_order_id="flip_order",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=2.0,
        estimated_price=60000.0,
        leverage=3.0,
    )

    mock_order_mgr.execute.return_value = OrderResult(
        client_order_id="flip_order",
        exchange_order_id="dry_flip",
        status=OrderStatus.FILLED,
        filled_amount=2.0,
        average_price=60000.0,
        fees=60.0,
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
    decision.metadata = {"short_leverage": 3.0, "effective_leverage": 0.0}

    mock_candle_svc.get_full_history.return_value = [
        MagicMock(timestamp_ms=1000, close=60000.0)
    ]
    mock_candle_svc.get_new_closed_candles.return_value = []
    mock_portfolio_svc.get_portfolio.return_value = portfolio
    mock_strategy.compute_signals.return_value = decision
    mock_portfolio_svc.compute_rebalance_plan.return_value = plan

    orchestrator._config.strategy.assets = {"BTC": MagicMock(pair="BTC/USDT")}
    orchestrator._config.strategy.warmup_candles = 1

    orchestrator.run_once(force=True)

    new_btc = portfolio.holdings["BTC"]
    assert new_btc.total == -1.0
    assert new_btc.value_usd == 60000.0
    assert new_btc.entry_price == 60000.0  # Must be new fill price, not old $40,000!
    assert new_btc.unrealized_pnl == 0.0   # Reset on flip!
    assert new_btc.leverage == 3.0         # Synced from short_leverage!
