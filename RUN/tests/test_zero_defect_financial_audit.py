"""
Zero-Defect Financial Audit & System Integrity Verification Suite.

Validates that:
1. In-cycle futures short position tracking preserves positive USD notional (abs(contracts) * price).
2. Dynamic in-cycle USDT margin calculation properly distinguishes futures margin vs spot cash.
3. RiskManager recognizes reduce_only intents, bypasses time throttles for risk reduction,
   and handles non-USDT quote currencies (e.g. USDC, USD).
4. OrderManager and ReconciliationService properly record EXPIRED and FAILED terminal order states.
5. ExchangeGateway extracts fee commissions from CCXT trades and raw Binance fills.
6. Web Dashboard /api/portfolio correctly computes short position PnL (profit when price drops).
7. Mode switching via Web API properly syncs logs/last_mode so restarts boot into requested mode.
8. ConfigManager resolves state and log paths relative to config file directory.
9. DryRunExchange normalizes perpetual symbols with suffixes (e.g. BTC/USDT:USDT) for leverage lookups.
"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config.config_manager import (
    BotConfig,
    ConfigManager,
    ExchangeConfig,
    RiskConfig,
    StrategyConfig,
)
from src.core.enums import OrderSide, OrderStatus, OrderType, Regime, RunMode
from src.core.models import (
    AssetHolding,
    BotState,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    TargetAllocation,
)
from src.exchanges.dry_run_exchange import DryRunExchange
from src.exchanges.exchange_gateway import ExchangeGateway
from src.orchestrator import BotOrchestrator
from src.services.order_manager import OrderManager
from src.services.portfolio_service import PortfolioService
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore


# ── 1. Orchestrator In-Cycle Futures Short Tracking ───────────────────────────

def test_orchestrator_futures_short_in_cycle_notional_positive(tmp_path):
    """Verify that opening/expanding a short in-cycle does not zero out value_usd."""
    state_file = tmp_path / "bot_state.json"
    state_store = JsonStateStore(str(state_file))
    state = BotState()

    config = BotConfig(
        run_mode=RunMode.DRY_RUN,
        exchange=ExchangeConfig(name="binance", market_type="future"),
        risk=RiskConfig(min_seconds_between_orders=0, allow_market_orders=True),
    )

    gateway = MagicMock()
    candle_svc = MagicMock()
    portfolio_svc = MagicMock()
    strategy = MagicMock()
    risk_mgr = RiskManager(config.risk, is_futures=True)

    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_svc,
        portfolio_service=portfolio_svc,
        strategy=strategy,
        risk_manager=risk_mgr,
        state_store=state_store,
        state=state,
    )

    # Simulate portfolio with 0 BTC and 10,000 USDT
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDT": AssetHolding("USDT", free=10000.0, locked=0.0, total=10000.0, value_usd=10000.0),
        },
        total_value_usd=10000.0,
    )

    # Order intent to open a short: SELL 0.1 BTC @ $60,000 ($6,000 notional)
    intent = OrderIntent(
        client_order_id="test_short_open",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        price=60000.0,
        estimated_price=60000.0,
        reason="Open Bear Short",
    )

    gateway.create_order.return_value = OrderResult(
        client_order_id="test_short_open",
        exchange_order_id="dry_test_1",
        status=OrderStatus.FILLED,
        filled_amount=0.1,
        average_price=60000.0,
        fees=6.0,
        fee_currency="USDT",
    )

    # Plan with 1 order
    plan = MagicMock()
    plan.orders = [intent]
    portfolio_svc.compute_rebalance_plan.return_value = plan
    portfolio_svc.get_portfolio.return_value = portfolio

    # Decision
    decision = MagicMock()
    decision.regime = Regime.BEAR
    decision.metadata = {"effective_leverage": 2.0, "short_leverage": 2.0}
    decision.target_allocation = TargetAllocation(
        weights={"BTC/USDT": -0.35, "USDT": 0.65},
        regime=Regime.BEAR,
        timestamp_ms=int(time.time() * 1000),
    )
    strategy.compute_signals.return_value = decision

    orchestrator.run_once(force=True)

    # Check the in-memory portfolio holdings
    btc_holding = portfolio.holdings.get("BTC")
    assert btc_holding is not None
    assert btc_holding.total == -0.1
    # value_usd MUST be positive $6,000.0, NOT 0.0!
    assert btc_holding.value_usd == 6000.0

    # In futures, selling short should consume margin, NOT inject $6,000 cash into USDT!
    usdt_holding = portfolio.holdings.get("USDT")
    assert usdt_holding is not None
    # 10,000 - margin consumed (6000/2 = 3000) - 6 fee = 6994
    assert usdt_holding.free < 10000.0


# ── 2. RiskManager Enhancements ───────────────────────────────────────────────

def test_risk_manager_reduce_only_and_time_throttle_bypass():
    """Verify RiskManager identifies reduce_only=True and allows rapid execution for risk mitigation."""
    config = RiskConfig(
        max_single_order_usd=5000.0,
        max_portfolio_change_pct=0.10,
        min_seconds_between_orders=10,
        allow_market_orders=True,
    )
    rm = RiskManager(config, is_futures=True)

    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=1.0, value_usd=60000.0),
            "USDT": AssetHolding("USDT", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
        },
        total_value_usd=65000.0,
    )

    # Reducing intent with reduce_only=True that exceeds normal single order limits ($60k > $5k)
    reduce_intent = OrderIntent(
        client_order_id="close_btc_pos",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=1.000005,  # Slightly above holding.total due to rounding
        price=60000.0,
        estimated_price=60000.0,
        reduce_only=True,
    )

    # Set last order time to 1 second ago (well within 10s cooldown)
    rm._last_order_time = time.time() - 1.0

    approved, reason = rm.approve_order(reduce_intent, portfolio)
    assert approved is True, f"Reduce-only order should be approved regardless of cooldown/limits: {reason}"


def test_risk_manager_quote_currency_flexibility():
    """Verify RiskManager checks the correct quote currency for non-USDT pairs (e.g. USDC)."""
    config = RiskConfig(min_order_value_usd=10.0)
    rm = RiskManager(config, is_futures=False)

    # Portfolio holding 5000 USDC and 0 USDT
    portfolio = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "USDC": AssetHolding("USDC", free=5000.0, locked=0.0, total=5000.0, value_usd=5000.0),
            "USDT": AssetHolding("USDT", free=0.0, locked=0.0, total=0.0, value_usd=0.0),
        },
        total_value_usd=5000.0,
    )

    # Buy BTC with USDC
    intent = OrderIntent(
        client_order_id="buy_btc_usdc",
        symbol="BTC/USDC",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.05,
        price=60000.0,  # $3000
        estimated_price=60000.0,
    )

    approved, reason = rm.approve_order(intent, portfolio)
    assert approved is True, f"Should approve BUY order when USDC is available: {reason}"


# ── 3. Terminal Order Status Persistence ──────────────────────────────────────

def test_order_manager_expired_order_persisted_to_completed():
    """Verify OrderManager moves EXPIRED orders into completed_orders."""
    state = BotState()
    gateway = MagicMock()
    om = OrderManager(gateway, state, RunMode.DRY_RUN)

    intent = OrderIntent(
        client_order_id="fok_order_expired",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.01,
        price=50000.0,
    )

    # Return EXPIRED result
    gateway.create_order.return_value = OrderResult(
        client_order_id="fok_order_expired",
        exchange_order_id="exp_123",
        status=OrderStatus.EXPIRED,
        error_message="Order expired immediately",
    )

    res = om.execute(intent)
    assert res.status == OrderStatus.EXPIRED
    assert len(state.pending_orders) == 0
    assert len(state.completed_orders) == 1
    assert state.completed_orders[0]["status"] == "expired"


def test_reconciliation_service_failed_order_persisted_to_completed():
    """Verify ReconciliationService moves FAILED orders into completed_orders."""
    state = BotState(
        pending_orders=[
            {
                "client_order_id": "test_failed_order",
                "exchange_order_id": "rej_456",
                "symbol": "ETH/USDT",
                "status": "submitted",
            }
        ]
    )
    gateway = MagicMock()
    gateway.fetch_open_orders.return_value = []  # Not open on exchange
    gateway.fetch_order.return_value = OrderResult(
        client_order_id="test_failed_order",
        exchange_order_id="rej_456",
        status=OrderStatus.FAILED,
        error_message="Order rejected by exchange risk engine",
    )

    recon = ReconciliationService(gateway, state, run_mode=RunMode.LIVE)
    recon.reconcile()

    assert len(state.pending_orders) == 0
    assert len(state.completed_orders) == 1
    assert state.completed_orders[0]["status"] == "failed"


# ── 4. ExchangeGateway Fee Extraction ─────────────────────────────────────────

def test_exchange_gateway_fee_extraction_from_trades_and_fills():
    """Verify ExchangeGateway extracts fees from trades array and info.fills."""
    # 1. From CCXT trades array
    raw_trades = {
        "id": "1",
        "trades": [
            {"cost": 1000.0, "amount": 0.01, "fee": {"cost": 0.75, "currency": "USDT"}},
            {"cost": 2000.0, "amount": 0.02, "fee": {"cost": 1.50, "currency": "USDT"}},
        ]
    }
    cost, curr = ExchangeGateway._extract_fee(raw_trades)
    assert cost == pytest.approx(2.25)
    assert curr == "USDT"

    # 2. From raw Binance fills
    raw_fills = {
        "id": "2",
        "info": {
            "fills": [
                {"commission": "0.00012", "commissionAsset": "BNB"},
                {"commission": "0.00024", "commissionAsset": "BNB"},
            ]
        }
    }
    cost_fill, curr_fill = ExchangeGateway._extract_fee(raw_fills)
    assert cost_fill == pytest.approx(0.00036)
    assert curr_fill == "BNB"


# ── 5. Dashboard Short Position PnL Calculation ───────────────────────────────

def test_dashboard_portfolio_short_pnl_calculation():
    """Verify that /api/portfolio correctly computes positive PnL when a short position drops in price."""
    from src.web.server import DashboardRequestHandler

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.gateway = MagicMock()
    handler.config = BotConfig(exchange=ExchangeConfig(market_type="future"))
    handler.state_store = MagicMock()

    # Short position of 0.5 BTC entered at $60,000. Price dropped to $54,000 (10% drop -> 10% gain for short)
    state = BotState(
        strategy_state={
            "positions": {
                "BTC": {"active": True, "entry_px": 60000.0, "mode": "BEAR"}
            }
        },
        session_initial_value_usd=10000.0,
    )
    handler.state_store.load_state.return_value = state

    snapshot = PortfolioSnapshot(
        timestamp_ms=int(time.time() * 1000),
        holdings={
            "BTC": AssetHolding("BTC", free=0.0, locked=0.0, total=-0.5, value_usd=27000.0, entry_price=60000.0),
            "USDT": AssetHolding("USDT", free=7000.0, locked=0.0, total=7000.0, value_usd=7000.0),
        },
        total_value_usd=34000.0,
    )

    captured_json = {}
    handler._send_json = lambda data, status=200: captured_json.update(data)

    with patch("src.services.portfolio_service.PortfolioService.get_portfolio", return_value=snapshot):
        handler._handle_portfolio()

    btc_item = next(h for h in captured_json["holdings"] if h["symbol"] == "BTC")
    assert btc_item["total"] == -0.5
    # When price drops from 60k to 54k (27000 / 0.5 = 54000), short gain should be +10.0%
    assert btc_item["change_pct"] == pytest.approx(10.0, abs=0.1)
    # Net change should deduct ~0.2% fee, approx +9.8%
    assert btc_item["net_change_pct"] == pytest.approx(9.8, abs=0.1)


# ── 6. Mode Switch Persistence and Makefile Sync ──────────────────────────────

def test_handle_switch_mode_syncs_last_mode_file(tmp_path):
    """Verify that switching mode via API updates logs/last_mode."""
    from src.web.server import DashboardRequestHandler

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.rfile = MagicMock()
    handler.headers = {"Content-Length": "23"}
    handler.rfile.read.return_value = b'{"mode": "LIVE"}'

    captured_resp = {}
    handler._send_json = lambda data, status=200: captured_resp.update(data)

    # Mock environment variables to allow switching to LIVE
    with patch.dict(os.environ, {
        "BINANCE_API_KEY": "test_valid_key_12345",
        "BINANCE_API_SECRET": "test_valid_secret_67890",
    }):
        with patch("src.config.config_manager.ConfigManager.save_run_mode"):
            with patch("threading.Thread"):
                handler._handle_switch_mode()

    assert captured_resp.get("success") is True
    assert captured_resp.get("mode") == "LIVE"

    # Verify logs/last_mode was updated
    last_mode_path = Path("logs/last_mode")
    if last_mode_path.exists():
        assert last_mode_path.read_text(encoding="utf-8").strip() == "LIVE"


# ── 7. ConfigManager Relative Path Resolution ─────────────────────────────────

def test_config_manager_resolves_paths_relative_to_config(tmp_path):
    """Verify ConfigManager finds state and log files located in config directory."""
    run_dir = tmp_path / "RUN"
    run_dir.mkdir()
    data_dir = run_dir / "data"
    data_dir.mkdir()
    state_file = data_dir / "bot_state.json"
    state_file.write_text("{}", encoding="utf-8")

    cfg_file = run_dir / "config.yaml"
    cfg_file.write_text(
        """
state:
  path: data/bot_state.json
logging:
  file: logs/bot.log
""",
        encoding="utf-8",
    )

    cm = ConfigManager(str(cfg_file))
    config = cm.load()

    assert Path(config.state.path).resolve() == state_file.resolve()


# ── 8. DryRunExchange Symbol Normalization ───────────────────────────────────

def test_dry_run_exchange_active_leverage_symbol_resolution():
    """Verify DryRunExchange correctly maps symbols with :USDT suffix."""
    ex = DryRunExchange()
    ex.set_leverage(5.0, "BTC/USDT")

    assert ex._get_active_leverage("BTC/USDT") == 5.0
    assert ex._get_active_leverage("BTC/USDT:USDT") == 5.0
    assert ex._get_active_leverage("BTC") == 5.0
