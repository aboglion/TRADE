"""
Zero-Defect Financial Master Audit v5 Tests.

Verifies:
1. Orchestrator position state on open-from-flat: When opening a fresh position from flat (old total == 0),
   entry_price is set to fill_price, unrealized_pnl is 0.0, and leverage is set correctly.
2. Orchestrator position state on flip: When flipping position side (long <-> short), entry_price
   is set to fill_price, unrealized_pnl is 0.0, and leverage is updated.
3. Orchestrator price lookup fallback for ':USDT' pairs: Correctly falls back to base symbol in prices dict.
4. PortfolioService spot buy budgeting: Capping spot buy orders to available quote balance minus fee cushion
   to eliminate risk manager rejections.
5. PortfolioService emergency closure when total_value <= 0: Generates closing orders for active positions
   with the correct liquidation side (SELL for longs, BUY for shorts).
6. RiskManager string side tolerance: Seamlessly handles string sides ("BUY", "SELL") without AttributeError.
7. OrderManager fee tracking resilience: Accurately records fees when fee_currency is missing/empty, defaulting to USDT.
8. ReconciliationService orphan deduplication: Prevents treating known client_order_ids as orphans.
"""

import time
from unittest.mock import MagicMock

import pytest

from src.config.config_manager import AssetConfig, BotConfig, ExchangeConfig, RiskConfig, StrategyConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.orchestrator import BotOrchestrator
from src.services.order_manager import OrderManager
from src.services.portfolio_service import PortfolioService
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore


def test_orchestrator_flat_to_open_entry_price_and_leverage(tmp_path):
    """Verify that opening a position from a flat state (total == 0) sets entry_price and leverage correctly."""
    state_file = tmp_path / "bot_state.json"
    state_store = JsonStateStore(str(state_file))
    state = BotState()

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="future"),
        risk=RiskConfig(min_seconds_between_orders=0, allow_market_orders=True, max_single_order_usd=100000.0),
    )

    gateway = MagicMock()
    candle_svc = MagicMock()
    portfolio_svc = MagicMock()
    strategy = MagicMock()
    risk_mgr = RiskManager(config.risk, is_futures=True)

    intent = OrderIntent(
        client_order_id="open_flat_1",
        symbol="BTC/USDT:USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        price=50000.0,
        estimated_price=50000.0,
        leverage=2.0,  # Strategy wants 2x short hedge
        reason="Bear regime short hedge",
    )

    gateway.create_order.return_value = OrderResult(
        client_order_id="open_flat_1",
        exchange_order_id="exc_101",
        status=OrderStatus.FILLED,
        filled_amount=0.1,
        average_price=50000.0,
        fees=2.5,
        fee_currency="USDT",
    )

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
            "BTC": AssetHolding(
                symbol="BTC",
                free=0.0,
                locked=0.0,
                total=0.0,
                value_usd=0.0,
                unrealized_pnl=0.0,
                entry_price=0.0,
                leverage=10.0,  # Old residual leverage from previous long
            ),
        },
        total_value_usd=5000.0,
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"short_leverage": 2.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": -0.1, "USDT": 0.9},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_svc,
        portfolio_service=portfolio_svc,
        strategy=strategy,
        risk_manager=risk_mgr,
        state_store=state_store,
        state=state,
    )

    orchestrator.run_once(force=True)

    btc_holding = portfolio.holdings["BTC"]
    assert btc_holding.total == pytest.approx(-0.1)
    # Crucial assertion: entry price must be 50000.0, NOT 0.0!
    assert btc_holding.entry_price == 50000.0
    # Crucial assertion: unrealized PnL must be 0.0 right after fill, NOT -$5,000!
    assert btc_holding.unrealized_pnl == 0.0
    # Crucial assertion: leverage must be 2.0, NOT old 10.0!
    assert btc_holding.leverage == 2.0


