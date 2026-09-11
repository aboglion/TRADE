"""
Ultimate System Audit Test Suite.
Verifies complete software stability, strategy accuracy, execution integrity,
precision, risk management, and network communication resilience.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.exceptions import ExchangeAuthError
from src.core.models import (
    AssetHolding,
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    StrategyDecision,
    TargetAllocation,
)
from src.exchanges.exchange_gateway import ExchangeGateway
from src.exchanges.dry_run_exchange import DryRunExchange
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.config.config_manager import BotConfig, ExchangeConfig, RiskConfig


def test_symbol_resolution_and_bare_names():
    """Verify ExchangeGateway resolves 'BTC', 'BTC/USDT', and 'BTC/USDT:USDT'."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.LIVE)
    mock_ccxt = MagicMock()
    mock_ccxt.markets = {
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}}},
        "ETH/USDT": {"symbol": "ETH/USDT", "limits": {"amount": {"min": 0.01}, "cost": {"min": 5.0}}},
    }
    gw._exchange = mock_ccxt
    gw._markets = mock_ccxt.markets
    gw._initialized = True

    # 1. Bare symbol 'BTC' -> resolves to 'BTC/USDT:USDT'
    info_btc = gw.get_market_info("BTC")
    assert info_btc["symbol"] == "BTC/USDT:USDT"

    # 2. 'BTC/USDT' -> resolves to 'BTC/USDT:USDT'
    info_btc_pair = gw.get_market_info("BTC/USDT")
    assert info_btc_pair["symbol"] == "BTC/USDT:USDT"

    # 3. 'ETH' -> resolves to 'ETH/USDT'
    info_eth = gw.get_market_info("ETH")
    assert info_eth["symbol"] == "ETH/USDT"


def test_portfolio_margin_fallback():
    """Verify that initialize falls back to standard futures if portfolio margin auth fails."""
    cfg = ExchangeConfig(name="binance", market_type="future", portfolio_margin=True)
    gw = ExchangeGateway(cfg, RunMode.LIVE)

    mock_ccxt = MagicMock()
    mock_ccxt.options = {"portfolioMargin": True}
    mock_ccxt.markets = {"BTC/USDT": {"symbol": "BTC/USDT"}}

    call_count = [0]
    def mock_load_markets():
        call_count[0] += 1
        if call_count[0] == 1:
            raise ExchangeAuthError("Binance Futures authentication failed (-2015: Invalid API-key/permissions).")
        return mock_ccxt.markets

    mock_ccxt.load_markets = MagicMock(side_effect=mock_load_markets)

    with patch("src.exchanges.exchange_gateway.ccxt.binance", return_value=mock_ccxt):
        gw.initialize()
        assert gw._initialized is True
        assert mock_ccxt.options.get("portfolioMargin") is False


def test_orchestrator_symbol_failure_isolation():
    """Verify that if order 1 fails for a symbol, subsequent order 2 for that symbol is skipped."""
    config = BotConfig()
    gateway = MagicMock()
    candle_service = MagicMock()
    portfolio_service = MagicMock()
    strategy = MagicMock()
    risk_manager = MagicMock()
    risk_manager.is_kill_switch_active.return_value = False
    state_store = MagicMock()
    state = BotState()

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_service,
        portfolio_service=portfolio_service,
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        state=state,
    )

    # Two orders for BTC/USDT: Close short (fails), Open long (should be skipped!)
    intent1 = OrderIntent(
        client_order_id="close_btc_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        price=60000.0,
        reason="Close previous position",
    )
    intent2 = OrderIntent(
        client_order_id="open_btc_2",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.02,
        price=60000.0,
        reason="Open new position",
    )

    # Order 1 returns FAILED
    result_failed = OrderResult(
        client_order_id="close_btc_1",
        status=OrderStatus.FAILED,
        error_message="Exchange rejected",
    )

    orchestrator._order_manager.execute = MagicMock(return_value=result_failed)
    risk_manager.approve_order = MagicMock(return_value=(True, ""))

    candles = [Candle(timestamp_ms=1000, open=60000, high=60100, low=59900, close=60000, volume=10, is_closed=True)]
    plan = MagicMock(orders=[intent1, intent2])
    decision = StrategyDecision(
        regime=Regime.BULL,
        target_allocation=TargetAllocation(weights={"BTC/USDT": 0.4}, regime=Regime.BULL, timestamp_ms=1000),
        signals=[],
        timestamp_ms=1000,
        metadata={"effective_leverage": 2.5},
    )

    portfolio_service.compute_rebalance_plan = MagicMock(return_value=plan)
    strategy.compute_signals = MagicMock(return_value=decision)
    portfolio_service.get_portfolio = MagicMock(return_value=PortfolioSnapshot(1000, {}, 1000.0))
    candle_service.get_new_closed_candles = MagicMock(return_value=candles)
    candle_service.get_full_history = MagicMock(return_value=candles)
    candle_service.validate_continuity = MagicMock()

    success = orchestrator.run_once(force=True)
    assert success is True

    # Check that execute was called ONLY ONCE for intent1; intent2 was safely skipped!
    assert orchestrator._order_manager.execute.call_count == 1
    orchestrator._order_manager.execute.assert_called_once_with(intent1)


