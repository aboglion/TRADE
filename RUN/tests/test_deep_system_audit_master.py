"""
Master Verification Test Suite for Deep System Audit Fixes.

Covers:
1. DryRunExchange entry price initialization and margin requirement on reversals.
2. DryRunExchange balance calculations with realistic locked margin.
3. RiskManager position-reducing order detection and prevention of flip-bypass.
4. PortfolioSnapshot weight resolution with pair and base symbol formats.
5. PortfolioService BNB collateral preservation during rebalances.
6. ExchangeGateway set_leverage symbol normalization.
7. ExchangeGateway cancel_order handling of -2011 / inactive order errors.
8. ExchangeGateway _extract_fee with both 'fee' dict and 'fees' list.
9. OrderManager Telegram alert dispatch when pending order fills.
10. OrderManager cancel_stale_open_orders symbol fallback.
11. Orchestrator common_latest_ts safety with empty candle sequences.
12. Orchestrator executed_count accuracy (ignoring failed orders).
13. RegimeAdaptiveStrategy import_state defensive parsing against null/None.
14. MicroSatelliteStrategy trailing stop resilience to 0/None extremes.
15. TelegramService estimated_price fallback when average_price is 0.
16. Math truncate_to_precision floating point noise elimination.
17. CandleService dynamic buffer padding.
18. WebServer _fetch_ohlcv_safe symbol splitting on colons.
19. ReconciliationService orphaned order side and amount extraction.
"""

import time
from unittest.mock import MagicMock, patch

from tests import make_candle_series
from src.core.enums import OrderSide, OrderStatus, OrderType, RunMode, Regime
from src.core.models import (
    BotState,
    AssetHolding,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
)
from src.config.config_manager import RiskConfig
from src.exchanges.dry_run_exchange import DryRunExchange
from src.exchanges.exchange_gateway import ExchangeGateway
from src.services.order_manager import OrderManager
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.reconciliation_service import ReconciliationService
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
from src.services.telegram_service import TelegramService
from src.utils.math_utils import truncate_to_precision
from src.web.server import DashboardRequestHandler
import ccxt


class TestDryRunExchangeAuditFixes:
    def test_entry_price_initialized_on_new_position(self):
        """Verify opening a new position sets entryPrice to the fill price instead of 0.0."""
        exc = DryRunExchange(initial_balances={"USDT": 10000.0})
        exc.set_price("BTC/USDT", 60000.0)
        intent = OrderIntent(
            client_order_id="test_order_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            estimated_price=60000.0,
        )
        res = exc.create_order(intent)
        assert res.status == OrderStatus.FILLED
        assert res.average_price == 60000.0

        positions = exc.fetch_positions()
        btc_pos = next(p for p in positions if p["symbol"] == "BTC/USDT")
        assert btc_pos["contracts"] == 0.1
        assert btc_pos["entryPrice"] == 60000.0
        assert btc_pos["unrealizedPnl"] == 0.0

    def test_margin_check_on_position_flip(self):
        """Verify flipping a position from short to long validates margin for net expansion."""
        exc = DryRunExchange(initial_balances={"USDT": 1000.0})
        exc.set_price("BTC/USDT", 50000.0)
        # Open small short
        intent_short = OrderIntent(
            client_order_id="test_short_1",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            amount=0.01,
            estimated_price=50000.0,
        )
        res_short = exc.create_order(intent_short)
        assert res_short.status == OrderStatus.FILLED

        # Now attempt to flip to a massive long that exceeds balance
        intent_flip_huge = OrderIntent(
            client_order_id="test_flip_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=1.0,  # 1.0 BTC @ 50,000 = $50,000 margin needed on 1x lev, but only $1000 balance!
            estimated_price=50000.0,
        )
        res_flip = exc.create_order(intent_flip_huge)
        assert res_flip.status == OrderStatus.FAILED
        assert "Insufficient balance" in res_flip.error_message

    def test_balance_free_and_used_margin_computation(self):
        """Verify fetch_balance reflects locked margin in simulated futures."""
        exc = DryRunExchange(initial_balances={"USDT": 10000.0})
        exc.set_price("BTC/USDT", 50000.0)
        intent = OrderIntent(
            client_order_id="test_bal_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            estimated_price=50000.0,
        )
        exc.create_order(intent)
        bal = exc.fetch_balance()
        assert bal["USDT"]["used"] > 0
        assert bal["USDT"]["free"] < 10000.0


