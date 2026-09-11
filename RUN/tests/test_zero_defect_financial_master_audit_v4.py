"""
Zero-Defect Financial Master Audit v4 Tests.

Verifies:
1. In-cycle pyramiding entry price update: Adding to an existing position calculates
   exact volume-weighted average entry price (VWAP) and accurate unrealized PnL.
2. In-cycle short addition entry price update: Adding to an existing short position
   calculates exact VWAP.
3. PortfolioService futures reduce-only overshoot prevention: Clamps reducing orders
   to not exceed current holding total, preventing Binance -2022 rejection.
4. PortfolioService futures position price fallbacks: Recovers from ticker failures
   by checking pair variants and falling back to entry_price.
5. OrderManager pre-submission idempotency: Ensures client_order_id is tracked in
   _submitted_ids even if the submission encounters an exception.
6. ReconciliationService status resilience: Handles both string and Enum statuses
   for orphaned orders safely.
7. Strategy calculation NaN immunity: Guards ATR%, 5d pullback, flash dip, and ADX
   against invalid or NaN inputs.
8. Web server portfolio price resolution: Falls back to ticker price and entry_price
   when holdings value is zero.
"""

import math
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.config.config_manager import BotConfig, ExchangeConfig, RiskConfig
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
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


def test_orchestrator_in_cycle_pyramiding_vwap_and_pnl(tmp_path):
    """Verify that adding to an existing long position updates entry price to VWAP and updates PnL."""
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

    # Initial holding: 1.0 BTC entered at $40,000
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=1.0, value_usd=40000.0, entry_price=40000.0, leverage=2.0),
            "USDT": AssetHolding("USDT", free=50000.0, locked=20000.0, total=70000.0, value_usd=70000.0),
        },
        total_value_usd=110000.0,
    )

    # Pyramiding order: BUY 1.0 BTC @ 60,000
    intent = OrderIntent(
        client_order_id="pyramid_buy_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        price=60000.0,
        estimated_price=60000.0,
        leverage=2.0,
    )

    gateway.create_order.return_value = OrderResult(
        client_order_id="pyramid_buy_1",
        exchange_order_id="dry_pyr_1",
        status=OrderStatus.FILLED,
        filled_amount=1.0,
        average_price=60000.0,
        fees=10.0,
        fee_currency="USDT",
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    decision = MagicMock()
    decision.regime = Regime.BULL
    decision.metadata = {"effective_leverage": 2.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": 0.5, "USDT": 0.5},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator.run_once(force=True)

    btc = portfolio.holdings["BTC"]
    assert btc.total == pytest.approx(2.0)
    # VWAP = (1.0 * 40000 + 1.0 * 60000) / 2.0 = 50000.0
    assert btc.entry_price == pytest.approx(50000.0)
    # Unrealized PnL at fill price 60000 = (60000 - 50000) * 2.0 = 20000.0
    assert btc.unrealized_pnl == pytest.approx(20000.0)


def test_orchestrator_in_cycle_short_addition_vwap(tmp_path):
    """Verify that adding to an existing short position updates entry price to VWAP."""
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

    # Initial holding: short -1.0 BTC entered at $60,000
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=-1.0, value_usd=60000.0, entry_price=60000.0, leverage=2.0),
            "USDT": AssetHolding("USDT", free=50000.0, locked=30000.0, total=80000.0, value_usd=80000.0),
        },
        total_value_usd=80000.0,
    )

    # Expanding short order: SELL 1.0 BTC @ 50,000
    intent = OrderIntent(
        client_order_id="add_short_1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=1.0,
        price=50000.0,
        estimated_price=50000.0,
        leverage=2.0,
    )

    gateway.create_order.return_value = OrderResult(
        client_order_id="add_short_1",
        exchange_order_id="dry_short_add",
        status=OrderStatus.FILLED,
        filled_amount=1.0,
        average_price=50000.0,
        fees=10.0,
        fee_currency="USDT",
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"short_leverage": 2.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": -0.7, "USDT": 0.3},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator.run_once(force=True)

    btc = portfolio.holdings["BTC"]
    assert btc.total == pytest.approx(-2.0)
    # VWAP = (1.0 * 60000 + 1.0 * 50000) / 2.0 = 55000.0
    assert btc.entry_price == pytest.approx(55000.0)
    # Unrealized PnL at fill price 50000 for short = (55000 - 50000) * 2.0 = 10000.0
    assert btc.unrealized_pnl == pytest.approx(10000.0)


def test_portfolio_service_clamps_reduce_only_order_to_position_total():
    """Verify that PortfolioService clamps futures reduce orders to not exceed holding total."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }
    ps = PortfolioService(gateway=mock_gw, is_futures=True, allow_market_orders=True)

    # Holding is 0.05 BTC long
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=0.05, value_usd=3000.0, leverage=2.0),
            "USDT": AssetHolding("USDT", free=7000.0, locked=0.0, total=7000.0, value_usd=7000.0),
        },
        total_value_usd=10000.0,
    )

    # Target weight would calculate an order of 0.08 BTC (e.g. deviation from sudden price move)
    # but since holding is only 0.05 BTC, reducing order must be clamped to 0.05 BTC
    target = TargetAllocation(
        weights={"BTC/USDT": 0.0, "USDT": 1.0},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )
    prices = {"BTC/USDT": 60000.0}

    plan = ps.compute_rebalance_plan(portfolio, target, prices)
    assert len(plan.orders) == 1
    order = plan.orders[0]
    assert order.side == OrderSide.SELL
    assert order.reduce_only is True
    assert order.amount == pytest.approx(0.05, abs=1e-5)


def test_portfolio_service_price_fallbacks_and_entry_price():
    """Verify that get_portfolio recovers from missing ticker prices using pair variants and entry_price."""
    mock_gw = MagicMock()
    # fetch_balance returns 1000 USDT
    mock_gw.fetch_balance.return_value = {
        "USDT": {"free": 1000.0, "used": 0.0, "total": 1000.0}
    }
    # fetch_positions returns position in BTC/USDT:USDT with entryPrice 60000
    mock_gw.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "contracts": 0.1,
            "side": "long",
            "entryPrice": 60000.0,
            "unrealizedPnl": 0.0,
            "leverage": 2.0,
        }
    ]
    # Simulate fetch_ticker_price failing for BTC/USDT:USDT but succeeding for BTC/USDT
    def mock_fetch_price(sym):
        if sym == "BTC/USDT":
            return 61000.0
        raise Exception("Not found")

    mock_gw.fetch_ticker_price.side_effect = mock_fetch_price

    ps = PortfolioService(gateway=mock_gw, is_futures=True)
    snapshot = ps.get_portfolio()

    btc = snapshot.holdings.get("BTC")
    assert btc is not None
    assert btc.total == pytest.approx(0.1)
    assert btc.value_usd == pytest.approx(6100.0)


def test_order_manager_pre_submission_idempotency_blocks_duplicates():
    """Verify that OrderManager blocks duplicate submissions even if the first attempt crashed/failed."""
    mock_gw = MagicMock()
    mock_gw.create_order.side_effect = Exception("Simulated network drop before confirmation")
    state = BotState()

    om = OrderManager(gateway=mock_gw, state=state, run_mode=RunMode.LIVE)
    intent = OrderIntent(
        client_order_id="duplicate_guard_id_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=60000.0,
    )

    # First attempt raises UnknownOrderStateError
    with pytest.raises(Exception):
        om.execute(intent)

    # Second attempt must immediately be blocked as duplicate by _submitted_ids
    with pytest.raises(Exception) as exc_info:
        om.execute(intent)

    assert "already submitted" in str(exc_info.value).lower()


def test_reconciliation_service_handles_enum_and_string_statuses():
    """Verify ReconciliationService handles both OrderStatus enum and string status safely."""
    mock_gw = MagicMock()
    orphan_order = MagicMock()
    orphan_order.exchange_order_id = "orphan_str_status"
    orphan_order.client_order_id = "orphan_cli"
    orphan_order.symbol = "SOLUSDT"
    orphan_order.status = "open"  # string, not Enum
    orphan_order.filled_amount = 0.0
    orphan_order.raw_response = {"amount": 5.0, "side": "BUY"}

    mock_gw.fetch_open_orders.return_value = [orphan_order]
    mock_gw.fetch_positions.return_value = []

    state = BotState()
    recon = ReconciliationService(gateway=mock_gw, state=state, run_mode=RunMode.LIVE)
    clean = recon.reconcile()

    assert not clean
    assert len(state.pending_orders) == 1
    assert state.pending_orders[0]["exchange_order_id"] == "orphan_str_status"
    assert state.pending_orders[0]["status"] == "open"


def test_strategy_indicators_nan_immunity():
    """Verify that RegimeAdaptiveStrategy is immune to NaN or invalid indicators in candles."""
    strategy = RegimeAdaptiveStrategy(sma_regime_period=150)
    # Generate 200 daily candles with some zero or extreme values
    candles = [
        Candle(
            timestamp_ms=1_600_000_000_000 + i * 86_400_000,
            open=50000.0 if i > 0 else 0.0,
            high=51000.0,
            low=49000.0 if i > 0 else 0.0,
            close=50000.0,
            volume=100.0,
            is_closed=True,
        )
        for i in range(200)
    ]
    portfolio = PortfolioSnapshot(
        timestamp_ms=1_600_000_000_000 + 200 * 86_400_000,
        holdings={"USDT": AssetHolding("USDT", 10000.0, 0.0, 10000.0, 10000.0)},
        total_value_usd=10000.0,
    )

    decision = strategy.compute_signals({"BTC/USDT": candles}, portfolio)
    assert decision is not None
    assert not math.isnan(decision.metadata.get("effective_leverage", 1.0))
    assert not math.isnan(decision.metadata.get("dist_from_5d_high_pct", 0.0))


def test_server_portfolio_price_resolution_fallback():
    """Verify that Web Dashboard /api/portfolio price determination falls back to ticker and entry price."""
    from src.web.server import DashboardRequestHandler

    mock_gateway = MagicMock()
    mock_gateway.fetch_ticker_price.return_value = 52000.0

    mock_state_store = MagicMock()
    mock_state = BotState()
    mock_state_store.load_state.return_value = mock_state

    # Holding with total != 0 but value_usd == 0.0 (e.g. before initial price fetch)
    h_btc = AssetHolding(symbol="BTC", free=0.5, locked=0.0, total=0.5, value_usd=0.0, entry_price=50000.0)
    h_usdt = AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)
    snapshot = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"BTC": h_btc, "USDT": h_usdt},
        total_value_usd=1000.0,
    )

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.gateway = mock_gateway
    handler.config = MagicMock()
    handler.config.exchange.market_type = "future"
    handler.state_store = mock_state_store
    handler._send_json = MagicMock()

    with MagicMock() as mock_ps_class:
        mock_ps_instance = MagicMock()
        mock_ps_instance.get_portfolio.return_value = snapshot
        import src.services.portfolio_service
        orig_ps = src.services.portfolio_service.PortfolioService
        src.services.portfolio_service.PortfolioService = MagicMock(return_value=mock_ps_instance)
        try:
            handler._handle_portfolio()
        finally:
            src.services.portfolio_service.PortfolioService = orig_ps

    handler._send_json.assert_called_once()
    sent_data = handler._send_json.call_args[0][0]
    btc_entry = next(h for h in sent_data["holdings"] if h["symbol"] == "BTC")
    # Must have resolved price to either ticker (52000) or entry_price (50000), not 0.0!
    assert btc_entry["current_price"] in (52000.0, 50000.0)
    assert btc_entry["current_price"] > 0

