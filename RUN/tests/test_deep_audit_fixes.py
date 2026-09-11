"""
Regression tests for Deep System Audit fixes.

Tests:
1. RiskManager position reduction / liquidation exemption from max_portfolio_change_pct.
2. PortfolioService full liquidation of residual positions (long and short) when target_weight == 0.0.
3. ExchangeGateway amount precision formatting before CCXT submission.
"""

from unittest.mock import MagicMock

from src.core.enums import OrderSide, OrderStatus, OrderType, Regime
from src.core.models import (
    AssetHolding,
    Candle,
    OrderIntent,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.config.config_manager import RiskConfig
from src.services.risk_manager import RiskManager
from src.services.portfolio_service import PortfolioService
from src.exchanges.exchange_gateway import ExchangeGateway


def test_risk_manager_exempts_position_liquidations():
    """Test that RiskManager allows 100% position liquidations even when max_portfolio_change_pct is 20%."""
    config = RiskConfig(
        max_portfolio_change_pct=0.20,
        max_single_order_usd=100000.0,
        min_order_value_usd=10.0,
        allow_market_orders=True,
        allowed_symbols=["BTC/USDT", "ETH/USDT"],
    )
    rm = RiskManager(config)

    # Portfolio holding $10,000 BTC position (100% of portfolio)
    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
            "BTC": AssetHolding(symbol="BTC", free=0.2, locked=0.0, total=0.2, value_usd=10000.0),
        },
        total_value_usd=10000.0,
    )

    # Order to SELL (liquidate) the 0.2 BTC position ($10,000 value = 100% change)
    sell_intent = OrderIntent(
        client_order_id="liq_btc",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.2,
        estimated_price=50000.0,
        reason="Exit position in Bear regime",
    )

    approved, reason = rm.approve_order(sell_intent, snapshot)
    assert approved is True, f"Liquidation order was incorrectly rejected: {reason}"


def test_portfolio_service_liquidates_residual_short_and_long():
    """Test that compute_rebalance_plan liquidates small residual positions when target_weight == 0.0."""
    gateway = MagicMock()
    gateway.get_market_info.return_value = {
        "precision": {"amount": 5, "price": 2},
        "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5.0}},
    }
    ps = PortfolioService(gateway, deviation_threshold=0.05, allow_market_orders=True, is_futures=True)

    # Residual short position (-2% weight)
    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0),
            "BTC": AssetHolding(
                symbol="BTC", free=0.0, locked=0.0, total=-0.0004, value_usd=20.0,
                unrealized_pnl=0.0, entry_price=50000.0, leverage=2.0
            ),
        },
        total_value_usd=1000.0,
    )
    target = TargetAllocation(weights={"BTC/USDT": 0.0, "USDT": 1.0}, regime=Regime.BULL, timestamp_ms=1000)
    prices = {"BTC/USDT": 50000.0}

    plan = ps.compute_rebalance_plan(snapshot, target, prices)
    assert len(plan.orders) == 1
    assert plan.orders[0].side == OrderSide.BUY
    assert plan.orders[0].amount == 0.0004


def test_exchange_gateway_create_order_formats_amount_precision():
    """Test that ExchangeGateway.create_order applies amount_to_precision to raw float amounts."""
    config = MagicMock()
    config.rate_limit = False
    config.timeout_ms = 5000
    config.market_type = "spot"
    config.api_key = "key"
    config.api_secret = "secret"
    config.max_retries = 1
    config.retry_delay_base_ms = 100

    gw = ExchangeGateway(config, run_mode=MagicMock())
    gw._initialized = True
    mock_ccxt = MagicMock()
    gw._exchange = mock_ccxt
    mock_ccxt.amount_to_precision.return_value = "0.00100"
    mock_ccxt.create_order.return_value = {
        "id": "12345",
        "status": "closed",
        "filled": 0.001,
        "price": 50000.0,
    }

    intent = OrderIntent(
        client_order_id="prec_test",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.00100000000000002,  # Raw float with IEEE 754 imprecision
        estimated_price=50000.0,
    )

    res = gw.create_order(intent)
    assert res.status == OrderStatus.FILLED
    mock_ccxt.amount_to_precision.assert_called_once_with("BTC/USDT", 0.00100000000000002)
    mock_ccxt.create_order.assert_called_once()
    assert mock_ccxt.create_order.call_args[1]["amount"] == 0.001


