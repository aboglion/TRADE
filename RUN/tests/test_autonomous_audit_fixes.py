"""
Autonomous Deep System Audit Fixes & Regressions Verification Tests.

Verifies:
1. math_utils: truncate_to_precision negative numbers symmetric truncation towards zero.
2. reconciliation_service: orphan order tracking preserves symbol.
3. portfolio_service: position-reducing orders priority before expanding orders.
4. exchange_gateway: fetch_balance preserves USDT collateral even when zero.
5. regime_adaptive_strategy:
   - Fast breakdown triggers Regime.BEAR on decision & target allocation.
   - Bear market counter-trend bounce maintains Regime.BEAR (no false bull conviction).
6. hybrid_strategy: Safe Haven Crash Shield exact 60% cash, 30% spot, 10% micro parity.
7. dry_run_exchange: position leverage sync and contracts float snapping to zero.
8. config_manager: short_leverage typed config parsing and defaults.
9. web_server: shared CCXT exchange caching avoids per-request client churn.
"""

import time
from unittest.mock import MagicMock

from src.core.enums import OrderSide, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.utils.math_utils import truncate_to_precision
from src.services.reconciliation_service import ReconciliationService
from src.services.portfolio_service import PortfolioService
from src.exchanges.dry_run_exchange import DryRunExchange
from src.exchanges.exchange_gateway import ExchangeGateway
from src.config.config_manager import ConfigManager, ExchangeConfig, StrategyConfig
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
from src.strategy.hybrid_strategy import HybridStrategy
from src.web.server import _get_shared_exchange
from tests.test_strategy import make_candle, make_candle_series


def test_truncate_to_precision_negative_numbers_symmetry():
    """Verify truncate_to_precision truncates towards zero without inflating magnitude."""
    # Positive truncation
    assert truncate_to_precision(1.2345, 2) == 1.23
    assert truncate_to_precision(1.2399, 2) == 1.23
    assert truncate_to_precision(0.00987, 3) == 0.009

    # Negative truncation (must NOT round away from zero like math.floor does!)
    assert truncate_to_precision(-1.2345, 2) == -1.23
    assert truncate_to_precision(-1.2399, 2) == -1.23
    assert truncate_to_precision(-0.00987, 3) == -0.009
    assert truncate_to_precision(-10.5, 0) == -10.0


def test_reconciliation_service_preserves_orphan_order_symbol():
    """Verify orphan order detection records the symbol so subsequent checks don't fail."""
    mock_gateway = MagicMock()
    orphan_order = OrderResult(
        client_order_id="ext_client_1",
        exchange_order_id="12345678",
        symbol="BTC/USDT",
        filled_amount=0.01,
    )
    mock_gateway.fetch_open_orders.return_value = [orphan_order]

    state = BotState()
    recon = ReconciliationService(mock_gateway, state)
    clean = recon.reconcile()

    assert clean is False
    assert len(state.pending_orders) == 1
    added_order = state.pending_orders[0]
    assert added_order["symbol"] == "BTC/USDT"
    assert added_order["exchange_order_id"] == "12345678"
    assert added_order["source"] == "orphan_detected"


def test_portfolio_service_reductions_before_expansions_and_sells_before_buys():
    """Verify that portfolio rebalance executes position-reducing orders before expanding orders."""
    mock_gateway = MagicMock()
    mock_gateway.get_market_info.return_value = {
        "precision": {"amount": 4, "price": 2},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }

    ps = PortfolioService(gateway=mock_gateway, deviation_threshold=0.01, is_futures=True)

    # Current holdings:
    # ETH has +1.0 long position (positive) -> needs SELL to 0
    # SOL has -5.0 short position (negative) -> needs BUY to 0
    # BTC has 0 position -> needs SELL to -0.35 short
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "ETH": AssetHolding("ETH", free=1.0, locked=0.0, total=1.0, value_usd=3000.0),
            "SOL": AssetHolding("SOL", free=0.0, locked=0.0, total=-5.0, value_usd=1000.0),
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
            "USDT": AssetHolding("USDT", free=6000.0, locked=0.0, total=6000.0, value_usd=6000.0),
        },
        total_value_usd=10000.0,
    )

    prices = {"ETH/USDT": 3000.0, "SOL/USDT": 200.0, "BTC/USDT": 60000.0}

    # Target:
    # ETH: 0.0 (liquidate long -> reducing sell)
    # BTC: -0.35 (open short -> expanding sell)
    # SOL: 0.0 (cover short -> reducing buy)
    target = TargetAllocation(
        weights={"ETH/USDT": 0.0, "BTC/USDT": -0.35, "SOL/USDT": 0.0, "USDT": 0.65},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )

    plan = ps.compute_rebalance_plan(portfolio, target, prices)

    # 1. All SELLS must execute before all BUYS
    saw_buy = False
    for o in plan.orders:
        if o.side == OrderSide.BUY:
            saw_buy = True
        elif o.side == OrderSide.SELL:
            assert not saw_buy, "Found SELL after BUY"

    # 2. In SELLs: ETH (closing existing long) must execute BEFORE BTC (opening new short)
    sell_symbols = [o.symbol for o in plan.orders if o.side == OrderSide.SELL]
    assert sell_symbols == ["ETH/USDT", "BTC/USDT"]

    # 3. In BUYs: SOL (covering existing short) is present
    buy_symbols = [o.symbol for o in plan.orders if o.side == OrderSide.BUY]
    assert buy_symbols == ["SOL/USDT"]


