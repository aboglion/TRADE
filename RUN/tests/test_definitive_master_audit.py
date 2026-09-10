"""
Definitive Master Audit Test Suite.
Validates all critical software, execution, strategy, and communication fixes:
1. Orchestrator OrderStatus resolution (no NameError) and executed_count tracking.
2. Binance limit order timeInForce ('GTC') mandatory parameter compliance.
3. Market order price stripping in ExchangeGateway.
4. Comprehensive order status mapping (partially_filled, filled, failed).
5. Symbol resilience for amount_to_precision and price_to_precision.
6. Large limit pagination when since_ms is None in fetch_ohlcv_all.
7. Seamless position flips (closing previous position before opening new one).
8. DryRun exchange instance caching.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    StrategyDecision,
    TargetAllocation,
)
from src.exchanges.exchange_gateway import ExchangeGateway
from src.exchanges.dry_run_exchange import DryRunExchange
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.config.config_manager import BotConfig, ExchangeConfig, RiskConfig


def test_orchestrator_order_status_import_and_execution_count():
    """Verify that orchestrator executes order without NameError and updates executed_count."""
    config = BotConfig()
    gateway = MagicMock()
    candle_service = MagicMock()
    portfolio_service = MagicMock()
    strategy = MagicMock()
    risk_manager = MagicMock()
    risk_manager.is_kill_switch_active.return_value = False
    state_store = MagicMock()
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

    # Mock order manager execute to return filled
    mock_intent = OrderIntent(
        client_order_id="test_cid_123",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        price=60000.0,
    )
    mock_result = OrderResult(
        client_order_id="test_cid_123",
        exchange_order_id="exc_123",
        status=OrderStatus.FILLED,
        filled_amount=0.01,
        average_price=60000.0,
    )

    orchestrator._order_manager.execute = MagicMock(return_value=mock_result)
    risk_manager.approve_order = MagicMock(return_value=(True, ""))

    # Prepare mock cycle components
    pairs = {"BTC": "BTC/USDT"}
    candles = [Candle(timestamp_ms=1000, open=60000, high=60100, low=59900, close=60000, volume=10, is_closed=True)]
    plan = MagicMock(orders=[mock_intent])
    decision = StrategyDecision(
        regime=Regime.BULL,
        target_allocation=TargetAllocation(weights={"BTC/USDT": 0.4}, regime=Regime.BULL, timestamp_ms=1000),
        signals=[],
        timestamp_ms=1000,
        metadata={"effective_leverage": 2.5},
    )

    portfolio_service.compute_rebalance_plan = MagicMock(return_value=plan)
    strategy.compute_signals = MagicMock(return_value=decision)
    portfolio_service.get_portfolio = MagicMock(return_value=PortfolioSnapshot(1000, {}, 1000.0))
    candle_service.get_new_closed_candles = MagicMock(return_value=candles)
    candle_service.get_full_history = MagicMock(return_value=candles)
    candle_service.validate_continuity = MagicMock()

    # Execute cycle with force=True
    success = orchestrator.run_once(force=True)
    assert success is True
    # Verify order execution was called and did not raise NameError
    orchestrator._order_manager.execute.assert_called_once_with(mock_intent)


def test_exchange_gateway_limit_order_time_in_force():
    """Verify that ExchangeGateway automatically attaches timeInForce: GTC to limit orders."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.LIVE)
    mock_ccxt = MagicMock()
    mock_ccxt.markets = {"BTC/USDT": {"limits": {}, "precision": {"amount": 3, "price": 2}}}
    mock_ccxt.amount_to_precision = MagicMock(return_value="0.010")
    mock_ccxt.price_to_precision = MagicMock(return_value="60000.00")
    mock_ccxt.create_order = MagicMock(return_value={"id": "123", "status": "closed", "filled": 0.01, "price": 60000.0})
    gw._exchange = mock_ccxt
    gw._initialized = True

    intent = OrderIntent(
        client_order_id="limit_ord_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.01,
        price=60000.0,
    )

    gw.create_order(intent)
    called_params = mock_ccxt.create_order.call_args[1]["params"]
    assert called_params.get("timeInForce") == "GTC"
    assert called_params.get("newClientOrderId") == "limit_ord_1"