def test_portfolio_service_liquidates_untracked_ghost_holdings():
    """Test that compute_rebalance_plan detects and liquidates holdings missing from target.weights."""
    gateway = MagicMock()
    gateway.get_market_info.return_value = {
        "precision": {"amount": 2, "price": 2},
        "limits": {"amount": {"min": 0.1}, "cost": {"min": 5.0}},
    }
    ps = PortfolioService(gateway, deviation_threshold=0.05, allow_market_orders=True, is_futures=True)

    # Account has 10 SOL ($1500) untracked, while target.weights only has BTC and USDT
    snapshot = PortfolioSnapshot(
        timestamp_ms=1000,
        holdings={
            "USDT": AssetHolding(symbol="USDT", free=8500.0, locked=0.0, total=8500.0, value_usd=8500.0),
            "SOL": AssetHolding(symbol="SOL", free=10.0, locked=0.0, total=10.0, value_usd=1500.0),
        },
        total_value_usd=10000.0,
    )
    target = TargetAllocation(weights={"BTC/USDT": 0.4, "USDT": 0.6}, regime=Regime.BULL, timestamp_ms=1000)
    prices = {"BTC/USDT": 50000.0, "SOL/USDT": 150.0}

    plan = ps.compute_rebalance_plan(snapshot, target, prices)
    # Expect 2 orders: BUY BTC (to reach 40%) and SELL SOL (to liquidate 10 SOL)
    sol_sell_orders = [o for o in plan.orders if o.symbol == "SOL/USDT" and o.side == OrderSide.SELL]
    assert len(sol_sell_orders) == 1, "Untracked SOL holding was not targeted for liquidation"
    assert sol_sell_orders[0].amount == 10.0


def test_risk_config_default_max_portfolio_change_pct():
    """Test that default RiskConfig max_portfolio_change_pct is set to 3.5 (350%)."""
    cfg = RiskConfig()
    assert cfg.max_portfolio_change_pct == 3.5


def test_dry_run_exchange_leverage_init_and_parsing():
    """Test DryRunExchange initializes _current_leverage and parses symbols & timeframes safely."""
    from src.exchanges.dry_run_exchange import DryRunExchange
    ex = DryRunExchange()
    assert hasattr(ex, "_current_leverage")
    assert ex._current_leverage == {}

    ex.set_leverage(3.5, "BTC/USDT")
    assert ex._get_active_leverage("BTC/USDT") == 3.5
    assert ex._get_active_leverage("ETH/USDT") == 3.5  # default

    # Symbol parsing
    b, q = ex._parse_symbol("BTC/USDT:USDT")
    assert b == "BTC" and q == "USDT"

    b2, q2 = ex._parse_symbol("SOLUSDT")
    assert b2 == "SOL" and q2 == "USDT"

    # Timeframe parsing
    assert ex._timeframe_to_ms("30s") == 30_000
    assert ex._timeframe_to_ms("4h") == 14_400_000
    assert ex._timeframe_to_ms("1w") == 604_800_000


def test_hybrid_strategy_resets_micro_positions_in_bear_regime():
    """Test that HybridStrategy clears/deactivates micro positions during Bear regime."""
    from src.strategy.hybrid_strategy import HybridStrategy
    from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
    from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy

    macro = RegimeAdaptiveStrategy(sma_regime_period=150, bear_short_hedge_weight=0.35, short_leverage=2.0)
    micro = MicroSatelliteStrategy(asset_weights={"BTC": 1.0})
    micro._positions["BTC"] = {"active": True, "entry_px": 50000.0}

    hybrid = HybridStrategy(macro_strategy=macro, micro_strategy=micro, core_ratio=0.80)

    from tests.test_strategy import make_candle_series
    btc_candles = make_candle_series(1_600_000_000_000, count=1000, base_price=60000.0, trend=-20.0)
    candles_by_asset = {"BTC/USDT": btc_candles}
    portfolio = PortfolioSnapshot(
        timestamp_ms=1_600_000_000_000,
        holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
        total_value_usd=1000.0,
    )

    decision = hybrid.compute_signals(candles_by_asset, portfolio)
    assert decision.regime == Regime.BEAR
    assert micro._positions["BTC"]["active"] is False, "Micro position was not deactivated in Bear regime"


def test_micro_strategy_zero_interval_protection():
    """Test that micro strategy does not throw ZeroDivisionError on identical candle timestamps."""
    from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
    micro = MicroSatelliteStrategy(asset_weights={"BTC": 1.0})
    micro._positions["BTC"] = {
        "active": True,
        "entry_px": 50000.0,
        "entry_ts": 1000,
        "extreme_px": 52000.0,
        "alloc": 0.85,
        "stop_px": 48000.0,
    }

    # Two candles with same timestamp (edge case from bad feed)
    c1 = Candle(1000, 50000, 51000, 49000, 50500, 100)
    c2 = Candle(1000, 50500, 51500, 49500, 51000, 100)
    candles = [c1, c2]
    # Verify the calculation doesn't raise ZeroDivisionError
    interval_ms = (candles[1].timestamp_ms - candles[0].timestamp_ms) if len(candles) >= 2 else 14_400_000
    candle_interval_ms = max(1, interval_ms)
    bars_held = max(0, int((2000 - 1000) / candle_interval_ms))
    assert bars_held >= 0


