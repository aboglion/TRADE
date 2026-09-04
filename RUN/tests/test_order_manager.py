"""
Tests for order manager — duplicate prevention, lifecycle tracking, crash recovery.
"""

import pytest
from src.core.enums import OrderSide, OrderStatus, OrderType, RunMode
from src.core.exceptions import DuplicateOrderError
from src.core.models import BotState, OrderIntent, OrderResult
from src.exchanges.dry_run_exchange import DryRunExchange
from src.services.order_manager import OrderManager


@pytest.fixture
def dry_exchange():
    return DryRunExchange(initial_balances={"USDT": 10000.0, "BTC": 0.1})


@pytest.fixture
def order_manager(dry_exchange):
    state = BotState()
    return OrderManager(dry_exchange, state, RunMode.DRY_RUN)


def make_intent(symbol="BTC/USDT", side=OrderSide.BUY, amount=0.001, price=50000.0):
    return OrderIntent(
        client_order_id=OrderIntent.generate_id(),
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        amount=amount,
        price=price,
        reason="test",
    )


class TestDuplicatePrevention:
    def test_same_id_rejected(self, dry_exchange):
        state = BotState()
        om = OrderManager(dry_exchange, state, RunMode.DRY_RUN)

        intent = OrderIntent(
            client_order_id="fixed_id_123",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=0.001,
            price=50000.0,
            reason="test",
        )

        dry_exchange.set_price("BTC/USDT", 50000.0)
        om.execute(intent)

        with pytest.raises(DuplicateOrderError):
            om.execute(intent)

    def test_different_ids_allowed(self, order_manager, dry_exchange):
        dry_exchange.set_price("BTC/USDT", 50000.0)
        r1 = order_manager.execute(make_intent())
        r2 = order_manager.execute(make_intent())
        assert r1.status == OrderStatus.FILLED
        assert r2.status == OrderStatus.FILLED


class TestOrderLifecycle:
    def test_buy_updates_state(self, order_manager, dry_exchange):
        dry_exchange.set_price("BTC/USDT", 50000.0)
        intent = make_intent()
        result = order_manager.execute(intent)

        assert result.status == OrderStatus.FILLED
        assert result.filled_amount > 0
        assert result.average_price > 0

    def test_sell_executes(self, order_manager, dry_exchange):
        dry_exchange.set_price("BTC/USDT", 50000.0)
        intent = make_intent(side=OrderSide.SELL, amount=0.01)
        result = order_manager.execute(intent)

        assert result.status == OrderStatus.FILLED

    def test_insufficient_balance_fails(self, dry_exchange):
        state = BotState()
        om = OrderManager(dry_exchange, state, RunMode.DRY_RUN)

        dry_exchange.set_price("BTC/USDT", 50000.0)
        # Try to buy way more than we can afford
        intent = make_intent(amount=1000.0, price=50000.0)
        result = om.execute(intent)
        # DryRunExchange returns FAILED, doesn't raise
        assert result.status == OrderStatus.FAILED


class TestCrashRecovery:
    def test_pending_orders_loaded_on_restart(self, dry_exchange):
        state = BotState()
        state.pending_orders = [{
            "client_order_id": "old_order",
            "exchange_order_id": "exc_123",
            "symbol": "BTC/USDT",
            "status": "submitted",
        }]

        om = OrderManager(dry_exchange, state, RunMode.DRY_RUN)

        # The old order ID should be in submitted_ids
        assert "old_order" in om._submitted_ids
