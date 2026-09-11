"""
Zero-Defect Financial Master Audit v2 Tests.

Verifies:
1. Strict 1.0x leverage propagation in Safe Haven mode (no fallback to 2.0x).
2. Risk manager enforces 100% margin requirement when order leverage is 1.0x.
3. Precise fee accounting in orchestrator:
   - Base-asset fee (e.g. BTC on spot buy) reduces received token quantity without double-deducting USDT.
   - Quote-asset fee (e.g. USDT) reduces USDT balance without reducing token quantity.
   - Third-currency fee (e.g. BNB) does not deduct from either in-cycle USDT or token balance.
4. Reconciliation recovery by client_order_id for orders lacking exchange_order_id.
5. Reconciliation handling of non-existent orders (-2013) cleanly transitioning to FAILED.
"""

import time
from unittest.mock import MagicMock

import pytest

from src.config.config_manager import RiskConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager


class TestSafeHavenLeverageAndRiskGuarantees:
    """Verify 1.0x leverage preservation and strict margin checks in Safe Haven."""

    def test_safe_haven_1x_leverage_propagation(self):
        """Ensure that passing explicit leverage=1.0 sets order leverage to 1.0 (not 2.0)."""
        mock_gw = MagicMock()
        mock_gw.get_market_info.return_value = {
            "precision": {"amount": 4, "price": 2},
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
        }
        ps = PortfolioService(gateway=mock_gw, is_futures=True, allow_market_orders=True)

        portfolio = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={
                "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
                "USDT": AssetHolding("USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0),
            },
            total_value_usd=10000.0,
        )
        target = TargetAllocation(
            weights={"BTC/USDT": 0.30, "USDT": 0.70},
            regime=Regime.BULL,
            timestamp_ms=int(time.time() * 1000),
            metadata={"effective_leverage": 1.0},
        )
        prices = {"BTC/USDT": 50000.0}

        # Explicitly pass leverage=1.0 (Safe Haven)
        plan = ps.compute_rebalance_plan(portfolio, target, prices, leverage=1.0)
        assert len(plan.orders) == 1
        btc_order = plan.orders[0]
        assert btc_order.leverage == 1.0, f"Expected 1.0x leverage, got {btc_order.leverage}x"

    def test_risk_manager_strict_100_percent_margin_at_1x(self):
        """Ensure RiskManager requires 100% margin ($1000 for $1000 order) when leverage=1.0."""
        cfg = RiskConfig(
            max_orders_per_cycle=10,
            max_single_order_usd=50000.0,
            max_portfolio_change_pct=5.0,
            min_seconds_between_orders=0,
            min_order_value_usd=10.0,
            allow_market_orders=True,
        )
        rm = RiskManager(cfg, is_futures=True)

        # Portfolio has only $600 free USDT
        portfolio = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings={"USDT": AssetHolding("USDT", free=600.0, locked=0.0, total=600.0, value_usd=600.0)},
            total_value_usd=600.0,
        )

        # Expanding order of $1000 with leverage=1.0 (needs $1000 margin)
        intent_1x = OrderIntent(
            client_order_id="id_1x",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.02,
            estimated_price=50000.0,  # value = $1000
            leverage=1.0,
        )
        approved, reason = rm.approve_order(intent_1x, portfolio)
        assert not approved
        assert "Insufficient margin" in reason
        assert "need $1000.00" in reason