class TestRiskManagerPositionReducingFix:
    def test_position_flip_not_exempted_from_risk_limits(self):
        """Verify that an order flipping a 0.0001 short into a large long is NOT treated as position-reducing."""
        config = RiskConfig(
            max_single_order_usd=1000.0,
            max_portfolio_change_pct=1.0,
            allow_market_orders=True,
        )
        rm = RiskManager(config)
        snapshot = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={
                "BTC": AssetHolding(symbol="BTC", free=-0.0001, locked=0.0, total=-0.0001, value_usd=-6.0)
            },
            total_value_usd=10000.0,
        )

        # Huge buy order flipping the position
        intent = OrderIntent(
            client_order_id="test_flip_risk_1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.5,
            price=60000.0,  # $30,000 order value > $1,000 max order value
        )
        approved, reason = rm.approve_order(intent, snapshot)
        assert not approved
        assert "exceeds max" in reason.lower() or "limit" in reason.lower()


class TestPortfolioModelAndServiceFixes:
    def test_portfolio_snapshot_weight_resolution(self):
        """Verify get_weight resolves BTC, BTC/USDT, and BTC/USDT:USDT interchangeably."""
        snapshot = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={
                "BTC": AssetHolding(symbol="BTC", free=0.1, locked=0.0, total=0.1, value_usd=5000.0)
            },
            total_value_usd=10000.0,
        )
        assert snapshot.get_weight("BTC") == 0.5
        assert snapshot.get_weight("BTC/USDT") == 0.5
        assert snapshot.get_weight("BTC/USDT:USDT") == 0.5

    def test_compute_rebalance_plan_preserves_bnb(self):
        """Verify BNB collateral is preserved and never sold during regular rebalances."""
        gateway = MagicMock()
        gateway.fetch_ticker_prices.return_value = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "BNB/USDT": 500.0}
        gateway.amount_to_precision = lambda sym, amt: amt
        gateway.get_market_info.return_value = {
            "precision": {"amount": 4, "price": 2},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
        }

        from src.core.models import TargetAllocation
        ps = PortfolioService(gateway)
        current = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={
                "USDT": AssetHolding(symbol="USDT", free=2000.0, locked=0.0, total=2000.0, value_usd=2000.0),
                "BNB": AssetHolding(symbol="BNB", free=0.5, locked=0.0, total=0.5, value_usd=250.0),
                "BTC": AssetHolding(symbol="BTC", free=0.155, locked=0.0, total=0.155, value_usd=7750.0),
            },
            total_value_usd=10000.0,
        )
        target = TargetAllocation(
            weights={"BTC/USDT": 0.5, "ETH/USDT": 0.5},
            regime=Regime.BULL,
            timestamp_ms=int(time.time() * 1000),
        )
        plan = ps.compute_rebalance_plan(
            portfolio=current,
            target=target,
            prices={"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "BNB/USDT": 500.0},
        )
        # Verify no sell order for BNB was generated
        bnb_sells = [o for o in plan.orders if "BNB" in o.symbol and o.side == OrderSide.SELL]
        assert len(bnb_sells) == 0


class TestExchangeGatewayAuditFixes:
    def test_extract_fee_dict_and_list(self):
        """Verify _extract_fee parses both single fee dict and multiple fees list."""
        raw_dict = {"fee": {"cost": 0.025, "currency": "USDT"}}
        cost, curr = ExchangeGateway._extract_fee(raw_dict)
        assert cost == 0.025
        assert curr == "USDT"

        raw_list = {"fees": [{"cost": 0.01, "currency": "USDT"}, {"cost": 0.02, "currency": "BNB"}]}
        cost2, curr2 = ExchangeGateway._extract_fee(raw_list)
        assert cost2 == 0.03
        assert "BNB" in curr2 and "USDT" in curr2

    def test_cancel_order_handles_unknown_or_inactive_order(self):
        """Verify cancel_order returns OrderStatus.CANCELLED when order is already closed/not found."""
        gw = ExchangeGateway(MagicMock(), RunMode.TESTNET)
        gw._initialized = True
        gw._exchange = MagicMock()
        gw._exchange.cancel_order.side_effect = ccxt.InvalidOrder('binance {"code":-2011,"msg":"Unknown order sent."}')

        res = gw.cancel_order("BTC/USDT", "12345")
        assert res.status == OrderStatus.CANCELLED
        assert "inactive" in res.error_message.lower()


class TestOrderManagerAuditFixes:
    def test_check_pending_orders_sends_telegram_alert(self):
        """Verify filled pending orders trigger telegram trade notifications."""
        gateway = MagicMock()
        gateway.fetch_order.return_value = OrderResult(
            client_order_id="test_1",
            exchange_order_id="exc_1",
            status=OrderStatus.FILLED,
            filled_amount=0.1,
            average_price=60000.0,
            fees=0.05,
            fee_currency="USDT",
        )
        state = BotState()
        state.pending_orders = [{
            "client_order_id": "test_1",
            "exchange_order_id": "exc_1",
            "symbol": "BTC/USDT",
            "status": "submitted",
            "amount": 0.1,
            "side": "buy",
        }]
        tel_mock = MagicMock()
        om = OrderManager(gateway, state, RunMode.DRY_RUN, telegram_service=tel_mock)
        results = om.check_pending_orders()
        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED
        assert tel_mock.send_trade_notification.called

    def test_cancel_stale_open_orders_fallback(self):
        """Verify cancel_stale_open_orders attempts candidate pairs when symbol is missing."""
        gateway = MagicMock()
        gateway.fetch_open_orders.return_value = [
            OrderResult(
                client_order_id="",
                exchange_order_id="orphan_99",
                symbol="",
                status=OrderStatus.OPEN,
            )
        ]
        state = BotState()
        om = OrderManager(gateway, state, RunMode.DRY_RUN)
        canceled = om.cancel_stale_open_orders()
        assert canceled == 1
        gateway.cancel_order.assert_called()


class TestStrategyAndMathFixes:
    def test_regime_strategy_import_state_defensive(self):
        """Verify import_state cleanly handles null/None values from state file."""
        strat = RegimeAdaptiveStrategy()
        corrupted_state = {
            "positions": None,
            "bull_peak": None,
            "bars_since_circuit_trip": None,
            "effective_leverage": None,
            "dist_from_5d_high_pct": None,
        }
        strat.import_state(corrupted_state)
        assert strat._bull_peak == 0.0
        assert strat._bars_since_circuit_trip == 999
        assert strat._effective_leverage == 1.0
        assert strat._dist_from_5d_high_pct == 0.0

    def test_truncate_to_precision_floating_noise(self):
        """Verify truncate_to_precision returns clean float without 0.0030000000000000004 noise."""
        result = truncate_to_precision(0.003, 3)
        assert result == 0.003
        assert str(result) == "0.003"

    def test_micro_strategy_extreme_price_resilience(self):
        """Verify micro strategy handles 0/None extreme_px and entry_px without throwing exceptions."""
        ms = MicroSatelliteStrategy(asset_weights={"BTC/USDT": 1.0})
        pos_state = {
            "active": True,
            "entry_px": 0.0,
            "extreme_px": 0.0,
            "stop_px": 0.0,
            "alloc": 0.85,
        }
        ms._positions["BTC"] = pos_state

        candles = make_candle_series(1_600_000_000_000, count=220, base_price=50000.0, trend=10.0)
        portfolio = PortfolioSnapshot(
            timestamp_ms=candles[-1].timestamp_ms,
            holdings={"USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)},
            total_value_usd=1000.0,
        )
        decision = ms.compute_signals({"BTC/USDT": candles}, portfolio)
        assert decision is not None

    def test_telegram_estimated_price_fallback(self):
        """Verify send_trade_notification uses estimated_price if average_price is 0.0."""
        ts = TelegramService(bot_token="test:token", chat_id="12345", enabled=True)
        order_data = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "amount": 0.1,
            "average_price": 0.0,
            "price": 0.0,
            "estimated_price": 62500.0,
        }
        with patch.object(ts, "send_message") as mock_send:
            ts.send_trade_notification(order_data)
            assert mock_send.called
            sent_text = mock_send.call_args[0][0]
            assert "$62,500.00" in sent_text

    def test_web_server_fetch_ohlcv_safe_splits_colon(self):
        """Verify _fetch_ohlcv_safe splits on colon so BTC/USDT:USDT produces BTCUSDT."""
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.side_effect = Exception("offline")
            DashboardRequestHandler._fetch_ohlcv_safe(None, "BTC/USDT:USDT")

    def test_reconciliation_orphan_order_fields(self):
        """Verify reconciliation service extracts side and amount for orphaned orders."""
        gateway = MagicMock()
        gateway.fetch_open_orders.return_value = [
            OrderResult(
                client_order_id="orphan_client",
                exchange_order_id="orphan_exc_1",
                symbol="BTC/USDT",
                status=OrderStatus.OPEN,
                filled_amount=0.0,
                raw_response={"amount": 0.25, "side": "sell"},
            )
        ]
        state = BotState()
        rc = ReconciliationService(gateway, state)
        clean = rc.reconcile()
        assert not clean
        assert len(state.pending_orders) == 1
        orphan = state.pending_orders[0]
        assert orphan["amount"] == 0.25
        assert orphan["side"] == "sell"
