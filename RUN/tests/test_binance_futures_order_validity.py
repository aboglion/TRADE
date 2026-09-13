"""
Comprehensive Binance Futures Order Validity Test Suite.

Tests specifically designed to verify that BUY and SELL orders for Binance Futures
are completely validated and cannot fail due to:
1. -2022: ReduceOnly Order is rejected (zero contracts, wrong side, or amount > contracts).
2. -4061: Order's position side does not match user's setting (Hedge Mode vs One-Way Mode).
3. Filter failure: MIN_NOTIONAL (Binance requires $50 for BTC, $20 for ETH, $5 for SOL on expanding orders, exempt on reduceOnly).
4. Filter failure: LOT_SIZE / -1111 (step size truncation and precision compliance).
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from src.core.enums import OrderSide, OrderType, OrderStatus, Regime, RunMode
from src.core.models import (
    AssetHolding,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.services.portfolio_service import PortfolioService
from src.exchanges.exchange_gateway import ExchangeGateway
from src.config.config_manager import ExchangeConfig
from src.utils.math_utils import (
    BINANCE_DEFAULT_MIN_NOTIONAL,
    is_above_min_order,
    truncate_to_step_size,
    truncate_to_precision,
    compute_order_amount,
)


class TestBinanceFuturesOrderValidity:
    """Test suite covering all Binance Futures order submission rules."""

    def test_zero_contracts_futures_rebalance_never_sets_reduce_only(self):
        """
        Verify that if an account has 0 open futures contracts, PortfolioService
        NEVER sends reduce_only=True even if target weights change or deviation is negative.
        This directly prevents Binance error: -2022 ReduceOnly Order is rejected.
        """
        gateway = MagicMock()
        gateway.fetch_balance.return_value = {
            "USDT": {"free": 1000.0, "used": 0.0, "total": 1000.0}
        }
        # No open futures positions on Binance
        gateway.fetch_positions.return_value = []
        gateway.get_market_info.return_value = {
            "precision": {"amount": 3, "price": 1},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 50.0}},
        }

        ps = PortfolioService(gateway, is_futures=True, allow_market_orders=True)
        snapshot = ps.get_portfolio(prices={"BTC/USDT": 60000.0})

        # Holding has contracts=0.0
        btc_holding = snapshot.holdings.get("BTC")
        if btc_holding:
            assert btc_holding.contracts == 0.0

        # Strategy targets 0% BTC (or Cash)
        target = TargetAllocation(
            weights={"BTC/USDT": 0.0, "USDT": 1.0},
            regime=Regime.BEAR,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = ps.compute_rebalance_plan(snapshot, target, prices={"BTC/USDT": 60000.0})
        # Since contracts are 0, there is nothing to reduce, so no reduce_only order should be generated
        for o in plan.orders:
            assert o.reduce_only is False, f"Order {o} should NOT have reduce_only=True with 0 contracts"

    def test_open_position_closing_sets_reduce_only_clamped_to_contracts(self):
        """
        Verify that when closing an open LONG position:
        - Side is SELL
        - reduce_only is True
        - Amount is clamped to exact open contracts (never exceeds)
        """
        gateway = MagicMock()
        gateway.fetch_balance.return_value = {
            "USDT": {"free": 5000.0, "used": 1500.0, "total": 6500.0}
        }
        # Open LONG position of 0.05 BTC
        gateway.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "contracts": 0.05,
                "side": "long",
                "entryPrice": 60000.0,
                "info": {"positionAmt": "0.050"},
            }
        ]
        gateway.get_market_info.return_value = {
            "precision": {"amount": 3, "price": 1},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 50.0}},
        }

        ps = PortfolioService(gateway, is_futures=True, allow_market_orders=True)
        snapshot = ps.get_portfolio(prices={"BTC/USDT": 60000.0})

        target = TargetAllocation(
            weights={"BTC/USDT": 0.0, "USDT": 1.0},
            regime=Regime.BEAR,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = ps.compute_rebalance_plan(snapshot, target, prices={"BTC/USDT": 60000.0})
        assert len(plan.orders) >= 1
        close_order = plan.orders[0]
        assert close_order.side == OrderSide.SELL
        assert close_order.reduce_only is True
        assert close_order.amount <= 0.05 + 1e-9

    def test_open_short_position_closing_sets_reduce_only_buy(self):
        """
        Verify that when closing an open SHORT position:
        - Side is BUY
        - reduce_only is True
        - Amount is clamped to abs(open contracts)
        """
        gateway = MagicMock()
        gateway.fetch_balance.return_value = {
            "USDT": {"free": 5000.0, "used": 1500.0, "total": 6500.0}
        }
        # Open SHORT position of -0.08 BTC
        gateway.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "contracts": -0.08,
                "side": "short",
                "entryPrice": 62000.0,
                "info": {"positionAmt": "-0.080"},
            }
        ]
        gateway.get_market_info.return_value = {
            "precision": {"amount": 3, "price": 1},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 50.0}},
        }

        ps = PortfolioService(gateway, is_futures=True, allow_market_orders=True)
        snapshot = ps.get_portfolio(prices={"BTC/USDT": 62000.0})

        target = TargetAllocation(
            weights={"BTC/USDT": 0.0, "USDT": 1.0},
            regime=Regime.BEAR,
            timestamp_ms=int(time.time() * 1000),
        )

        plan = ps.compute_rebalance_plan(snapshot, target, prices={"BTC/USDT": 62000.0})
        assert len(plan.orders) >= 1
        close_order = plan.orders[0]
        assert close_order.side == OrderSide.BUY
        assert close_order.reduce_only is True
        assert close_order.amount <= 0.08 + 1e-9

    def test_binance_min_notional_rules(self):
        """
        Verify Binance default minimum notionals:
        - BTC/USDT: $50
        - ETH/USDT: $20
        - SOL/USDT: $5
        - Expanding orders below minimum notional are rejected.
        - Reduce-only orders below minimum notional are allowed (Binance allows closing any size).
        """
        assert BINANCE_DEFAULT_MIN_NOTIONAL["BTC"] == 50.0
        assert BINANCE_DEFAULT_MIN_NOTIONAL["ETH"] == 20.0
        assert BINANCE_DEFAULT_MIN_NOTIONAL["SOL"] == 5.0

        # BTC expanding order of $40 (below $50) -> Rejected
        assert not is_above_min_order(amount=40.0, price=1.0, min_amount=0.001, min_notional=50.0, is_reduce_only=False)
        # BTC expanding order of $51 -> Allowed
        assert is_above_min_order(amount=51.0, price=1.0, min_amount=0.001, min_notional=50.0, is_reduce_only=False)
        # BTC reduce_only order of $10 -> Allowed!
        assert is_above_min_order(amount=10.0, price=1.0, min_amount=0.001, min_notional=50.0, is_reduce_only=True)

        # ETH expanding order of $15 (below $20) -> Rejected
        assert not is_above_min_order(amount=15.0, price=1.0, min_amount=0.01, min_notional=20.0, is_reduce_only=False)
        # ETH expanding order of $25 -> Allowed
        assert is_above_min_order(amount=25.0, price=1.0, min_amount=0.01, min_notional=20.0, is_reduce_only=False)

        # SOL expanding order of $4 (below $5) -> Rejected
        assert not is_above_min_order(amount=4.0, price=1.0, min_amount=0.1, min_notional=5.0, is_reduce_only=False)
        # SOL expanding order of $6 -> Allowed
        assert is_above_min_order(amount=6.0, price=1.0, min_amount=0.1, min_notional=5.0, is_reduce_only=False)

        # Test compute_order_amount enforces min_notional on expanding orders
        amt = compute_order_amount(target_value_usd=40.0, price=60000.0, amount_precision=4, min_amount=0.0001, min_notional=50.0, is_reduce_only=False)
        assert amt is None

        # Test compute_order_amount allows reduce_only orders below min_notional
        amt_close = compute_order_amount(target_value_usd=10.0, price=60000.0, amount_precision=4, min_amount=0.0001, min_notional=50.0, is_reduce_only=True)
        assert amt_close is not None and amt_close > 0.0

    def test_step_size_truncation_never_rounds_up(self):
        """
        Verify that amount truncation strictly truncates to step size multiples
        and never rounds up, preventing -1111 LOT_SIZE error or exceeding available margin.
        """
        # Step size 0.001: 0.1239 -> 0.123
        truncated = truncate_to_step_size(0.1239, step_size=0.001, precision=3)
        assert truncated == 0.123

        # Step size 0.05: 0.17 -> 0.15
        truncated = truncate_to_step_size(0.17, step_size=0.05, precision=2)
        assert truncated == 0.15

        # Floating point imprecision tolerance: 0.30000000000000004 -> 0.3
        truncated = truncate_to_step_size(0.30000000000000004, step_size=0.1, precision=1)
        assert truncated == 0.3

    def test_hedge_mode_dual_side_positions(self):
        """
        Verify that when Binance account is in Hedge Mode (Dual-Side Position):
        - Opening LONG order passes positionSide="LONG" and hedged=True
        - Closing LONG order (reduce_only SELL) passes positionSide="LONG"
        - Opening SHORT order passes positionSide="SHORT" and hedged=True
        - Closing SHORT order (reduce_only BUY) passes positionSide="SHORT"
        This eliminates Binance error: -4061 Order's position side does not match user's setting.
        """
        mock_ccxt = MagicMock()
        mock_ccxt.create_order.return_value = {
            "id": "hedge_1",
            "status": "closed",
            "filled": 0.05,
            "average": 60000.0,
        }
        config = ExchangeConfig(market_type="future")
        gw = ExchangeGateway(config, run_mode=MagicMock())
        gw._initialized = True
        gw._exchange = mock_ccxt
        gw._is_hedged = True  # Account is in Dual-Side (Hedge) Mode
        gw.amount_to_precision = MagicMock(return_value=0.05)
        gw.price_to_precision = MagicMock(return_value=60000.0)

        # 1. Opening LONG order (BUY, reduce_only=False)
        buy_long = OrderIntent(
            client_order_id="hl_open",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=0.05,
            price=60000.0,
            reduce_only=False,
        )
        gw.create_order(buy_long)
        _, kwargs = mock_ccxt.create_order.call_args
        assert kwargs["params"]["hedged"] is True
        assert kwargs["params"]["positionSide"] == "LONG"

        # 2. Closing LONG order (SELL, reduce_only=True)
        sell_close = OrderIntent(
            client_order_id="hl_close",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            amount=0.05,
            price=60000.0,
            reduce_only=True,
        )
        gw.create_order(sell_close)
        _, kwargs = mock_ccxt.create_order.call_args
        assert kwargs["params"]["positionSide"] == "LONG"
        assert kwargs["params"]["reduceOnly"] is True

        # 3. Opening SHORT order (SELL, reduce_only=False)
        sell_short = OrderIntent(
            client_order_id="hs_open",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            amount=0.05,
            price=60000.0,
            reduce_only=False,
        )
        gw.create_order(sell_short)
        _, kwargs = mock_ccxt.create_order.call_args
        assert kwargs["params"]["positionSide"] == "SHORT"

        # 4. Closing SHORT order (BUY, reduce_only=True)
        buy_close = OrderIntent(
            client_order_id="hs_close",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=0.05,
            price=60000.0,
            reduce_only=True,
        )
        gw.create_order(buy_close)
        _, kwargs = mock_ccxt.create_order.call_args
        assert kwargs["params"]["positionSide"] == "SHORT"
        assert kwargs["params"]["reduceOnly"] is True

    def test_binance_minus_2022_automatic_recovery(self):
        """
        Verify that if Binance returns -2022 (ReduceOnly Order is rejected):
        If the position is already 0, ExchangeGateway gracefully resolves it as FILLED
        without crashing the bot cycle.
        """
        import ccxt
        mock_ccxt = MagicMock()
        # Raise -2022 from CCXT
        mock_ccxt.create_order.side_effect = ccxt.InvalidOrder(
            'binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}'
        )
        # Position query shows contracts are already 0.0
        mock_ccxt.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.0}
        ]

        config = ExchangeConfig(market_type="future")
        gw = ExchangeGateway(config, run_mode=MagicMock())
        gw._initialized = True
        gw._exchange = mock_ccxt
        gw.amount_to_precision = MagicMock(return_value=0.05)

        intent = OrderIntent(
            client_order_id="rec_1",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            amount=0.05,
            reduce_only=True,
        )

        result = gw.create_order(intent)
        assert result.status == OrderStatus.FILLED
        assert "Position already closed on exchange" in result.error_message
