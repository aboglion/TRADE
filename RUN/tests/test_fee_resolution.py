"""
Tests for ExchangeGateway Fee Resolution:
- Option 2: Query exact trade commissions via fetch_my_trades
- Option 1: Fallback to estimated taker fee with clear logging / marking
- Integration with OrderManager, Orchestrator, and Telegram notifications
"""

import pytest
from unittest.mock import MagicMock, patch
import logging

from src.core.enums import OrderSide, OrderType, OrderStatus, RunMode
from src.core.models import OrderIntent, OrderResult, BotState
from src.exchanges.exchange_gateway import ExchangeGateway
from src.config.config_manager import ExchangeConfig
from src.services.order_manager import OrderManager
from src.services.telegram_service import TelegramService


@pytest.fixture
def mock_exchange_config():
    return ExchangeConfig(
        name="binance",
        market_type="future",
        portfolio_margin=False,
    )


@pytest.fixture
def mock_gateway(mock_exchange_config):
    gateway = ExchangeGateway(mock_exchange_config, run_mode=RunMode.LIVE)
    gateway._initialized = True
    gateway._exchange = MagicMock()
    gateway._markets = {
        "BTC/USDT": {
            "id": "BTCUSDT",
            "symbol": "BTC/USDT",
            "taker": 0.0005,
            "precision": {"amount": 3, "price": 1},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
        }
    }
    return gateway