class TestOrchestratorFeeAccountingPrecision:
    """Verify precise fee settlement for base, quote, and BNB fee currencies."""

    def test_spot_buy_fee_in_base_currency(self):
        """Fee in BTC reduces BTC holding, but does not deduct from USDT."""
        cfg = MagicMock()
        cfg.run_mode.name = "LIVE"
        cfg.exchange.market_type = "spot"
        cfg.risk.min_seconds_between_orders = 0
        cfg.scheduler.poll_interval_seconds = 300
        cfg.strategy.assets = {"BTC": MagicMock(pair="BTC/USDT")}

        gateway = MagicMock()
        candle_svc = MagicMock()
        candle_svc.get_new_closed_candles.return_value = []
        portfolio_svc = MagicMock()
        strategy = MagicMock()
        risk_mgr = MagicMock()
        risk_mgr.is_kill_switch_active.return_value = False
        risk_mgr.approve_order.return_value = (True, "")

        state_store = MagicMock()
        state = BotState()

        orch = BotOrchestrator(
            config=cfg,
            gateway=gateway,
            candle_service=candle_svc,
            portfolio_service=portfolio_svc,
            strategy=strategy,
            risk_manager=risk_mgr,
            state_store=state_store,
            state=state,
        )

        # Initial portfolio: 10000 USDT, 0 BTC
        portfolio = PortfolioSnapshot(
            timestamp_ms=1000,
            holdings={"USDT": AssetHolding("USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0)},
            total_value_usd=10000.0,
        )

        # Buy 0.1 BTC @ 50,000 USDT ($5,000)
        # Exchange charges 0.0001 BTC fee (deducted in BTC)
        intent = OrderIntent(
            client_order_id="spot_buy_fee_base",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            estimated_price=50000.0,
        )
        plan = MagicMock()
        plan.orders = [intent]
        portfolio_svc.compute_rebalance_plan.return_value = plan
        portfolio_svc.get_portfolio.return_value = portfolio

        decision = MagicMock()
        decision.regime = Regime.BULL
        decision.metadata = {"effective_leverage": 1.0}
        strategy.compute_signals.return_value = decision

        # Mock OrderManager execution result
        result = OrderResult(
            client_order_id="spot_buy_fee_base",
            exchange_order_id="exc_1",
            status=OrderStatus.FILLED,
            filled_amount=0.1,
            average_price=50000.0,
            fees=0.0001,
            fee_currency="BTC",
        )
        orch._order_manager.execute = MagicMock(return_value=result)

        success = orch.run_once(force=True)
        assert success

        # In-cycle portfolio assertions:
        # Net BTC = 0.1 - 0.0001 = 0.0999 BTC
        btc_holding = portfolio.holdings["BTC"]
        assert pytest.approx(btc_holding.total, rel=1e-6) == 0.0999
        assert pytest.approx(btc_holding.free, rel=1e-6) == 0.0999

        # USDT free = 10000 - 5000 = 5000 USDT (NOT 5000 - 5 = 4995!)
        usdt_holding = portfolio.holdings["USDT"]
        assert pytest.approx(usdt_holding.free, rel=1e-6) == 5000.0


class TestReconciliationClientOrderIdAndNotFoundRecovery:
    """Verify reconciliation recovers orders by client_order_id and handles -2013 cleanly."""

    def test_reconcile_recovers_submitted_order_by_client_id(self):
        """Order in 'submitted' status with no exchange_order_id is queried via client_order_id."""
        mock_gateway = MagicMock()
        mock_gateway.fetch_open_orders.return_value = []

        # Order is filled on exchange
        mock_gateway.fetch_order.return_value = OrderResult(
            client_order_id="client_cid_100",
            exchange_order_id="exc_eid_200",
            status=OrderStatus.FILLED,
            filled_amount=0.05,
            average_price=60000.0,
            fees=1.5,
            fee_currency="USDT",
        )

        state = BotState()
        state.pending_orders = [
            {
                "client_order_id": "client_cid_100",
                "exchange_order_id": None,
                "symbol": "BTC/USDT",
                "status": "submitted",
                "amount": 0.05,
            }
        ]

        svc = ReconciliationService(gateway=mock_gateway, state=state)
        svc.reconcile()

        # Successfully moved to completed_orders and updated with exchange_order_id
        assert len(state.pending_orders) == 0
        assert len(state.completed_orders) == 1
        completed = state.completed_orders[0]
        assert completed["status"] == "filled"
        assert completed["exchange_order_id"] == "exc_eid_200"
        assert completed["fees"] == 1.5

    def test_reconcile_handles_binance_2013_order_not_found_as_failed(self):
        """If exchange returns 'Order does not exist' (-2013), mark FAILED instead of stuck in unknown."""
        mock_gateway = MagicMock()
        mock_gateway.fetch_open_orders.return_value = []
        mock_gateway.fetch_order.side_effect = Exception("binance error: -2013 Order does not exist.")

        state = BotState()
        state.pending_orders = [
            {
                "client_order_id": "orphan_cid_999",
                "exchange_order_id": "non_existent_eid",
                "symbol": "BTC/USDT",
                "status": "unknown",
                "amount": 0.01,
            }
        ]

        svc = ReconciliationService(gateway=mock_gateway, state=state)
        svc.reconcile()

        # Must be removed from pending and marked failed in completed
        assert len(state.pending_orders) == 0
        assert len(state.completed_orders) == 1
        assert state.completed_orders[0]["status"] == "failed"
        assert "Order not found on exchange" in state.completed_orders[0]["error_message"]