def test_orchestrator_flip_position_entry_price_and_pnl(tmp_path):
    """Verify that flipping from long (+0.1) to short (-0.1) resets entry price and pnl to the fill price."""
    state_file = tmp_path / "bot_state.json"
    state_store = JsonStateStore(str(state_file))
    state = BotState()

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="future"),
        risk=RiskConfig(min_seconds_between_orders=0, allow_market_orders=True, max_single_order_usd=100000.0),
    )

    gateway = MagicMock()
    candle_svc = MagicMock()
    portfolio_svc = MagicMock()
    strategy = MagicMock()
    risk_mgr = RiskManager(config.risk, is_futures=True)

    intent = OrderIntent(
        client_order_id="flip_1",
        symbol="BTC/USDT:USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.2,
        price=60000.0,
        estimated_price=60000.0,
        leverage=2.0,
    )

    gateway.create_order.return_value = OrderResult(
        client_order_id="flip_1",
        exchange_order_id="exc_102",
        status=OrderStatus.FILLED,
        filled_amount=0.2,
        average_price=60000.0,
        fees=5.0,
        fee_currency="USDT",
    )

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0),
            "BTC": AssetHolding(
                symbol="BTC",
                free=0.0,
                locked=0.0,
                total=0.1,
                value_usd=6000.0,
                unrealized_pnl=500.0,
                entry_price=55000.0,
                leverage=5.0,
            ),
        },
        total_value_usd=16000.0,
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"short_leverage": 2.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": -0.1, "USDT": 0.9},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_svc,
        portfolio_service=portfolio_svc,
        strategy=strategy,
        risk_manager=risk_mgr,
        state_store=state_store,
        state=state,
    )

    orchestrator.run_once(force=True)

    btc_holding = portfolio.holdings["BTC"]
    assert btc_holding.total == pytest.approx(-0.1)
    assert btc_holding.entry_price == 60000.0
    assert btc_holding.unrealized_pnl == 0.0
    assert btc_holding.leverage == 2.0


def test_orchestrator_colon_usdt_pair_price_lookup_fallback(tmp_path):
    """Verify that prices dictionary keyed by 'BTC/USDT' is resolved when intent symbol is 'BTC/USDT:USDT'."""
    state_file = tmp_path / "bot_state.json"
    state_store = JsonStateStore(str(state_file))
    state = BotState()

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="future"),
        strategy=StrategyConfig(assets={"BTC": AssetConfig(pair="BTC/USDT")}),
        risk=RiskConfig(min_seconds_between_orders=0, min_order_value_usd=0.0, allow_market_orders=True, max_single_order_usd=100000.0),
    )

    gateway = MagicMock()
    candle_svc = MagicMock()
    portfolio_svc = MagicMock()
    strategy = MagicMock()
    risk_mgr = RiskManager(config.risk, is_futures=True)

    intent = OrderIntent(
        client_order_id="colon_fallback_1",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.05,
        price=None,
        estimated_price=None,
        leverage=1.0,
    )

    # Average price from gateway is 0.0, so it must fall back to prices dict
    gateway.create_order.return_value = OrderResult(
        client_order_id="colon_fallback_1",
        exchange_order_id="exc_103",
        status=OrderStatus.FILLED,
        filled_amount=0.05,
        average_price=0.0,
        fees=1.0,
        fee_currency="USDT",
    )

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
            "BTC": AssetHolding(symbol="BTC", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=5000.0,
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    # Provide price keyed with "BTC/USDT" (without :USDT) via candle history
    candle = Candle(timestamp_ms=1000, open=65000.0, high=65000.0, low=65000.0, close=65000.0, volume=10.0)
    candle_svc.get_full_history.return_value = [candle]

    decision = MagicMock()
    decision.regime = Regime.BULL
    decision.metadata = {"effective_leverage": 1.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": 0.5, "USDT": 0.5},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_svc,
        portfolio_service=portfolio_svc,
        strategy=strategy,
        risk_manager=risk_mgr,
        state_store=state_store,
        state=state,
    )

    orchestrator.run_once(force=True)

    btc_holding = portfolio.holdings["BTC"]
    assert btc_holding.entry_price == 65000.0
    assert btc_holding.value_usd == pytest.approx(0.05 * 65000.0)


def test_portfolio_service_spot_buy_budget_within_quote_balance():
    """Verify that spot buy orders are budgeted against available quote balance to prevent risk manager rejection."""
    gateway = MagicMock()
    gateway.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }
    ps = PortfolioService(gateway=gateway, deviation_threshold=0.01, is_futures=False)

    portfolio = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=100.0, locked=0.0, total=100.0, value_usd=100.0),
        },
        total_value_usd=100.0,
    )

    target = TargetAllocation(
        weights={"BTC/USDT": 0.90, "USDT": 0.10},  # Wants $90 of BTC
        timestamp_ms=1000,
        regime=Regime.BULL,
    )

    prices = {"BTC/USDT": 50000.0}
    plan = ps.compute_rebalance_plan(portfolio, target, prices)

    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.side == OrderSide.BUY
    # Max spend allowed is free quote (100) * 0.998 = $99.80
    assert (order.amount * 50000.0) <= 100.0


