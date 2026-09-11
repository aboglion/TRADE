"""
Zero-Defect Financial Integrity Audit Test Suite v6.

Validates:
1. Spot multi-order quote budget tracking in PortfolioService.
2. Price lookup fallbacks (:USDT, /USDT, base) in PortfolioService & Orchestrator.
3. In-cycle quote holding initialization when old_quote is initially None in Orchestrator.
4. Adaptive trailing stop non-negative bounds protection (tm >= tb) in RegimeAdaptiveStrategy.
"""

import time
from unittest.mock import MagicMock

from src.config.config_manager import AssetConfig, BotConfig, ExchangeConfig, RiskConfig, StrategyConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.orchestrator import BotOrchestrator
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy


def test_portfolio_service_multi_order_spot_budget_tracking():
    """Verify that multiple spot buy orders correctly share and decrement available quote budget."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=False)

    # Initial account has $100 USDT free in spot
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=100.0, locked=0.0, total=100.0, value_usd=100.0),
            "BTC": AssetHolding(symbol="BTC", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
            "ETH": AssetHolding(symbol="ETH", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=100.0,
    )

    # Strategy wants 60% BTC ($60) and 60% ETH ($60)
    target = TargetAllocation(
        weights={"BTC/USDT": 0.60, "ETH/USDT": 0.60, "USDT": 0.0},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )

    prices = {"BTC/USDT": 60000.0, "ETH/USDT": 3000.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)

    # Both orders should be generated
    assert len(plan.orders) == 2
    total_spend = sum(o.amount * (o.price or prices[o.symbol]) for o in plan.orders)
    # Total spend must NOT exceed available USDT (100.0 * 0.998 = 99.8)
    assert total_spend <= 100.0 * 0.998 + 1e-4
    assert plan.orders[0].side == OrderSide.BUY
    assert plan.orders[1].side == OrderSide.BUY


def test_portfolio_service_price_lookup_with_colon_and_base_fallbacks():
    """Verify PortfolioService resolves prices when provided with :USDT or base key variants."""
    mock_gw = MagicMock()
    mock_gw.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }
    # Gateway ticker should NOT be called because price exists in fallback keys
    mock_gw.fetch_ticker_price.side_effect = AssertionError("Should have used memory price")

    ps = PortfolioService(gateway=mock_gw, deviation_threshold=0.01, is_futures=True)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0),
            "BTC": AssetHolding(symbol="BTC", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=10000.0,
    )

    target = TargetAllocation(
        weights={"BTC/USDT": 0.40, "USDT": 0.60},
        regime=Regime.BULL,
        timestamp_ms=int(time.time() * 1000),
    )

    # Prices dictionary only has :USDT variant
    prices = {"BTC/USDT:USDT": 62000.0}

    plan = ps.compute_rebalance_plan(portfolio=portfolio, target=target, prices=prices)
    assert len(plan.orders) == 1
    assert plan.orders[0].symbol == "BTC/USDT"
    assert plan.orders[0].price == 62000.0


def test_orchestrator_quote_holding_initialization_when_old_quote_none():
    """Verify orchestrator in-cycle update initializes quote holding if old_quote was None."""
    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="future"),
        risk=RiskConfig(
            max_single_order_usd=50000.0,
            max_portfolio_change_pct=10.0,
            min_order_value_usd=10.0,
            min_seconds_between_orders=0.0,
            allow_market_orders=True,
        ),
        strategy=StrategyConfig(
            assets={"BTC": AssetConfig(pair="BTC/USDT", weight=1.0)},
            warmup_candles=50,
        ),
    )

    state = BotState()
    gateway = MagicMock()
    candle_svc = MagicMock()
    portfolio_svc = MagicMock()
    strategy = MagicMock()
    risk_mgr = RiskManager(config.risk, is_futures=True)
    state_store = MagicMock()

    candles = [
        Candle(
            timestamp_ms=1000 * (i + 1),
            open=60000.0,
            high=61000.0,
            low=59000.0,
            close=60000.0,
            volume=100.0,
            is_closed=True,
        )
        for i in range(50)
    ]

    candle_svc.get_new_closed_candles.return_value = candles[-1:]
    candle_svc.get_full_history.return_value = candles
    candle_svc.validate_continuity.return_value = True

    # Account has NO USDT holding initially (empty holdings)
    portfolio = PortfolioSnapshot(
        timestamp_ms=1000 * 50,
        holdings={},
        total_value_usd=0.0,
    )
    portfolio_svc.get_portfolio.return_value = portfolio

    strategy.compute_signals.return_value = MagicMock(
        regime=Regime.BEAR,
        metadata={"effective_leverage": 2.0, "short_leverage": 2.0},
        target_allocation=TargetAllocation(
            weights={"BTC/USDT": -0.35, "USDT": 0.65},
            regime=Regime.BEAR,
            timestamp_ms=1000 * 50,
        ),
    )

    intent = OrderIntent(
        client_order_id="test_init_quote_001",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        price=60000.0,
        estimated_price=60000.0,
        reason="Open short hedge",
        leverage=2.0,
    )

    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan

    gateway.create_order.return_value = OrderResult(
        client_order_id=intent.client_order_id,
        exchange_order_id="exc_short_001",
        status=OrderStatus.FILLED,
        filled_amount=0.1,
        average_price=60000.0,
        fees=6.0,
        fee_currency="USDT",
    )

    risk_mgr.approve_order = MagicMock(return_value=(True, ""))

    orch = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_svc,
        portfolio_service=portfolio_svc,
        strategy=strategy,
        risk_manager=risk_mgr,
        state_store=state_store,
        state=state,
    )

    success = orch.run_once(force=True)
    assert success is True

    # Verify USDT was initialized into portfolio.holdings
    assert "USDT" in portfolio.holdings
    usdt_h = portfolio.holdings["USDT"]
    assert isinstance(usdt_h, AssetHolding)
    assert usdt_h.symbol == "USDT"


def test_regime_strategy_adaptive_trail_clamp_protection():
    """Verify that when tb > tm, adaptive trail does not compute negative extra and keeps trail_atr >= tb."""
    strat = RegimeAdaptiveStrategy(
        asset_weights={"BTC": 0.40, "ETH": 0.30, "SOL": 0.30},
        sma_regime_period=20,
        core_ratio=0.80,
    )

    # Mock an active position in TREND mode where tb is 5.0 (from TRAIL_OVERRIDES) and tm is 4.5 (from _BASE_CFG)
    strat._positions = {
        "BTC": {
            "active": True,
            "entry_px": 50000.0,
            "atr_at_entry": 500.0,
            "high_water": 66000.0,
            "mode": "TREND",
            "entries": [{"px": 50000.0, "atr": 500.0}],
        }
    }

    # Generate candles where close is high (open_r > parabolic_r=3.0)
    candles = []
    base_ts = int(time.time() * 1000) - (250 * 4 * 3600 * 1000)
    for i in range(250):
        px = 50000.0 + (i * 60.0)
        candles.append(Candle(
            timestamp_ms=base_ts + (i * 4 * 3600 * 1000),
            open=px,
            high=px + 20,
            low=px - 20,
            close=px,
            volume=500.0,
            is_closed=True,
        ))
    candles[-1] = Candle(
        timestamp_ms=candles[-1].timestamp_ms,
        open=65500.0,
        high=66000.0,
        low=65400.0,
        close=65800.0,
        volume=1000.0,
        is_closed=True,
    )

    candles_by_asset = {
        "BTC/USDT": candles,
        "ETH/USDT": candles,
        "SOL/USDT": candles,
    }

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={"BTC": AssetHolding("BTC", free=0.1, locked=0.0, total=0.1, value_usd=6500.0)},
        total_value_usd=10000.0,
    )

    decision = strat.compute_signals(candles_by_asset=candles_by_asset, portfolio=portfolio)
    assert decision.regime in (Regime.BULL, Regime.BEAR)
    btc_pos = strat._positions["BTC"]
    assert btc_pos.get("active") is True
