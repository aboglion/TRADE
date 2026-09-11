"""
Zero-Defect Financial Integrity Audit Test Suite v7.

Validates:
1. Spot crypto-to-crypto rebalance with zero starting quote currency (sell proceeds credit budget).
2. Spot leveraged weights normalization (relative risk parity preserved without starvation).
3. Strict 1.0x leverage enforcement in Spot mode.
4. Spot Bear regime negative short hedge clamping to zero (safe 100% cash protection).
5. Web dashboard server-orchestrator in-memory and on-disk state synchronization.
6. Fallback price resolution in PortfolioService.get_portfolio.
"""

import time
from unittest.mock import MagicMock

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
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.web.server import DashboardRequestHandler


def test_spot_crypto_to_crypto_rebalance_with_zero_initial_quote():
    """Verify that in Spot mode, selling one asset produces quote budget to fund multiple buy orders."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    # Account has $120 worth of BTC and $0 USDT
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding(symbol="BTC", free=0.002, locked=0.0, total=0.002, value_usd=120.0),
            "USDT": AssetHolding(symbol="USDT", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=120.0,
    )

    # Strategy wants to rotate out of BTC into 50% ETH ($60) and 50% SOL ($60)
    target = TargetAllocation(
        weights={"BTC/USDT": 0.0, "ETH/USDT": 0.50, "SOL/USDT": 0.50, "USDT": 0.0},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )

    prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)

    # Expect 1 sell (BTC) followed by 2 buys (ETH, SOL)
    assert len(plan.orders) == 3
    assert plan.orders[0].symbol == "BTC/USDT"
    assert plan.orders[0].side == OrderSide.SELL
    assert plan.orders[0].amount == 0.002

    assert plan.orders[1].symbol == "ETH/USDT"
    assert plan.orders[1].side == OrderSide.BUY

    assert plan.orders[2].symbol == "SOL/USDT"
    assert plan.orders[2].side == OrderSide.BUY

    # Total spend across the buys must not exceed the proceeds of the sell
    total_buy_spend = sum(o.amount * prices[o.symbol] for o in plan.orders if o.side == OrderSide.BUY)
    assert total_buy_spend <= 120.0 * 0.998 + 1e-4
    assert total_buy_spend >= 115.0  # Successfully deployed nearly all proceeds


def test_spot_leveraged_target_weights_normalization():
    """Verify that when the strategy emits leveraged weights (>1.0), Spot mode normalizes them to 1.0."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0),
        },
        total_value_usd=1000.0,
    )

    # Leveraged bull weights summing to 2.05 (205% notional exposure)
    target = TargetAllocation(
        weights={"BTC/USDT": 0.82, "ETH/USDT": 0.615, "SOL/USDT": 0.615, "USDT": 0.0},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )

    prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)

    # All 3 assets should be bought
    assert len(plan.orders) == 3
    order_dict = {o.symbol: o for o in plan.orders}
    assert "BTC/USDT" in order_dict
    assert "ETH/USDT" in order_dict
    assert "SOL/USDT" in order_dict

    btc_val = order_dict["BTC/USDT"].amount * prices["BTC/USDT"]
    eth_val = order_dict["ETH/USDT"].amount * prices["ETH/USDT"]
    sol_val = order_dict["SOL/USDT"].amount * prices["SOL/USDT"]

    # Target relative proportions were 0.82 / 0.615 / 0.615 = 40% / 30% / 30%
    assert abs(btc_val - 400.0) < 5.0
    assert abs(eth_val - 300.0) < 5.0
    assert abs(sol_val - 300.0) < 5.0

    total_spend = btc_val + eth_val + sol_val
    assert total_spend <= 1000.0 * 0.998 + 1e-4


def test_spot_mode_enforces_strict_1x_leverage():
    """Verify that in Spot mode, all generated order intents have leverage == 1.0."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
        },
        total_value_usd=5000.0,
    )

    target = TargetAllocation(
        weights={"BTC/USDT": 1.0, "USDT": 0.0},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
        metadata={"effective_leverage": 10.0, "short_leverage": 2.0},
    )

    prices = {"BTC/USDT": 60000.0}

    plan = ps.compute_rebalance_plan(
        portfolio=portfolio,
        target=target,
        prices=prices,
        leverage=10.0,
        short_leverage=2.0,
    )

    assert len(plan.orders) == 1
    assert plan.orders[0].leverage == 1.0


def test_spot_bear_regime_short_hedge_clamped_to_zero():
    """Verify that in Spot mode, a negative target short weight is clamped to 0.0, protecting cash."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    # Spot account holds $10,000 USDT cash
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0),
        },
        total_value_usd=10000.0,
    )

    # Strategy wants -70% short hedge on BTC
    target = TargetAllocation(
        weights={"BTC/USDT": -0.70, "USDT": 0.35},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )

    prices = {"BTC/USDT": 60000.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)

    # In spot, no orders should be generated to short BTC from cash
    assert len(plan.orders) == 0


def test_server_live_orchestrator_state_sync():
    """Verify DashboardRequestHandler synchronizes with running orchestrator's in-memory state."""
    mock_state = BotState(
        last_regime="BULL",
        session_initial_value_usd=10000.0,
        pnl_history=[{"ts": 1000, "val": 10000.0, "pnl_usd": 0.0, "pnl_pct": 0.0}],
    )

    mock_orch = MagicMock()
    mock_orch._state = mock_state

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.orchestrator = mock_orch
    handler.state_store = MagicMock()
    # Mock state store to return a different object if called
    handler.state_store.load_state.return_value = BotState(last_regime="BEAR")

    # _get_active_state must prioritize running orchestrator in-memory state
    active = handler._get_active_state()
    assert active is mock_state
    assert active.last_regime == "BULL"
    assert len(active.pnl_history) == 1


def test_portfolio_service_get_portfolio_fallback_price_keys():
    """Verify get_portfolio resolves prices from fallback keys without calling gateway."""
    mock_gw = MagicMock()
    mock_gw.fetch_balance.return_value = {
        "USDT": {"free": 500.0, "used": 0.0, "total": 500.0},
        "ETH": {"free": 1.0, "used": 0.0, "total": 1.0},
    }
    mock_gw.fetch_positions.return_value = []
    # If fetch_ticker_price is called, fail the test
    mock_gw.fetch_ticker_price.side_effect = AssertionError("Should have used memory price fallback")

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    # Prices dictionary only has ETH/USDT:USDT variant
    prices = {"ETH/USDT:USDT": 3200.0}

    snapshot = ps.get_portfolio(prices=prices)
    assert "ETH" in snapshot.holdings
    assert snapshot.holdings["ETH"].value_usd == 3200.0
    assert snapshot.total_value_usd == 3700.0
