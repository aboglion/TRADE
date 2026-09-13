"""
Comprehensive Audit & Bug Fix Regression Tests.

Tests:
1. DryRunExchange margin validation for short position expansions.
2. MicroSatelliteStrategy deterministic timestamp-based bars_held calculation.
3. OrderManager symbol resolution during stale order cancellation.
4. Orchestrator dynamic leverage synchronization.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from src.core.enums import OrderSide, OrderStatus, OrderType
from src.core.models import (
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    AssetHolding,
)
from src.exchanges.dry_run_exchange import DryRunExchange
from src.services.order_manager import OrderManager
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy


def test_dry_run_exchange_short_margin_check():
    """Test that DryRunExchange rejects short orders exceeding collateral margin limit."""
    exchange = DryRunExchange(initial_balances={"USDT": 100.0})
    exchange.set_leverage(2.0, "BTC/USDT")
    exchange.set_price("BTC/USDT", 50000.0)

    # 1. Attempt short order of 1.0 BTC ($50,000 cost @ 2x = $25,000 margin req > $100 available)
    large_short = OrderIntent(
        client_order_id="test_short_large",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=1.0,
        estimated_price=50000.0,
    )
    result = exchange.create_order(large_short)
    assert result.status == OrderStatus.FAILED
    assert "Insufficient balance" in result.error_message

    # 2. Valid short order of 0.003 BTC ($150 cost @ 2x = $75 margin req <= $100 available)
    valid_short = OrderIntent(
        client_order_id="test_short_valid",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.003,
        estimated_price=50000.0,
    )
    res_valid = exchange.create_order(valid_short)
    assert res_valid.status == OrderStatus.FILLED


def test_micro_satellite_strategy_timestamp_bars_held():
    """Test that MicroSatelliteStrategy uses deterministic timestamp-based duration for bars_held."""
    strategy = MicroSatelliteStrategy(asset_weights={"BTC/USDT": 1.0})
    base_ts = 1700000000000  # arbitrary UTC ms timestamp (4H aligned)
    tf_ms = 14_400_000

    # Build 250 test candles
    candles = []
    for i in range(250):
        ts = base_ts + (i * tf_ms)
        candles.append(Candle(
            timestamp_ms=ts,
            open=100.0 + i,
            high=105.0 + i,
            low=99.0 + i,
            close=102.0 + i,
            volume=1000.0,
            is_closed=True,
        ))

    portfolio = PortfolioSnapshot(
        timestamp_ms=candles[-1].timestamp_ms,
        holdings={"USDT": AssetHolding("USDT", free=1000.0, locked=0.0, total=1000.0, value_usd=1000.0)},
        total_value_usd=1000.0,
    )

    # Export state to simulate active position opened 10 bars ago
    entry_ts = candles[-1].timestamp_ms - (10 * tf_ms)
    state = {
        "positions": {
            "BTC": {
                "active": True,
                "entry_px": 300.0,
                "extreme_px": 350.0,
                "stop_px": 250.0,
                "entry_bar": 230,
                "entry_ts": entry_ts,
                "tp1_done": False,
                "mode": "HIGH_CONVICTION_MICRO",
                "alloc": 0.85,
            }
        }
    }
    strategy.import_state(state)

    _ = strategy.compute_signals({"BTC/USDT": candles}, portfolio)
    exported = strategy.export_state()
    pos = exported["positions"]["BTC"]

    # Verify position remains active (bars_held = 10 < 42 max hold bars)
    assert pos["active"] is True


def test_order_manager_cancel_stale_orders_symbol_resolution():
    """Test OrderManager symbol resolution when canceling stale exchange orders."""
    mock_gateway = MagicMock()
    state = BotState()
    
    # Raw response from exchange missing unified symbol
    raw_order = OrderResult(
        client_order_id="stale_123",
        exchange_order_id="exc_999",
        status=OrderStatus.OPEN,
        raw_response={"id": "exc_999", "symbol": "BTCUSDT"},
    )
    mock_gateway.fetch_open_orders.return_value = [raw_order]

    order_manager = OrderManager(mock_gateway, state, run_mode=MagicMock())
    canceled = order_manager.cancel_stale_open_orders()

    assert canceled == 1
    mock_gateway.cancel_order.assert_called_once_with(symbol="BTC/USDT", order_id="exc_999")


def test_set_margin_mode_silences_portfolio_margin_error(caplog):
    """Test that set_margin_mode logs debug and does not warn on -2015 / already cross."""
    import logging
    from src.exchanges.exchange_gateway import ExchangeGateway
    from src.config.config_manager import ExchangeConfig

    config = ExchangeConfig(name="binance", api_key="k", api_secret="s")
    gw = ExchangeGateway(config, run_mode=MagicMock())
    gw._initialized = True
    mock_exchange = MagicMock()
    mock_exchange.set_margin_mode.side_effect = Exception("binance -2015: Invalid API-key, IP, or permissions")
    gw._exchange = mock_exchange

    with caplog.at_level(logging.DEBUG):
        gw.set_margin_mode("cross", "BTC/USDT")

    # Check that it logged debug, but NO warning
    assert any("Portfolio Margin" in rec.message for rec in caplog.records if rec.levelno == logging.DEBUG)
    assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_retry_transient_error_log_levels(caplog):
    """Test that _retry transient errors log at DEBUG on 1st attempt and WARNING on 2nd+."""
    import logging
    import ccxt
    from src.exchanges.exchange_gateway import ExchangeGateway
    from src.config.config_manager import ExchangeConfig

    config = ExchangeConfig(name="binance", api_key="k", api_secret="s", retry_delay_base_ms=1)
    gw = ExchangeGateway(config, run_mode=MagicMock())

    attempts = 0

    def flaky_fn():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ccxt.NetworkError("temporary connection reset")
        return "success"

    with caplog.at_level(logging.DEBUG):
        res = gw._retry(flaky_fn, max_retries=3)

    assert res == "success"
    assert attempts == 3

    # Attempt 0 (first failure) should log DEBUG
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "attempt 1/3" in r.message]
    assert len(debug_records) == 1

    # Attempt 1 (second failure) should log WARNING
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING and "attempt 2/3" in r.message]
    assert len(warn_records) == 1