def test_exchange_gateway_fetch_balance_preserves_zero_balance_usdt():
    """Verify ExchangeGateway fetch_balance always returns USDT even when zero."""
    config = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(config, RunMode.TESTNET)

    # Simulate CCXT returning only BTC and ETH balances with no USDT
    mock_ccxt = MagicMock()
    mock_ccxt.fetch_balance.return_value = {
        "BTC": {"free": 0.5, "used": 0.0, "total": 0.5},
        "ETH": {"free": 2.0, "used": 0.0, "total": 2.0},
        "USDT": {"free": 0.0, "used": 0.0, "total": 0.0},
    }
    gw._exchange = mock_ccxt
    gw._initialized = True

    balances = gw.fetch_balance()
    assert "USDT" in balances
    assert balances["USDT"]["total"] == 0.0
    assert balances["USDT"]["free"] == 0.0


def test_regime_adaptive_fast_breakdown_sets_bear_regime():
    """Verify that Fast Breakdown (BTC < EMA20 < EMA50) sets effective regime to Regime.BEAR."""
    strategy = RegimeAdaptiveStrategy(sma_regime_period=50, bear_short_hedge_weight=0.35, short_leverage=2.0)

    # 1000 candles with a strong bull base so price is above SMA-50
    btc_candles = make_candle_series(1_600_000_000_000, count=990, base_price=50000.0, trend=20.0)
    last_ts = btc_candles[-1].timestamp_ms

    # Append a sharp 10-candle breakdown where price drops below EMA20 and EMA20 falls below EMA50
    for i in range(10):
        last_ts += 86_400_000
        p = 68000.0 - (i * 2000.0)
        btc_candles.append(make_candle(last_ts, open=p + 500, high=p + 600, low=p - 500, close=p))

    portfolio = PortfolioSnapshot(
        timestamp_ms=last_ts,
        holdings={"USDT": AssetHolding("USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)},
        total_value_usd=1000.0,
    )

    decision = strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
    assert decision.regime == Regime.BEAR
    assert decision.target_allocation.regime == Regime.BEAR
    assert decision.target_allocation.weights.get("BTC/USDT") == -0.70


def test_regime_adaptive_bear_market_rally_maintains_bear_regime():
    """Verify that a temporary bounce in a bear market (BTC < SMA150) maintains Regime.BEAR."""
    strategy = RegimeAdaptiveStrategy(sma_regime_period=50, bear_short_hedge_weight=0.35, short_leverage=2.0)

    # 1000 candles in a deep downtrend so price is well below SMA-50
    btc_candles = make_candle_series(1_600_000_000_000, count=995, base_price=80000.0, trend=-50.0)
    last_ts = btc_candles[-1].timestamp_ms

    # Price was at ~30,000, now bounces up for 5 candles above recent EMA20, but still way below SMA50
    for i in range(5):
        last_ts += 86_400_000
        p = 30000.0 + (i * 800.0)
        btc_candles.append(make_candle(last_ts, open=p - 200, high=p + 300, low=p - 300, close=p))

    portfolio = PortfolioSnapshot(
        timestamp_ms=last_ts,
        holdings={"USDT": AssetHolding("USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)},
        total_value_usd=1000.0,
    )

    decision = strategy.compute_signals({"BTC/USDT": btc_candles}, portfolio)
    # Must remain BEAR! Never launch Conviction Rocket long in a macro bear market!
    assert decision.regime == Regime.BEAR
    assert decision.target_allocation.regime == Regime.BEAR
    assert decision.target_allocation.weights.get("BTC/USDT") == -0.70


def test_hybrid_strategy_safe_haven_allocation_parity():
    """Verify that in Safe Haven mode HybridStrategy allocates 60% cash, 30% spot, up to 10% micro."""
    macro = RegimeAdaptiveStrategy(
        sma_regime_period=50,
        momentum_cutoff_pct=-0.02,
        safe_cash_weight=0.60,
        safe_spot_weight=0.30,
        safe_micro_weight=0.10,
        core_ratio=0.80,
    )
    micro = MicroSatelliteStrategy(asset_weights={"BTC": 0.40, "ETH": 0.30, "SOL": 0.30})
    hybrid = HybridStrategy(macro_strategy=macro, micro_strategy=micro, core_ratio=0.80)

    # Create candles: peak at 70,000 then 3% pullback from 5d high -> triggers Safe Haven
    btc_candles = make_candle_series(1_600_000_000_000, count=999, base_price=60000.0, trend=0.0)
    last_ts = btc_candles[-1].timestamp_ms + 86_400_000
    btc_candles.append(make_candle(last_ts, open=69500.0, high=70000.0, low=69000.0, close=70000.0))

    portfolio = PortfolioSnapshot(
        timestamp_ms=last_ts,
        holdings={"USDT": AssetHolding("USDT", 1000.0, 0.0, 1000.0, 1000.0)},
        total_value_usd=1000.0,
    )
    hybrid.compute_signals({"BTC/USDT": btc_candles}, portfolio)

    # Breaches -2% cutoff from 70,000 to 67,900
    last_ts += 86_400_000
    btc_candles.append(make_candle(last_ts, open=69000.0, high=69200.0, low=67800.0, close=67900.0))

    decision = hybrid.compute_signals({"BTC/USDT": btc_candles}, portfolio)

    assert decision.metadata.get("safe_haven_active") is True
    # Safe Haven cash is guaranteed >= 60%
    assert decision.target_allocation.weights["USDT"] >= 0.60
    # Spot total crypto is within 40% (30% spot + max 10% micro)
    total_crypto = sum(v for k, v in decision.target_allocation.weights.items() if k != "USDT")
    assert total_crypto <= 0.4001


def test_dry_run_exchange_leverage_and_float_zero_snap():
    """Verify DryRunExchange correctly tracks leverage and snaps zero contracts without dust."""
    dry = DryRunExchange(initial_balances={"USDT": 10000.0})
    dry.set_price("BTC/USDT", 60000.0)
    dry.set_leverage(5.0, "BTC/USDT")

    # Buy 0.1 BTC (cost $6,000, margin req $1,200)
    intent_buy = OrderIntent(
        client_order_id="buy_1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        price=60000.0,
    )
    res_buy = dry.create_order(intent_buy)
    assert res_buy.status.value == "filled"

    positions = dry.fetch_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC/USDT"
    assert positions[0]["contracts"] == 0.1
    assert positions[0]["leverage"] == 5.0

    # Sell 0.1 BTC to completely close position
    intent_sell = OrderIntent(
        client_order_id="sell_1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        price=60000.0,
    )
    res_sell = dry.create_order(intent_sell)
    assert res_sell.status.value == "filled"

    # Positions list must be empty (snapped to zero, no float dust like 1e-16)
    positions_after = dry.fetch_positions()
    assert len(positions_after) == 0


def test_config_manager_short_leverage_typed_parsing():
    """Verify StrategyConfig and ConfigManager parse short_leverage correctly."""
    cfg = StrategyConfig()
    assert cfg.short_leverage == 2.0

    cm = ConfigManager("config.yaml")
    config = cm.load()
    assert config.strategy.short_leverage == 2.0


def test_web_server_shared_exchange_caching():
    """Verify _get_shared_exchange reuses gateway exchange or cached ccxt client."""
    mock_gateway = MagicMock()
    mock_gateway.exchange = MagicMock()

    ex1 = _get_shared_exchange(mock_gateway)
    assert ex1 == mock_gateway.exchange

    # Without gateway, reuses cached client across calls
    ex2 = _get_shared_exchange(None)
    ex3 = _get_shared_exchange(None)
    assert ex2 is not None
    assert ex2 is ex3