def test_portfolio_service_rebalance_pair_symbols():
    """Verify that PortfolioService generates OrderIntents with full pair symbols."""
    gateway = MagicMock()
    gateway.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }
    gateway.fetch_ticker_price.return_value = 60000.0

    ps = PortfolioService(gateway, allow_market_orders=True, is_futures=True)

    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0),
        },
        total_value_usd=1000.0,
    )

    # Target allocation specifying bare asset 'BTC'
    target = TargetAllocation(
        weights={"BTC": 0.40, "USDT": 0.60},
        regime=Regime.BULL,
        timestamp_ms=1000,
    )

    plan = ps.compute_rebalance_plan(snapshot, target, prices={"BTC/USDT": 60000.0})
    assert len(plan.orders) == 1
    order = plan.orders[0]
    # Order symbol MUST be full pair 'BTC/USDT'
    assert order.symbol == "BTC/USDT"
    assert order.side == OrderSide.BUY
    assert order.amount > 0


def test_risk_manager_symbol_allowed_variants():
    """Verify that RiskManager allows futures symbol variants like 'BTC/USDT:USDT'."""
    cfg = RiskConfig(
        allowed_symbols=["BTC/USDT", "ETH/USDT"],
        banned_symbols=[],
    )
    rm = RiskManager(cfg)

    portfolio = PortfolioSnapshot(1000, {}, 1000.0)

    # Normal pair
    intent_std = OrderIntent(
        client_order_id="1", symbol="BTC/USDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, amount=0.01, price=60000.0,
    )
    ok_std, _ = rm._check_symbol_allowed(intent_std, portfolio)
    assert ok_std is True

    # Futures CCXT symbol with :USDT
    intent_fut = OrderIntent(
        client_order_id="2", symbol="BTC/USDT:USDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, amount=0.01, price=60000.0,
    )
    ok_fut, _ = rm._check_symbol_allowed(intent_fut, portfolio)
    assert ok_fut is True

    # Disallowed symbol
    intent_bad = OrderIntent(
        client_order_id="3", symbol="DOGE/USDT", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, amount=10.0, price=0.1,
    )
    ok_bad, reason = rm._check_symbol_allowed(intent_bad, portfolio)
    assert ok_bad is False
    assert "not in allowed list" in reason


def test_risk_manager_position_reduction_variants():
    """Verify _is_position_reducing_order handles futures symbols and prevents flip false positives."""
    cfg = RiskConfig()
    rm = RiskManager(cfg)

    portfolio = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "BTC": AssetHolding(symbol="BTC", free=0.0, locked=0.0, total=-0.05, value_usd=3000.0),
        },
        total_value_usd=10000.0,
    )

    # Buying 0.05 BTC/USDT:USDT to cover the -0.05 short is a reducing order
    intent_cover = OrderIntent(
        client_order_id="cov_1", symbol="BTC/USDT:USDT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, amount=0.05, price=60000.0,
    )
    assert rm._is_position_reducing_order(intent_cover, portfolio) is True

    # Buying 0.10 BTC/USDT expands into long, so is NOT purely reducing
    intent_expand = OrderIntent(
        client_order_id="exp_1", symbol="BTC/USDT:USDT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, amount=0.10, price=60000.0,
    )
    assert rm._is_position_reducing_order(intent_expand, portfolio) is False


def test_dry_run_exchange_leverage_and_short_realized_pnl():
    """Verify DryRunExchange correctly opens short, sets leverage, and realizes PnL on close."""
    dr = DryRunExchange(initial_balances={"USDT": 10000.0})
    dr.set_price("BTC/USDT", 60000.0)
    dr.set_leverage(2.0, "BTC/USDT")

    # 1. Open short 0.1 BTC ($6000 notional, requires $3000 margin at 2x)
    intent_short = OrderIntent(
        client_order_id="dr_short_1", symbol="BTC/USDT", side=OrderSide.SELL,
        order_type=OrderType.MARKET, amount=0.1, price=60000.0,
    )
    res1 = dr.create_order(intent_short)
    assert res1.status == OrderStatus.FILLED

    positions = dr.fetch_positions()
    assert len(positions) == 1
    assert positions[0]["contracts"] == -0.1
    assert positions[0]["entryPrice"] == 60000.0

    # 2. Price drops to $55,000 (profit on short)
    dr.set_price("BTC/USDT", 55000.0)
    pos_updated = dr.fetch_positions()[0]
    assert pos_updated["unrealizedPnl"] == pytest.approx(500.0, abs=1e-2)

    # 3. Close short by buying 0.1 BTC at $55,000
    intent_cover = OrderIntent(
        client_order_id="dr_cover_1", symbol="BTC/USDT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, amount=0.1, price=55000.0,
    )
    res2 = dr.create_order(intent_cover)
    assert res2.status == OrderStatus.FILLED

    # Position is closed
    pos_closed = dr.fetch_positions()
    assert len(pos_closed) == 0

    # Realized PnL is applied to USDT balance ($500 gain minus taker fees)
    bal = dr.fetch_balance()
    assert bal["USDT"]["total"] > 10480.0
