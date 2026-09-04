"""
Tests for risk manager — limit enforcement, kill switch, symbol validation.
"""

import time
import pytest
from src.config.config_manager import RiskConfig
from src.core.enums import OrderSide, OrderType
from src.core.models import (
    AssetHolding,
    OrderIntent,
    PortfolioSnapshot,
)
from src.services.risk_manager import RiskManager


def make_portfolio(total_value: float = 10000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding("USDT", total_value, 0, total_value, total_value),
        },
        total_value_usd=total_value,
    )


def make_intent(
    symbol: str = "BTC/USDT",
    side: OrderSide = OrderSide.BUY,
    amount: float = 0.01,
    price: float = 50000.0,
) -> OrderIntent:
    return OrderIntent(
        client_order_id=OrderIntent.generate_id(),
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        amount=amount,
        price=price,
        reason="test",
    )


class TestKillSwitch:
    def test_kill_switch_blocks_all_orders(self):
        config = RiskConfig(kill_switch=True)
        rm = RiskManager(config)
        approved, reason = rm.approve_order(make_intent(), make_portfolio())
        assert not approved
        assert "Kill switch" in reason

    def test_kill_switch_off_allows_orders(self):
        config = RiskConfig(kill_switch=False)
        rm = RiskManager(config)
        approved, _ = rm.approve_order(make_intent(), make_portfolio())
        assert approved


class TestSymbolValidation:
    def test_banned_symbol_rejected(self):
        config = RiskConfig(banned_symbols=["BTC/USDT"])
        rm = RiskManager(config)
        approved, reason = rm.approve_order(make_intent(), make_portfolio())
        assert not approved
        assert "banned" in reason

    def test_unlisted_symbol_rejected(self):
        config = RiskConfig(allowed_symbols=["ETH/USDT"])
        rm = RiskManager(config)
        approved, reason = rm.approve_order(
            make_intent(symbol="BTC/USDT"), make_portfolio()
        )
        assert not approved
        assert "not in allowed" in reason


class TestMaxOrderValue:
    def test_exceeds_max_rejected(self):
        config = RiskConfig(max_single_order_usd=100.0)
        rm = RiskManager(config)
        # 0.01 BTC * 50000 = $500 > $100 max
        approved, reason = rm.approve_order(make_intent(), make_portfolio())
        assert not approved
        assert "exceeds max" in reason

    def test_within_max_approved(self):
        config = RiskConfig(max_single_order_usd=1000.0)
        rm = RiskManager(config)
        approved, _ = rm.approve_order(
            make_intent(amount=0.001, price=50000.0), make_portfolio()
        )
        assert approved


class TestMaxOrdersPerCycle:
    def test_exceeds_max_orders(self):
        config = RiskConfig(max_orders_per_cycle=2, min_seconds_between_orders=0)
        rm = RiskManager(config)
        portfolio = make_portfolio()

        rm.approve_order(make_intent(), portfolio)
        rm.approve_order(make_intent(), portfolio)
        approved, reason = rm.approve_order(make_intent(), portfolio)
        assert not approved
        assert "Max orders per cycle" in reason


class TestMarketOrderRestriction:
    def test_market_order_rejected_when_disabled(self):
        config = RiskConfig(allow_market_orders=False)
        rm = RiskManager(config)
        intent = OrderIntent(
            client_order_id="test",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.001,
            reason="test",
        )
        approved, reason = rm.approve_order(intent, make_portfolio())
        assert not approved
        assert "Market orders" in reason


class TestMinOrderValue:
    def test_below_minimum_rejected(self):
        config = RiskConfig(min_order_value_usd=11.0)
        rm = RiskManager(config)
        # 0.0001 * 50000 = $5 < $11 min
        approved, reason = rm.approve_order(
            make_intent(amount=0.0001, price=50000.0), make_portfolio()
        )
        assert not approved
        assert "below minimum" in reason
