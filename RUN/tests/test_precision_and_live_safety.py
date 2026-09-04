"""
Unit tests for float precision, market order selection, estimated_price valuation, and live safety fixes.
"""

from unittest.mock import MagicMock
import pytest
from src.utils.math_utils import (
    compute_order_amount,
    get_market_constraints,
    parse_precision_to_decimals,
    truncate_to_precision,
)
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.config.config_manager import RiskConfig
from src.core.enums import OrderType, OrderSide, Regime
from src.core.models import AssetHolding, PortfolioSnapshot, TargetAllocation, OrderIntent, Candle
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


def test_parse_precision_to_decimals():
    assert parse_precision_to_decimals(8) == 8
    assert parse_precision_to_decimals(5) == 5
    assert parse_precision_to_decimals("4") == 4
    assert parse_precision_to_decimals(1e-05) == 5
    assert parse_precision_to_decimals(0.0001) == 4
    assert parse_precision_to_decimals(0.01) == 2
    assert parse_precision_to_decimals(0.001000) == 3
    assert parse_precision_to_decimals(1.0) == 1
    assert parse_precision_to_decimals(None) == 8
    assert parse_precision_to_decimals(0) == 0


def test_truncate_to_precision_with_ccxt_floats():
    val = 0.0201094
    truncated = truncate_to_precision(val, 1e-05)
    assert truncated == 0.02010

    price = 65432.1987
    truncated_price = truncate_to_precision(price, 0.01)
    assert truncated_price == 65432.19


def test_compute_order_amount_with_float_precision():
    target_usd = 1307.12
    price = 65000.0
    amount_precision = 1e-05
    min_amount = 0.00001
    min_notional = 5.0

    amount = compute_order_amount(
        target_value_usd=target_usd,
        price=price,
        amount_precision=amount_precision,
        min_amount=min_amount,
        min_notional=min_notional,
    )
    assert amount is not None
    assert amount > 0.0
    assert amount == 0.02010


def test_get_market_constraints_parsing():
    ccxt_market_info = {
        "precision": {"amount": 1e-05, "price": 0.01},
        "limits": {
            "amount": {"min": 1e-05, "max": 9000.0},
            "price": {"min": 0.01, "max": 1000000.0},
            "cost": {"min": 5.0, "max": 9000000.0},
        },
    }
    constraints = get_market_constraints(ccxt_market_info)
    assert constraints["amount_precision"] == 5
    assert constraints["price_precision"] == 2
    assert constraints["min_amount"] == 1e-05
    assert constraints["min_notional"] == 5.0


def test_portfolio_service_market_order_selection():
    mock_gateway = MagicMock()
    mock_gateway.get_market_info.return_value = {
        "precision": {"amount": 5, "price": 2},
        "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5.0}},
    }

    ps_market = PortfolioService(mock_gateway, deviation_threshold=0.01, allow_market_orders=True)
    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={"USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)},
        total_value_usd=1000.0,
    )
    target = TargetAllocation(weights={"BTC/USDT": 0.50, "USDT": 0.50}, regime=Regime.BULL, timestamp_ms=1000)
    prices = {"BTC/USDT": 50000.0}

    plan_market = ps_market.compute_rebalance_plan(snapshot, target, prices)
    assert len(plan_market.orders) == 1
    assert plan_market.orders[0].order_type == OrderType.MARKET
    assert plan_market.orders[0].price is None
    assert plan_market.orders[0].estimated_price == 50000.0

    ps_limit = PortfolioService(mock_gateway, deviation_threshold=0.01, allow_market_orders=False)
    plan_limit = ps_limit.compute_rebalance_plan(snapshot, target, prices)
    assert len(plan_limit.orders) == 1
    assert plan_limit.orders[0].order_type == OrderType.LIMIT
    assert plan_limit.orders[0].price == 50000.0


def test_risk_manager_evaluates_market_orders_with_estimated_price():
    config = RiskConfig(
        min_order_value_usd=11.0,
        max_single_order_usd=1000.0,
        max_portfolio_change_pct=0.20,
        allow_market_orders=True,
        allowed_symbols=["BTC/USDT"],
    )
    rm = RiskManager(config)
    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={"USDT": AssetHolding(symbol="USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0)},
        total_value_usd=10000.0,
    )

    # Market order without price, but with estimated_price = 50000.0, amount = 0.01 (value = $500, 5% of $10,000)
    market_intent = OrderIntent(
        client_order_id="test_mkt",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        price=None,
        estimated_price=50000.0,
    )

    approved, reason = rm.approve_order(market_intent, snapshot)
    assert approved is True
    assert reason == ""


def test_regime_detection_with_1000_candles():
    strategy = RegimeAdaptiveStrategy(sma_regime_period=150)
    # Generate 1000 4H candles (approx 166 days)
    candles = []
    base_ts = 1600000000000
    tf_ms = 14400000
    base_price = 10000.0

    for i in range(1000):
        price = base_price + (i * 10)  # Uptrend so latest > SMA150
        candles.append(Candle(
            timestamp_ms=base_ts + (i * tf_ms),
            open=price,
            high=price + 5,
            low=price - 5,
            close=price,
            volume=100.0,
        ))

    candles_by_asset = {"BTC/USDT": candles}
    regime = strategy._determine_regime(candles)
    assert regime == Regime.BULL