class TestFeeResolution:
    """Test suite for Option 2 (exact trade fee) and Option 1 (estimated fallback)."""

    def test_option_2_retrieves_exact_fee_from_trades(self, mock_gateway, caplog):
        """When raw response has no fee, fetch_my_trades returns the exact trade commission."""
        caplog.set_level(logging.INFO)

        # Raw order response without fee
        mock_gateway.exchange.create_order.return_value = {
            "id": "1136087869442",
            "status": "closed",
            "amount": 0.002,
            "filled": 0.002,
            "price": 78543.1,
            "fee": None,
            "fees": [],
            "info": {},
        }

        # fetch_my_trades returns exact commission
        mock_gateway.exchange.fetch_my_trades.return_value = [
            {
                "order": "1136087869442",
                "fee": {"cost": 0.078543, "currency": "USDT"},
            }
        ]

        intent = OrderIntent(
            client_order_id="test_opt2",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.002,
            estimated_price=78543.1,
        )

        res = mock_gateway.create_order(intent)

        assert res.status == OrderStatus.FILLED
        assert res.fees == pytest.approx(0.078543, abs=1e-6)
        assert res.fee_currency == "USDT"
        assert res.fee_estimated is False
        assert "Retrieved exact fee from exchange trades for order 1136087869442" in caplog.text

    def test_option_1_fallback_when_trades_fail(self, mock_gateway, caplog):
        """When fetch_my_trades raises an exception, fall back to estimated fee with warning."""
        caplog.set_level(logging.WARNING)

        mock_gateway.exchange.create_order.return_value = {
            "id": "1136087869443",
            "status": "closed",
            "amount": 0.002,
            "filled": 0.002,
            "price": 78543.1,
            "fee": None,
            "fees": [],
            "info": {},
        }

        # fetch_my_trades fails
        mock_gateway.exchange.fetch_my_trades.side_effect = Exception("API rate limit or network error")

        intent = OrderIntent(
            client_order_id="test_opt1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.002,
            estimated_price=78543.1,
        )

        res = mock_gateway.create_order(intent)

        # Expected estimated fee: 0.002 * 78543.1 * 0.0005 = 0.0785431 USDT
        expected_est_fee = 0.002 * 78543.1 * 0.0005
        assert res.status == OrderStatus.FILLED
        assert res.fees == pytest.approx(expected_est_fee, abs=1e-6)
        assert res.fee_currency == "USDT"
        assert res.fee_estimated is True
        assert "Using ESTIMATED fee for order 1136087869443" in caplog.text

    def test_option_1_fallback_when_trades_empty(self, mock_gateway, caplog):
        """When fetch_my_trades returns empty list, fall back to estimated fee."""
        caplog.set_level(logging.WARNING)

        mock_gateway.exchange.create_order.return_value = {
            "id": "1136087869444",
            "status": "closed",
            "amount": 0.005,
            "filled": 0.005,
            "price": 80000.0,
            "fee": None,
            "fees": [],
            "info": {},
        }

        mock_gateway.exchange.fetch_my_trades.return_value = []

        intent = OrderIntent(
            client_order_id="test_opt1_empty",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.005,
            estimated_price=80000.0,
        )

        res = mock_gateway.create_order(intent)

        expected_est_fee = 0.005 * 80000.0 * 0.0005  # 0.20 USDT
        assert res.fees == pytest.approx(expected_est_fee, abs=1e-6)
        assert res.fee_estimated is True
        assert "Using ESTIMATED fee" in caplog.text

    def test_fetch_order_resolves_fees_correctly(self, mock_gateway):
        """fetch_order also resolves fees via trades or estimation."""
        mock_gateway.exchange.fetch_order.return_value = {
            "id": "1136087869445",
            "status": "closed",
            "amount": 0.001,
            "filled": 0.001,
            "price": 75000.0,
            "fee": None,
            "fees": [],
            "info": {},
        }
        mock_gateway.exchange.fetch_my_trades.return_value = [
            {
                "order": "1136087869445",
                "fee": {"cost": 0.0375, "currency": "USDT"},
            }
        ]

        res = mock_gateway.fetch_order("BTC/USDT", "1136087869445")
        assert res.fees == pytest.approx(0.0375, abs=1e-6)
        assert res.fee_estimated is False

    def test_order_manager_logs_estimated_tag(self, caplog):
        """OrderManager includes (est.) tag in logs when fee_estimated is True."""
        caplog.set_level(logging.INFO)
        gateway = MagicMock()
        state = BotState()
        mgr = OrderManager(gateway, state, run_mode=RunMode.LIVE)

        intent = OrderIntent(
            client_order_id="om_test_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.002,
        )

        gateway.create_order.return_value = OrderResult(
            client_order_id="om_test_1",
            exchange_order_id="1136087869442",
            status=OrderStatus.FILLED,
            filled_amount=0.002,
            average_price=78543.1,
            fees=0.078543,
            fee_currency="USDT",
            fee_estimated=True,
        )

        mgr.execute(intent)

        # Check log has "(est.)"
        assert "fees=0.078543 (est.)" in caplog.text

        # Check completed order has fee_estimated saved
        completed = state.completed_orders[-1]
        assert completed["fees"] == pytest.approx(0.078543, abs=1e-6)
        assert completed["fee_estimated"] is True

    def test_telegram_notification_includes_est_tag(self):
        """Telegram trade notification includes (est.) when fee_estimated is True."""
        service = TelegramService(bot_token="fake", chat_id="fake", enabled=True)
        service.send_message = MagicMock(return_value=True)

        order_data = {
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.002,
            "average_price": 78543.1,
            "fees": 0.078543,
            "fee_currency": "USDT",
            "fee_estimated": True,
            "reason": "Rebalance",
        }

        service.send_trade_notification(order_data, run_mode="LIVE")

        assert service.send_message.called
        sent_html = service.send_message.call_args[0][0]
        assert "0.078543 USDT (est.)" in sent_html

    def test_fee_resolution_catastrophic_error_never_crashes_order(self, mock_gateway, caplog):
        """Even if fee resolution throws a catastrophic exception, create_order still succeeds."""
        caplog.set_level(logging.WARNING)

        mock_gateway.exchange.create_order.return_value = {
            "id": "1136087869999",
            "status": "closed",
            "amount": 0.002,
            "filled": 0.002,
            "price": 78543.1,
            "fee": None,
            "fees": [],
            "info": {},
        }

        # Force _resolve_filled_order_fee to simulate an extreme unexpected bug/crash
        with patch.object(mock_gateway, "_resolve_filled_order_fee", side_effect=RuntimeError("Unexpected system failure")):
            intent = OrderIntent(
                client_order_id="test_crash_safe",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=0.002,
                estimated_price=78543.1,
            )

            # MUST NOT RAISE RuntimeError
            res = mock_gateway.create_order(intent)

            assert res.status == OrderStatus.FILLED
            assert res.exchange_order_id == "1136087869999"
            assert res.filled_amount == 0.002
            assert res.fees == 0.0
            assert "Safely handled fee resolution exception" in caplog.text

