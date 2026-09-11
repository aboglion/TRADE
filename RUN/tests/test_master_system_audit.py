"""
Master System Audit Verification Tests.
Verifies all bug fixes and architectural reliability improvements across:
- Web API & Strategy Conditions
- Kill Switch Persistence
- Exchange Fill Price Resolution & Symbol Aliases
- Portfolio Price Resolution & Symbol Normalization
- Orchestrator Data Gap & Insufficient Data Safety Gates
- Micro Satellite Strategy Signal Completeness
"""

import pytest
from unittest.mock import MagicMock, patch

from src.core.enums import OrderSide, OrderType, PositionAction, Regime, RunMode
from src.core.models import (
    BotState,
    Candle,
    OrderIntent,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.core.exceptions import InsufficientDataError
from src.config.config_manager import ConfigManager, ExchangeConfig
from src.exchanges.exchange_gateway import ExchangeGateway
from src.exchanges.dry_run_exchange import DryRunExchange
from src.services.portfolio_service import PortfolioService
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy


def test_strategy_conditions_last_regime_fallback_when_btc_unavailable(tmp_path):
    """Verify that _handle_strategy_conditions handles missing BTC without UnboundLocalError."""
    from src.web.server import DashboardRequestHandler

    mock_state_store = MagicMock()
    mock_state = BotState()
    mock_state.last_regime = "bull"
    mock_state_store.load_state.return_value = mock_state

    # Instantiate handler with mocks
    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.state_store = mock_state_store
    handler.gateway = MagicMock()
    handler.gateway.fetch_ticker_price.return_value = 0.0
    handler.config = MagicMock()
    sent_data = {}

    def fake_send_json(data, status=200):
        nonlocal sent_data
        sent_data = data

    handler._send_json = fake_send_json

    # Mock _fetch_ohlcv_safe to return empty (simulating network failure or cold start)
    with patch.object(DashboardRequestHandler, "_fetch_ohlcv_safe", return_value=[]):
        handler._handle_strategy_conditions()

    assert "macro_regime" in sent_data
    # Should cleanly fallback to state's last_regime without UnboundLocalError
    assert sent_data["macro_regime"]["regime"] == "BULL"


def test_kill_switch_persistence_to_config_yaml(tmp_path):
    """Verify that toggling kill switch persists to config.yaml on disk."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("risk:\n  kill_switch: false\nrun_mode: DRY_RUN\n")

    cm = ConfigManager(str(cfg_file))
    config = cm.load()
    assert config.risk.kill_switch is False

    cm.save_kill_switch(True)
    assert config.risk.kill_switch is True

    # Reload from disk to verify persistence
    cm2 = ConfigManager(str(cfg_file))
    config2 = cm2.load()
    assert config2.risk.kill_switch is True


def test_exchange_gateway_fill_price_resolution_from_info():
    """Verify ExchangeGateway extracts exact fill price from Binance info or falls back to estimated price."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.DRY_RUN)
    gw._initialized = True
    gw._exchange = MagicMock()

    intent = OrderIntent(
        client_order_id="test_order_123",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.01,
        estimated_price=64200.0,
    )

    # 1. Binance response with avgPrice="0.00000" but cumQuote and cumQty present in info
    gw.exchange.create_order.return_value = {
        "id": "123456",
        "clientOrderId": "test_order_123",
        "status": "closed",
        "filled": 0.01,
        "average": 0.0,
        "price": 0.0,
        "trades": [],
        "info": {
            "cumQuote": "645.00",
            "cumQty": "0.01",
            "avgPrice": "0.00000",
        },
    }

    res = gw.create_order(intent)
    assert res.average_price == pytest.approx(64500.0)

    # 2. Binance response with avgPrice directly in info
    gw.exchange.create_order.return_value = {
        "id": "123457",
        "clientOrderId": "test_order_123",
        "status": "closed",
        "filled": 0.01,
        "average": 0.0,
        "price": 0.0,
        "trades": [],
        "info": {
            "avgPrice": "64350.50",
        },
    }
    res2 = gw.create_order(intent)
    assert res2.average_price == pytest.approx(64350.50)

    # 3. Completely empty price fields -> falls back to intent.estimated_price
    gw.exchange.create_order.return_value = {
        "id": "123458",
        "clientOrderId": "test_order_123",
        "status": "closed",
        "filled": 0.01,
        "average": 0.0,
        "price": 0.0,
        "trades": [],
        "info": {},
    }
    res3 = gw.create_order(intent)
    assert res3.average_price == pytest.approx(64200.0)


def test_exchange_gateway_market_info_symbol_aliases():
    """Verify get_market_info resolves symbols whether queried with or without :USDT suffix."""
    cfg = ExchangeConfig(name="binance", market_type="future")
    gw = ExchangeGateway(cfg, RunMode.DRY_RUN)
    gw._markets = {
        "BTC/USDT:USDT": {"limits": {"amount": {"min": 0.001}}, "precision": {"amount": 3}},
        "ETH/USDT": {"limits": {"amount": {"min": 0.01}}, "precision": {"amount": 2}},
    }

    # Query without suffix when market has suffix
    info_btc = gw.get_market_info("BTC/USDT")
    assert info_btc["limits"]["amount"]["min"] == 0.001

    # Query with suffix when market has no suffix
    info_eth = gw.get_market_info("ETH/USDT:USDT")
    assert info_eth["limits"]["amount"]["min"] == 0.01


def test_portfolio_service_price_resolution_and_clean_symbol():
    """Verify PortfolioService resolves prices correctly with symbol aliases."""
    dry_ex = DryRunExchange(initial_balances={"USDT": 1000.0})
    dry_ex.set_price("BTC/USDT", 50000.0)

    ps = PortfolioService(dry_ex)

    # In compute_rebalance_plan, pass target with clean symbol "BTC" or "BTC/USDT"
    target = TargetAllocation(
        weights={"BTC": 0.50, "USDT": 0.50},
        regime=Regime.BULL,
        timestamp_ms=1000000,
    )
    snap = ps.get_portfolio(prices={"BTC/USDT": 50000.0})
    plan = ps.compute_rebalance_plan(snap, target, prices={"BTC/USDT": 50000.0})

    assert len(plan.orders) == 1
    assert plan.orders[0].symbol == "BTC" or plan.orders[0].symbol == "BTC/USDT"
    assert plan.orders[0].estimated_price == 50000.0


def test_orchestrator_insufficient_data_halts_cycle_safely():
    """Verify that InsufficientDataError on any pair halts the orchestrator cycle safely."""
    from src.orchestrator import BotOrchestrator
    from src.core.models import Candle

    mock_config = MagicMock()
    mock_config.strategy.assets = {
        "BTC": MagicMock(pair="BTC/USDT"),
        "ETH": MagicMock(pair="ETH/USDT"),
    }
    mock_config.strategy.warmup_candles = 200
    mock_config.run_mode = RunMode.DRY_RUN
    mock_config.risk.kill_switch = False
    mock_config.scheduler.poll_interval_seconds = 300

    mock_gateway = MagicMock()
    mock_candle_service = MagicMock()
    # Return 1 new candle so it starts cycle
    dummy_candle = Candle(timestamp_ms=1000, open=1, high=1, low=1, close=1, volume=1, is_closed=True)
    mock_candle_service.get_new_closed_candles.return_value = [dummy_candle]

    # Raise InsufficientDataError on ETH
    def fake_get_full_history(symbol, up_to_ts, min_candles):
        if "ETH" in symbol:
            raise InsufficientDataError("Not enough ETH candles")
        return [dummy_candle] * 200

    mock_candle_service.get_full_history.side_effect = fake_get_full_history

    mock_portfolio = MagicMock()
    mock_strategy = MagicMock()
    mock_risk = MagicMock()
    mock_risk.is_kill_switch_active.return_value = False
    mock_state_store = MagicMock()
    mock_state = BotState()

    orch = BotOrchestrator(
        config=mock_config,
        gateway=mock_gateway,
        candle_service=mock_candle_service,
        portfolio_service=mock_portfolio,
        strategy=mock_strategy,
        risk_manager=mock_risk,
        state_store=mock_state_store,
        state=mock_state,
    )

    # Must return False and record critical error, not proceed to trade
    result = orch.run_once(force=True)
    assert result is False
    assert any("Insufficient data" in err for err in mock_state.critical_errors)
    assert mock_strategy.compute_signals.called is False


def test_micro_satellite_emits_hold_signals_and_bracket_safety():
    """Verify MicroSatelliteStrategy emits explicit HOLD signals when neutral."""
    micro = MicroSatelliteStrategy(asset_weights={"BTC": 0.40})
    now_ms = 1_700_000_000_000
    tf_ms = 14_400_000
    candles = [
        Candle(
            timestamp_ms=now_ms + i * tf_ms,
            open=50000.0,
            high=50100.0,
            low=49900.0,
            close=50000.0,
            volume=100.0,
            is_closed=True,
        )
        for i in range(250)
    ]
    portfolio = PortfolioSnapshot(timestamp_ms=now_ms + 250 * tf_ms, holdings={}, total_value_usd=1000.0)

    decision = micro.compute_signals({"BTC/USDT": candles}, portfolio)
    assert decision is not None
    assert len(decision.signals) == 1
    assert decision.signals[0].action == PositionAction.HOLD
    assert "No Entry" in decision.signals[0].reason
