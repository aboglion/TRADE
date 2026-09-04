"""
Tests for portfolio service — balance reading, rebalance plan computation.
"""

import time
import pytest
from src.core.enums import OrderSide, Regime
from src.core.models import (
    AssetHolding,
    OrderIntent,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.exchanges.dry_run_exchange import DryRunExchange
from src.services.portfolio_service import PortfolioService


@pytest.fixture
def dry_exchange():
    ex = DryRunExchange(initial_balances={"USDT": 1000.0, "BTC": 0.01, "ETH": 0.1})
    ex.set_price("BTC/USDT", 50000.0)
    ex.set_price("ETH/USDT", 3000.0)
    ex.set_price("SOL/USDT", 100.0)
    return ex


@pytest.fixture
def portfolio_service(dry_exchange):
    return PortfolioService(dry_exchange, deviation_threshold=0.03)


class TestGetPortfolio:
    def test_returns_snapshot(self, portfolio_service):
        prices = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0}
        snap = portfolio_service.get_portfolio(prices=prices)

        assert isinstance(snap, PortfolioSnapshot)
        assert snap.total_value_usd > 0
        assert "USDT" in snap.holdings
        assert "BTC" in snap.holdings

    def test_usdt_valued_at_face(self, portfolio_service):
        snap = portfolio_service.get_portfolio(prices={})
        usdt = snap.holdings.get("USDT")
        assert usdt is not None
        assert abs(usdt.value_usd - 1000.0) < 1.0


class TestRebalancePlan:
    def test_no_rebalance_when_within_threshold(self, portfolio_service, dry_exchange):
        # Current: all USDT ~ all in cash
        prices = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 100.0}
        snap = portfolio_service.get_portfolio(prices=prices)

        # Target: also mostly USDT
        target = TargetAllocation(
            weights={
                "BTC/USDT": snap.get_weight("BTC"),
                "ETH/USDT": snap.get_weight("ETH"),
                "SOL/USDT": 0.0,
                "USDT": 1.0 - snap.get_weight("BTC") - snap.get_weight("ETH"),
            },
            regime=Regime.BULL,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = portfolio_service.compute_rebalance_plan(snap, target, prices)
        # Should have zero or very few orders (within threshold)
        total_value = sum(o.amount * (o.price or 0) for o in plan.orders)
        assert total_value < snap.total_value_usd * 0.05

    def test_sells_before_buys(self, portfolio_service, dry_exchange):
        prices = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 100.0}
        snap = portfolio_service.get_portfolio(prices=prices)

        # Target: sell all crypto, buy nothing
        target = TargetAllocation(
            weights={
                "BTC/USDT": 0.0,
                "ETH/USDT": 0.0,
                "SOL/USDT": 0.0,
                "USDT": 1.0,
            },
            regime=Regime.BEAR,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = portfolio_service.compute_rebalance_plan(snap, target, prices)

        # All orders should be sells
        for order in plan.orders:
            assert order.side == OrderSide.SELL

    def test_min_order_size_filtering(self, portfolio_service, dry_exchange):
        prices = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 100.0}
        snap = portfolio_service.get_portfolio(prices=prices)

        # Target with tiny deviation
        target = TargetAllocation(
            weights={
                "BTC/USDT": snap.get_weight("BTC") + 0.001,  # Tiny change
                "ETH/USDT": snap.get_weight("ETH"),
                "SOL/USDT": 0.0,
                "USDT": 1.0 - snap.get_weight("BTC") - 0.001 - snap.get_weight("ETH"),
            },
            regime=Regime.BULL,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = portfolio_service.compute_rebalance_plan(snap, target, prices)
        # Tiny changes should be filtered out
        for o in plan.orders:
            assert o.amount * (o.price or 0) >= 10.0  # Min notional


class TestDryRunBalancesUpdate:
    def test_set_balances_updates_dry_exchange(self, dry_exchange, portfolio_service):
        dry_exchange.set_balances({"USDT": 2500.0, "SOL": 10.0})
        bal = dry_exchange.fetch_balance()

        assert bal["USDT"]["total"] == 2500.0
        assert bal["SOL"]["total"] == 10.0
        assert "BTC" not in bal

        snap = portfolio_service.get_portfolio(prices={"SOL/USDT": 100.0})
        assert snap.total_value_usd == 3500.0  # 2500 USDT + 10 SOL * $100