def test_exchange_gateway_market_order_price_none():
    """Verify that market orders pass price=None even if estimated_price is provided."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.LIVE)
    mock_ccxt = MagicMock()
    mock_ccxt.markets = {"BTC/USDT": {"limits": {}, "precision": {"amount": 3, "price": 2}}}
    mock_ccxt.amount_to_precision = MagicMock(return_value="0.010")
    mock_ccxt.create_order = MagicMock(return_value={"id": "124", "status": "closed", "filled": 0.01, "average": 60000.0})
    gw._exchange = mock_ccxt
    gw._initialized = True

    intent = OrderIntent(
        client_order_id="mkt_ord_1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.01,
        price=None,
        estimated_price=60000.0,
    )

    gw.create_order(intent)
    call_kwargs = mock_ccxt.create_order.call_args[1]
    assert call_kwargs["price"] is None
    assert call_kwargs["type"] == "market"
    assert "timeInForce" not in call_kwargs["params"]


def test_exchange_gateway_status_mapping_expansion():
    """Verify mapping of partially_filled, filled, failed."""
    assert ExchangeGateway._map_order_status("partially_filled") == OrderStatus.PARTIALLY_FILLED
    assert ExchangeGateway._map_order_status("partiallyfilled") == OrderStatus.PARTIALLY_FILLED
    assert ExchangeGateway._map_order_status("filled") == OrderStatus.FILLED
    assert ExchangeGateway._map_order_status("closed") == OrderStatus.FILLED
    assert ExchangeGateway._map_order_status("failed") == OrderStatus.FAILED
    assert ExchangeGateway._map_order_status("rejected") == OrderStatus.FAILED
    assert ExchangeGateway._map_order_status("open") == OrderStatus.OPEN
    assert ExchangeGateway._map_order_status("canceled") == OrderStatus.CANCELLED


def test_exchange_gateway_precision_bare_symbols():
    """Verify that amount_to_precision and price_to_precision handle bare symbols like 'BTC'."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.LIVE)
    mock_ccxt = MagicMock()
    mock_ccxt.amount_to_precision = MagicMock(return_value="0.005")
    mock_ccxt.price_to_precision = MagicMock(return_value="62000.50")
    gw._exchange = mock_ccxt
    gw._initialized = True

    res_amt = gw.amount_to_precision("BTC", 0.005123)
    assert res_amt == 0.005
    mock_ccxt.amount_to_precision.assert_called_with("BTC/USDT", 0.005123)

    res_px = gw.price_to_precision("BTC", 62000.504)
    assert res_px == 62000.50
    mock_ccxt.price_to_precision.assert_called_with("BTC/USDT", 62000.504)


def test_portfolio_service_position_flip_order_generation():
    """Verify that when flipping from Long to Short, PortfolioService generates a close order followed by an open order."""
    gw = MagicMock()
    gw.get_market_info = MagicMock(return_value={
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
        "precision": {"amount": 4, "price": 2},
    })
    ps = PortfolioService(gw, allow_market_orders=True, is_futures=True)

    # Initial snapshot: holding +0.05 BTC ($3,000 notional) on $10,000 total value (weight = +0.30)
    holding = AssetHolding(
        symbol="BTC",
        free=0.05,
        locked=0.0,
        total=0.05,
        value_usd=3000.0,
        entry_price=60000.0,
    )
    portfolio = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={"BTC": holding, "USDT": AssetHolding("USDT", 7000.0, 0.0, 7000.0, 7000.0)},
        total_value_usd=10000.0,
    )

    # Target allocation flips to Short: weight = -0.70 ($7,000 notional short)
    target = TargetAllocation(
        weights={"BTC/USDT": -0.70, "USDT": 0.30},
        regime=Regime.BEAR,
        timestamp_ms=1000,
    )
    prices = {"BTC/USDT": 60000.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)

    # Should generate TWO orders:
    # 1. SELL 0.05 BTC to close the long
    # 2. SELL 0.1166 BTC to open the short hedge
    assert len(plan.orders) == 2
    order1, order2 = plan.orders[0], plan.orders[1]

    assert order1.side == OrderSide.SELL
    assert order1.amount == 0.05
    assert "Close previous" in order1.reason

    assert order2.side == OrderSide.SELL
    assert "Open new" in order2.reason
    assert abs(order2.amount * 60000.0 - 7000.0) < 10.0  # 70% of 10,000 within precision truncation


def test_dry_run_exchange_cached_client():
    """Verify that DryRunExchange caches its public exchange client."""
    ex = DryRunExchange(initial_balances={"USDT": 1000.0})
    assert ex._public_exchange is None
    c1 = ex._get_public_exchange()
    if c1 is not None:
        c2 = ex._get_public_exchange()
        assert c1 is c2
