"""
Bulletproof System Integrity Tests.

Validates:
1. Thread-safe atomic persistence in JsonStateStore (concurrent read/write).
2. ExchangeGateway client order ID routing (origClientOrderId for fetch & cancel).
3. ExchangeGateway precision truncation zero-amount protection.
4. ExchangeGateway raw info fill fallback (cumQty / executedQty).
5. PortfolioService base symbol parsing with colon-delimited symbols (e.g. BTC:USDT, BTC/USDT:USDT).
6. BotOrchestrator intra-cycle dynamic portfolio balance update (sell proceeds funding subsequent buy).
7. RiskManager position-reducing orders bypassing max_orders_per_cycle.
8. RiskManager spot buy balance validation (rejecting order_value > free_usdt).
9. ReconciliationService fee tracking and Telegram trade notifications on resolved fills.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.config.config_manager import ExchangeConfig, RiskConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.exceptions import InvalidOrderError
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    RebalancePlan,
    StrategyDecision,
    TargetAllocation,
)
from src.exchanges.exchange_gateway import ExchangeGateway
from src.services.portfolio_service import PortfolioService
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore


def make_mock_gateway(mock_ccxt):
    cfg = ExchangeConfig()
    gw = ExchangeGateway(cfg, RunMode.DRY_RUN)
    gw._exchange = mock_ccxt
    gw._initialized = True
    gw._markets = {
        "BTC/USDT": {
            "precision": {"amount": 8, "price": 2},
            "limits": {"amount": {"min": 0.00001}},
        }
    }
    return gw


# ── 1. Thread Safety Test ─────────────────────────────────────────────────────

def test_json_state_store_concurrency(tmp_path):
    """Verify concurrent reads and writes do not corrupt the state file or crash."""
    state_path = str(tmp_path / "bot_state.json")
    store = JsonStateStore(state_path)

    # Initial state
    initial_state = BotState(session_fees={"USDT": 1.5})
    store.save_state(initial_state)

    errors = []

    def writer_worker(worker_id: int):
        for i in range(25):
            try:
                state = store.load_state()
                state.session_fees["USDT"] = state.session_fees.get("USDT", 0.0) + 0.1
                state.last_processed_candle_ts[f"BTC_{worker_id}"] = int(time.time() * 1000)
                store.save_state(state)
                time.sleep(0.005)
            except Exception as e:
                errors.append(f"Writer error: {e}")

    def reader_worker():
        for _ in range(50):
            try:
                state = store.load_state()
                assert isinstance(state.session_fees, dict)
                time.sleep(0.002)
            except Exception as e:
                errors.append(f"Reader error: {e}")

    threads = []
    for wid in range(3):
        threads.append(threading.Thread(target=writer_worker, args=(wid,)))
    for _ in range(4):
        threads.append(threading.Thread(target=reader_worker))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrency errors encountered: {errors}"
    final_state = store.load_state()
    assert final_state.session_fees["USDT"] >= 1.5


# ── 2. Client Order ID Routing ────────────────────────────────────────────────

def test_exchange_gateway_client_order_id_routing():
    """Verify non-numeric order IDs pass origClientOrderId to CCXT."""
    mock_ccxt = MagicMock()
    mock_ccxt.cancel_order.return_value = {
        "id": "12345",
        "clientOrderId": "bot_1710000000_0001",
        "status": "canceled",
        "filled": 0.0,
        "symbol": "BTC/USDT",
    }
    mock_ccxt.fetch_order.return_value = {
        "id": "12345",
        "clientOrderId": "bot_1710000000_0001",
        "status": "closed",
        "filled": 0.05,
        "price": 60000.0,
        "symbol": "BTC/USDT",
    }

    gateway = make_mock_gateway(mock_ccxt)

    # Cancel by client_order_id
    res_cancel = gateway.cancel_order("BTC/USDT", "bot_1710000000_0001")
    assert res_cancel.status == OrderStatus.CANCELLED
    mock_ccxt.cancel_order.assert_called_with(
        None, "BTC/USDT", params={"origClientOrderId": "bot_1710000000_0001"}
    )

    # Fetch by client_order_id
    res_fetch = gateway.fetch_order("BTC/USDT", "bot_1710000000_0001")
    assert res_fetch.status == OrderStatus.FILLED
    mock_ccxt.fetch_order.assert_called_with(
        None, "BTC/USDT", params={"origClientOrderId": "bot_1710000000_0001"}
    )


# ── 3. Precision Truncation Safeguard ─────────────────────────────────────────

def test_exchange_gateway_zero_amount_safeguard():
    """Verify create_order rejects an order if formatted amount is truncated to zero."""
    mock_ccxt = MagicMock()
    mock_ccxt.amount_to_precision.return_value = "0"

    gateway = make_mock_gateway(mock_ccxt)
    intent = OrderIntent(
        client_order_id="bot_zero_test",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.00000001,
        estimated_price=60000.0,
    )

    with pytest.raises(InvalidOrderError, match="formatted to 0.0"):
        gateway.create_order(intent)


# ── 4. Fallback Fill Amount from raw info ──────────────────────────────────────

def test_exchange_gateway_raw_info_fill_fallback():
    """Verify fill amount is recovered from info.cumQty when top-level CCXT filled is 0."""
    mock_ccxt = MagicMock()
    mock_ccxt.amount_to_precision.return_value = "0.05"
    mock_ccxt.create_order.return_value = {
        "id": "9999",
        "clientOrderId": "bot_test_raw_info",
        "status": "closed",
        "filled": 0.0,  # top-level CCXT field unpopulated
        "symbol": "BTC/USDT",
        "info": {
            "cumQty": "0.05",
            "avgPrice": "62000.0",
        },
    }

    gateway = make_mock_gateway(mock_ccxt)
    intent = OrderIntent(
        client_order_id="bot_test_raw_info",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.05,
        estimated_price=62000.0,
    )

    res = gateway.create_order(intent)
    assert res.status == OrderStatus.FILLED
    assert res.filled_amount == 0.05


# ── 5. PortfolioService Colon-delimited Base Symbol Extraction ─────────────────

def test_portfolio_service_colon_delimited_base_extraction():
    """Verify get_portfolio handles futures symbols like BTC:USDT and BTC/USDT:USDT."""
    mock_gateway = MagicMock()
    mock_gateway.fetch_balance.return_value = {
        "free": {"BTC": 1.0, "ETH": 2.0, "USDT": 1000.0},
        "total": {"BTC": 1.0, "ETH": 2.0, "USDT": 1000.0},
    }
    mock_gateway.fetch_positions.return_value = [
        {
            "symbol": "SOL/USDT:USDT",
            "contracts": 10.0,
            "notional": 1500.0,
            "entryPrice": 150.0,
            "unrealizedPnl": 50.0,
            "leverage": 2.0,
        }
    ]

    service = PortfolioService(mock_gateway, is_futures=True)
    prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0, "SOL/USDT": 150.0}

    portfolio = service.get_portfolio(prices)
    # Holdings should have "SOL" extracted cleanly without ":USDT" causing issues
    assert "SOL" in portfolio.holdings
    assert portfolio.holdings["SOL"].total == 10.0
    assert portfolio.holdings["SOL"].value_usd == 1500.0


# ── 6. Intra-Cycle Dynamic Balance Updates in Orchestrator ─────────────────────

def test_orchestrator_intra_cycle_balance_update():
    """
    Verify that selling an asset dynamically updates the in-memory portfolio's
    free USDT so that the subsequent BUY order passes risk checks instead of being rejected.
    """
    from src.config.config_manager import BotConfig, ExchangeConfig, RiskConfig, StrategyConfig
    from src.orchestrator import BotOrchestrator

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="spot"),
        strategy=StrategyConfig(timeframe="1h"),
        risk=RiskConfig(max_orders_per_cycle=6, max_single_order_usd=5000.0, min_order_value_usd=10.0, allow_market_orders=True),
    )

    mock_gateway = MagicMock()
    mock_data_provider = MagicMock()
    state = BotState()

    # Initial portfolio has 0 USDT and 1.0 BTC ($60,000)
    initial_portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding("USDT", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
            "BTC": AssetHolding("BTC", free=1.0, locked=0.0, total=1.0, value_usd=60000.0),
            "ETH": AssetHolding("ETH", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=60000.0,
    )

    prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0}

    # Rebalance plan: SELL 0.01 BTC ($600), then BUY 0.1 ETH ($300)
    sell_intent = OrderIntent(
        client_order_id="test_sell_btc",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=60000.0,
    )
    buy_intent = OrderIntent(
        client_order_id="test_buy_eth",
        symbol="ETH/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        estimated_price=3000.0,
    )

    target_alloc = TargetAllocation(weights={}, regime=Regime.BULL, timestamp_ms=int(time.time() * 1000))
    rebalance_plan = RebalancePlan(
        orders=[sell_intent, buy_intent],
        current_snapshot=initial_portfolio,
        target_allocation=target_alloc,
        total_deviation_pct=0.1,
    )

    with patch("src.orchestrator.PortfolioService") as mock_ps_cls, \
         patch("src.orchestrator.CandleService"), \
         patch("src.orchestrator.JsonStateStore"):

        mock_ps = MagicMock()
        mock_ps.get_portfolio.return_value = initial_portfolio
        mock_ps.compute_rebalance_plan.return_value = rebalance_plan
        mock_ps_cls.return_value = mock_ps

        mock_candle_service = MagicMock()
        mock_strategy = MagicMock()
        mock_state_store = MagicMock()
        risk_manager = RiskManager(config.risk, is_futures=False)

        orchestrator = BotOrchestrator(
            config=config,
            gateway=mock_gateway,
            candle_service=mock_candle_service,
            portfolio_service=mock_ps,
            strategy=mock_strategy,
            risk_manager=risk_manager,
            state_store=mock_state_store,
            state=state,
        )

        # Mock order execution results
        def mock_execute(intent):
            if intent.side == OrderSide.SELL:
                return OrderResult(
                    client_order_id=intent.client_order_id,
                    exchange_order_id="exc_sell",
                    status=OrderStatus.FILLED,
                    filled_amount=0.01,
                    average_price=60000.0,
                    fees=0.60,
                    fee_currency="USDT",
                )
            else:
                return OrderResult(
                    client_order_id=intent.client_order_id,
                    exchange_order_id="exc_buy",
                    status=OrderStatus.FILLED,
                    filled_amount=0.1,
                    average_price=3000.0,
                    fees=0.30,
                    fee_currency="USDT",
                )

        orchestrator._order_manager.execute = MagicMock(side_effect=mock_execute)
        orchestrator._candle_service.fetch_new_closed_candles = MagicMock(return_value={"BTC/USDT": [MagicMock(timestamp_ms=1000)]})
        orchestrator._candle_service.fetch_history = MagicMock(return_value={"BTC/USDT": [MagicMock(timestamp_ms=1000)]})
        orchestrator._candle_service.validate_continuity = MagicMock(return_value=True)
        decision = StrategyDecision(
            regime=Regime.BULL,
            target_allocation=target_alloc,
            signals=[],
            timestamp_ms=int(time.time() * 1000),
            metadata={"effective_leverage": 1.0},
        )
        orchestrator._strategy.compute_target = MagicMock(return_value=decision)

        # Run cycle
        orchestrator.run_once(force=True)

        # Verify BOTH orders were executed!
        assert orchestrator._order_manager.execute.call_count == 2
        calls = orchestrator._order_manager.execute.call_args_list
        assert calls[0][0][0].symbol == "BTC/USDT"
        assert calls[1][0][0].symbol == "ETH/USDT"


# ── 7. Position Reducing Orders Bypass Max Orders Per Cycle ───────────────────

def test_risk_manager_position_reducing_bypasses_max_orders():
    """Verify that a position-reducing order is NOT blocked even when max_orders_per_cycle is reached."""
    config = RiskConfig(max_orders_per_cycle=2)
    rm = RiskManager(config)

    # Set cycle order count to 2 (limit reached)
    rm._cycle_order_count = 2

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=1.0, locked=0.0, total=1.0, value_usd=60000.0),
            "USDT": AssetHolding("USDT", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
        },
        total_value_usd=65000.0,
    )

    # Non-reducing order should be rejected
    buy_intent = OrderIntent(
        client_order_id="buy_new",
        symbol="ETH/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.5,
        price=3000.0,
    )
    approved, reason = rm.approve_order(buy_intent, portfolio)
    assert not approved
    assert "Max orders per cycle reached" in reason

    # Position-reducing order (selling existing BTC) must be APPROVED
    sell_intent = OrderIntent(
        client_order_id="sell_reduce",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        amount=0.5,
        price=60000.0,
    )
    approved, reason = rm.approve_order(sell_intent, portfolio)
    assert approved, f"Expected approval but got reason: {reason}"


# ── 8. Spot Buy Balance Validation ───────────────────────────────────────────

def test_risk_manager_spot_buy_balance_validation():
    """Verify that a Spot BUY order is rejected if order value exceeds free USDT."""
    config = RiskConfig(min_order_value_usd=10.0, max_single_order_usd=5000.0)
    rm = RiskManager(config, is_futures=False)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding("USDT", free=50.0, locked=0.0, total=50.0, value_usd=50.0),
        },
        total_value_usd=50.0,
    )

    # Order value = $150 > free USDT ($50)
    big_buy = OrderIntent(
        client_order_id="big_buy",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.0025,
        price=60000.0,
    )
    approved, reason = rm.approve_order(big_buy, portfolio)
    assert not approved
    assert "Insufficient balance" in reason

    # Order value = $30 <= free USDT ($50)
    small_buy = OrderIntent(
        client_order_id="small_buy",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.0005,
        price=60000.0,
    )
    approved, reason = rm.approve_order(small_buy, portfolio)
    assert approved


# ── 9. Reconciliation Fee Tracking and Telegram Alert ────────────────────────

def test_reconciliation_service_fees_and_telegram_notification():
    """Verify ReconciliationService tracks fees and sends Telegram notification upon fill."""
    mock_gateway = MagicMock()
    mock_gateway.fetch_open_orders.return_value = []  # No open orders on exchange
    mock_gateway.fetch_order.return_value = OrderResult(
        client_order_id="bot_recon_test",
        exchange_order_id="exc_999",
        status=OrderStatus.FILLED,
        filled_amount=0.05,
        average_price=60000.0,
        fees=1.20,
        fee_currency="USDT",
    )

    state = BotState(
        pending_orders=[
            {
                "client_order_id": "bot_recon_test",
                "exchange_order_id": "exc_999",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.05,
                "status": "submitted",
            }
        ]
    )

    mock_tg = MagicMock()
    recon = ReconciliationService(
        mock_gateway,
        state,
        telegram_service=mock_tg,
        run_mode=RunMode.LIVE,
    )

    success = recon.reconcile()
    assert success

    # Fee should be recorded in session_fees
    assert state.session_fees.get("USDT") == 1.20

    # Order should be moved to completed_orders
    assert len(state.pending_orders) == 0
    assert len(state.completed_orders) == 1
    completed = state.completed_orders[0]
    assert completed["fees"] == 1.20
    assert completed["fee_currency"] == "USDT"

    # Telegram notification should have been sent
    mock_tg.send_trade_notification.assert_called_once()