def test_portfolio_service_emergency_closure_on_zero_portfolio():
    """Verify that active positions are liquidated when portfolio total value <= 0."""
    gateway = MagicMock()
    gateway.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }
    ps = PortfolioService(gateway=gateway, deviation_threshold=0.01, is_futures=True)

    portfolio = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
            "BTC": AssetHolding(symbol="BTC", free=0.0, locked=0.0, total=0.5, value_usd=25000.0, unrealized_pnl=-25000.0),
        },
        total_value_usd=0.0,  # Zero net equity
    )

    target = TargetAllocation(
        weights={"BTC/USDT": 0.0, "USDT": 1.0},
        timestamp_ms=1000,
        regime=Regime.BEAR,
    )

    prices = {"BTC/USDT": 50000.0}
    plan = ps.compute_rebalance_plan(portfolio, target, prices)

    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.side == OrderSide.SELL
    assert order.amount == 0.5
    assert order.reduce_only is True


def test_risk_manager_string_side_tolerance():
    """Verify that RiskManager handles string side representations without AttributeError."""
    config = RiskConfig(
        max_single_order_usd=50000.0,
        min_order_value_usd=5.0,
        max_portfolio_change_pct=10.0,
        min_seconds_between_orders=0,
        allow_market_orders=True,
        kill_switch=False,
    )
    rm = RiskManager(config, is_futures=False)

    portfolio = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0),
            "BTC": AssetHolding(symbol="BTC", free=0.1, locked=0.0, total=0.1, value_usd=5000.0),
        },
        total_value_usd=6000.0,
    )

    # OrderIntent with string side "SELL" instead of OrderSide.SELL
    intent_sell = OrderIntent(
        client_order_id="test_str_sell",
        symbol="BTC/USDT",
        side="SELL",  # Plain string!
        order_type=OrderType.MARKET,
        amount=0.05,
        price=50000.0,
        estimated_price=50000.0,
    )

    approved, reason = rm.approve_order(intent_sell, portfolio)
    assert approved, f"Expected approval, got reason: {reason}"

    # OrderIntent with string side "BUY"
    intent_buy = OrderIntent(
        client_order_id="test_str_buy",
        symbol="BTC/USDT",
        side="BUY",  # Plain string!
        order_type=OrderType.MARKET,
        amount=0.01,
        price=50000.0,
        estimated_price=50000.0,
    )

    approved_buy, reason_buy = rm.approve_order(intent_buy, portfolio)
    assert approved_buy, f"Expected approval, got reason: {reason_buy}"


def test_order_manager_fee_recovery_default_currency():
    """Verify that OrderManager accurately recovers fees and defaults to USDT when fee_currency is missing."""
    state = BotState()
    gateway = MagicMock()
    om = OrderManager(state=state, gateway=gateway, run_mode=RunMode.DRY_RUN)

    intent = OrderIntent(
        client_order_id="fee_test_1",
        symbol="ETH/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        price=3000.0,
    )
    om._save_intent(intent)

    order_result = OrderResult(
        client_order_id="fee_test_1",
        exchange_order_id="exc_fee_1",
        symbol="ETH/USDT",
        status=OrderStatus.FILLED,
        filled_amount=1.0,
        average_price=3000.0,
        fees=1.5,
        fee_currency="",  # Empty currency
    )

    om._update_order_result(intent, order_result)

    assert "USDT" in state.session_fees
    assert state.session_fees["USDT"] == 1.5
    assert len(state.completed_orders) == 1
    assert state.completed_orders[0]["fees_recorded"] is True
    assert state.completed_orders[0]["fee_currency"] == "USDT"


def test_reconciliation_service_orphan_deduplication():
    """Verify that open exchange orders matching a known client_order_id are not duplicated as orphans."""
    state = BotState()
    state.pending_orders = [{
        "client_order_id": "known_client_1",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.1,
        "status": "submitted",
    }]

    gateway = MagicMock()
    # Mock open order on exchange
    gateway.get_open_orders.return_value = [
        OrderResult(
            client_order_id="known_client_1",
            exchange_order_id="exchange_id_999",
            symbol="BTC/USDT",
            status=OrderStatus.OPEN,
            filled_amount=0.0,
        )
    ]
    # Mock fetch_order for lookup
    gateway.fetch_order.return_value = OrderResult(
        client_order_id="known_client_1",
        exchange_order_id="exchange_id_999",
        symbol="BTC/USDT",
        status=OrderStatus.OPEN,
        filled_amount=0.0,
        fees=0.0,
    )

    recon = ReconciliationService(state=state, gateway=gateway)
    clean = recon.reconcile()

    # The order should be updated in-place, NOT duplicated into pending_orders
    assert len(state.pending_orders) == 1
    assert state.pending_orders[0]["exchange_order_id"] == "exchange_id_999"
    assert state.pending_orders[0]["status"] == "open"
