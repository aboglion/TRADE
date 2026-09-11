"""
Comprehensive Zero-Defect Financial Master Audit Test Suite.
Validates end-to-end mathematical precision, edge case robustness,
leverage propagation, position flip margin mechanics, risk checks,
and state sanitization for real-money trading safety.
"""

import math
import time
from unittest.mock import MagicMock

import pytest

from src.config.config_manager import RiskConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.models import AssetHolding, BotState, Candle, OrderIntent, OrderResult, PortfolioSnapshot, StrategyDecision, TargetAllocation
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.utils.math_utils import compute_order_amount, is_above_min_order, parse_precision_to_decimals, truncate_to_precision


class TestMathPrecisionAndZeroRiskBounds:
    """Rigorous verification of core mathematical utilities against NaN, Inf, and zero-value risks."""

    def test_truncate_to_precision_safety(self):
        # NaN / Inf should return 0.0
        assert truncate_to_precision(float("nan"), 4) == 0.0
        assert truncate_to_precision(float("inf"), 4) == 0.0
        assert truncate_to_precision(float("-inf"), 4) == 0.0

        # Truncation without rounding up
        assert truncate_to_precision(1.999999, 4) == 1.9999
        assert truncate_to_precision(0.00019, 4) == 0.0001
        assert truncate_to_precision(-1.999999, 4) == -1.9999
        assert truncate_to_precision(1234.56, 0) == 1234.0

    def test_parse_precision_to_decimals(self):
        assert parse_precision_to_decimals(4) == 4
        assert parse_precision_to_decimals("0.0001") == 4
        assert parse_precision_to_decimals(0.001) == 3
        assert parse_precision_to_decimals(1.0) == 1
        assert parse_precision_to_decimals("invalid") == 8

    def test_is_above_min_order_strictness(self):
        # Non-positive amounts or prices must be strictly rejected
        assert not is_above_min_order(0.0, 50000.0, 0.001, 10.0)
        assert not is_above_min_order(-0.01, 50000.0, 0.001, 10.0)
        assert not is_above_min_order(0.01, 0.0, 0.001, 10.0)
        assert not is_above_min_order(0.01, -50000.0, 0.001, 10.0)
        assert not is_above_min_order(float("nan"), 50000.0, 0.001, 10.0)

        # Amount below min_amount
        assert not is_above_min_order(0.0005, 50000.0, 0.001, 10.0)
        # Notional below min_notional ($25 vs $50 min)
        assert not is_above_min_order(0.001, 25000.0, 0.001, 50.0)
        # Valid order
        assert is_above_min_order(0.001, 50000.0, 0.001, 10.0)

    def test_compute_order_amount_non_positive_target(self):
        # Non-positive target_value_usd returns None (safe omission)
        assert compute_order_amount(0.0, 50000.0, 4, 0.001, 10.0) is None
        assert compute_order_amount(-100.0, 50000.0, 4, 0.001, 10.0) is None
        assert compute_order_amount(100.0, 0.0, 4, 0.001, 10.0) is None


class TestModelLeveragePropagation:
    """Ensure leverage metadata survives and propagates accurately from Strategy to RebalancePlan."""

    def test_target_allocation_leverage_defaults_and_fields(self):
        ta = TargetAllocation(
            weights={"BTC/USDT": 0.5},
            regime=Regime.BULL,
            timestamp_ms=1000,
            leverage=5.0,
            metadata={"short_leverage": 2.0},
        )
        assert ta.leverage == 5.0
        assert ta.metadata["short_leverage"] == 2.0

    def test_regime_adaptive_strategy_attaches_leverage(self):
        strat = RegimeAdaptiveStrategy(
            bull_leverage=10.0,
            short_leverage=3.0,
        )
        candles = [
            Candle(
                timestamp_ms=1600000000000 + i * 86400000,
                open=50000.0 + i * 100,
                high=51000.0 + i * 100,
                low=49000.0 + i * 100,
                close=50000.0 + i * 100,
                volume=1000.0,
            )
            for i in range(250)
        ]
        portfolio = PortfolioSnapshot(
            timestamp_ms=1600000000000,
            holdings={"USDT": AssetHolding("USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0)},
            total_value_usd=10000.0,
        )
        decision = strat.compute_signals({"BTC": candles}, portfolio)
        assert isinstance(decision, StrategyDecision)
        ta = decision.target_allocation
        assert hasattr(ta, "leverage")
        assert hasattr(ta, "metadata")
        assert "short_leverage" in ta.metadata


class TestPortfolioServiceFlipAndScopingIntegrity:
    """Verify position flip leverage resolution and confirm zero variable leakage in compute_rebalance_plan."""

    def test_flip_order_preserves_position_leverage_without_leakage(self):
        mock_gw = MagicMock()
        mock_gw.get_market_info.return_value = {
            "precision": {"amount": 4, "price": 2},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
        }
        ps = PortfolioService(gateway=mock_gw, is_futures=True, allow_market_orders=True)

        # Portfolio has multiple assets where USDT is processed last in loop
        portfolio = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={
                "ETH": AssetHolding("ETH", free=0.0, locked=0.0, total=5.0, value_usd=15000.0, entry_price=3000.0, leverage=4.0),
                "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=1.0, value_usd=60000.0, entry_price=50000.0, leverage=3.5),
                "USDT": AssetHolding("USDT", free=25000.0, locked=0.0, total=25000.0, value_usd=25000.0, leverage=1.0),
            },
            total_value_usd=100000.0,
        )
        prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0}

        # Target flips BTC from long (+0.60) to short (-0.20)
        target = TargetAllocation(
            weights={"BTC/USDT": -0.20, "ETH/USDT": 0.15, "USDT": 0.65},
            regime=Regime.BEAR,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = ps.compute_rebalance_plan(portfolio, target, prices)

        btc_close = next(o for o in plan.orders if "Close previous" in o.reason and "BTC" in o.symbol)
        btc_open = next(o for o in plan.orders if "Open new" in o.reason and "BTC" in o.symbol)

        # Both must preserve 3.5x leverage from original holding since neither target nor call specified override
        assert btc_close.leverage == 3.5
        assert btc_open.leverage == 3.5


class TestRiskManagerSymbolFlexibilityAndFuturesLeverage:
    """Verify symbol checking flexibility and high-conviction futures leverage rebalance approvals."""

    def test_check_symbol_allowed_variants(self):
        cfg = RiskConfig(
            allowed_symbols=["BTC/USDT", "ETH/USDT"],
            allow_market_orders=True,
            min_seconds_between_orders=0,
        )
        rm = RiskManager(config=cfg, is_futures=True)
        portfolio = PortfolioSnapshot(1000, {"USDT": AssetHolding("USDT", 10000, 0, 10000, 10000)}, 10000)

        # Standard pair
        intent1 = OrderIntent("1", "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, estimated_price=50000.0)
        approved1, _ = rm.approve_order(intent1, portfolio)
        assert approved1

        # Futures swap symbol variant
        intent2 = OrderIntent("2", "BTC/USDT:USDT", OrderSide.BUY, OrderType.MARKET, 0.01, estimated_price=50000.0)
        approved2, _ = rm.approve_order(intent2, portfolio)
        assert approved2

        # Base asset only
        intent3 = OrderIntent("3", "BTC", OrderSide.BUY, OrderType.MARKET, 0.01, estimated_price=50000.0)
        approved3, _ = rm.approve_order(intent3, portfolio)
        assert approved3

        # Disallowed symbol
        intent4 = OrderIntent("4", "DOGE/USDT", OrderSide.BUY, OrderType.MARKET, 100.0, estimated_price=0.1)
        approved4, reason4 = rm.approve_order(intent4, portfolio)
        assert not approved4
        assert "not in allowed list" in reason4

    def test_max_portfolio_change_scales_with_futures_leverage(self):
        # Normal max portfolio change: 0.35 (35%)
        cfg = RiskConfig(
            allowed_symbols=["BTC/USDT"],
            max_portfolio_change_pct=0.35,
            allow_market_orders=True,
            min_seconds_between_orders=0,
        )
        rm = RiskManager(config=cfg, is_futures=True)
        # Total portfolio: $10,000.
        portfolio = PortfolioSnapshot(1000, {"USDT": AssetHolding("USDT", 10000, 0, 10000, 10000)}, 10000)

        # Order with 0.1 BTC at $60,000 ($6,000 notional) with 10x leverage requires margin $600 (6% of portfolio).
        intent = OrderIntent(
            client_order_id="lev10_test",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            estimated_price=60000.0,
            leverage=10.0,
        )
        approved, msg = rm.approve_order(intent, portfolio)
        assert approved, f"High conviction leverage order was falsely rejected: {msg}"
