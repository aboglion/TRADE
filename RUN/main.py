"""
Dynamic Regime-Adaptive 2.0x — Live Trading Bot Entry Point.

Usage:
  python main.py                          # DRY_RUN mode (default)
  python main.py --mode DRY_RUN           # Explicit DRY_RUN
  python main.py --mode TESTNET           # Binance testnet
  python main.py --mode LIVE              # Real trading (requires explicit config)
  python main.py --once                   # Run single cycle and exit
  python main.py --check                  # Test connectivity only
  python main.py --config custom.yaml     # Use custom config file

Environment variables:
  BINANCE_API_KEY       API key (read from .env or shell)
  BINANCE_API_SECRET    API secret
  RUN_MODE              Override run mode
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))


def load_dotenv(path: str = ".env") -> None:
    """Load .env file into environment variables (no external dependency)."""
    env_path = Path(path)
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dynamic Regime-Adaptive 2.0x Live Trading Bot"
    )
    parser.add_argument(
        "--mode",
        choices=["BACKTEST", "DRY_RUN", "TESTNET", "LIVE"],
        default=None,
        help="Run mode (overrides config and env var)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Start live monitoring web dashboard server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Web dashboard port (default: 8090)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Test exchange connectivity and exit",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    return parser.parse_args()


def main() -> None:
    # Load .env before anything else
    args = parse_args()
    env_path = args.env if Path(args.env).exists() else str(Path(__file__).parent / args.env)
    load_dotenv(env_path)

    # Override RUN_MODE from CLI if specified
    if args.mode:
        os.environ["RUN_MODE"] = args.mode

    # Now import everything (after env is loaded)
    from src.config.config_manager import ConfigManager
    from src.core.enums import RunMode
    from src.core.models import BotState
    from src.data.candle_service import CandleService
    from src.data.live_provider import LiveDataProvider
    from src.exchanges.dry_run_exchange import DryRunExchange
    from src.exchanges.exchange_gateway import ExchangeGateway
    from src.services.portfolio_service import PortfolioService
    from src.services.risk_manager import RiskManager
    from src.services.state_store import JsonStateStore
    from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
    from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
    from src.strategy.hybrid_strategy import HybridStrategy
    from src.orchestrator import BotOrchestrator
    from src.utils.logging_utils import setup_logger

    # Load config
    if Path(args.config).exists():
        config_path = args.config
    elif (Path(__file__).parent / args.config).exists():
        config_path = str(Path(__file__).parent / args.config)
    else:
        config_path = None

    cm = ConfigManager(config_path)
    config = cm.load()

    # Setup logging
    logger = setup_logger(
        name="bot",
        level=config.logging.level,
        log_file=config.logging.file,
    )

    logger.info("=" * 60)
    logger.info("Dynamic Regime-Adaptive 2.0x Trading Bot")
    logger.info("Mode: %s", config.run_mode.name)
    logger.info("=" * 60)

    # Safety gate for LIVE mode
    if config.run_mode == RunMode.LIVE:
        logger.warning("⚠️  LIVE MODE — Real money at risk!")
        confirm = os.environ.get("CONFIRM_LIVE", "")
        if confirm != "YES_I_UNDERSTAND":
            logger.error(
                "LIVE mode requires CONFIRM_LIVE=YES_I_UNDERSTAND env var. "
                "Aborting."
            )
            sys.exit(1)

    # Load state
    state_store = JsonStateStore(config.state.path)
    state = state_store.load_state()

    # Callback to persist dry run balance changes to config.yaml & bot_state.json
    def on_dry_run_balance_change(balances: Dict[str, float]) -> None:
        try:
            cm.save_dry_run_balances(balances)
            st = state_store.load_state()
            st.strategy_state["dry_run_balances"] = balances
            state_store.save_state(st)
        except Exception as ex:
            logger.error("Failed to persist dry run balances: %s", ex)

    # Initialize exchange gateway
    if config.run_mode == RunMode.DRY_RUN:
        saved_dry_balances = state.strategy_state.get("dry_run_balances")
        init_balances = saved_dry_balances or config.dry_run.initial_balances
        gateway = DryRunExchange(
            initial_balances=init_balances,
            fee_rate=0.001,
            on_balance_change=on_dry_run_balance_change,
        )
        logger.info("Using DRY_RUN simulated exchange with balances: %s", init_balances)
    else:
        gateway = ExchangeGateway(config.exchange, config.run_mode)
        gateway.initialize()
        logger.info("Connected to %s exchange", config.exchange.name)

    # Connectivity check mode
    if args.check:
        logger.info("Connectivity check passed ✓")
        if config.run_mode != RunMode.DRY_RUN:
            balance = gateway.fetch_balance()
            logger.info("Account balances: %s", {
                k: f"${v['total']:.4f}" for k, v in balance.items()
                if isinstance(v, dict) and v.get("total", 0) > 0
            })
        sys.exit(0)

    # Initialize services
    data_provider = LiveDataProvider(gateway)
    candle_service = CandleService(
        provider=data_provider,
        timeframe=config.strategy.timeframe,
        warmup_candles=config.strategy.warmup_candles,
    )
    portfolio_service = PortfolioService(
        gateway,
        allow_market_orders=config.risk.allow_market_orders,
        is_futures=(config.exchange.market_type == "future"),
    )
    risk_manager = RiskManager(config.risk)

    # Initialize strategy
    asset_weights = {
        name: cfg.weight
        for name, cfg in config.strategy.assets.items()
    }
    macro_strategy = RegimeAdaptiveStrategy(
        asset_weights=asset_weights,
        sma_regime_period=config.strategy.sma_regime_period,
        bull_leverage=config.strategy.bull_leverage,
        bear_short_hedge_weight=config.strategy.bear_short_hedge_weight,
    )
    
    micro_strategy = MicroSatelliteStrategy(
        asset_weights=asset_weights,
    )
    
    strategy = HybridStrategy(
        macro_strategy=macro_strategy,
        micro_strategy=micro_strategy,
        core_ratio=config.strategy.core_ratio,
    )

    # Build orchestrator
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

    # Start Dashboard Server if requested
    if args.dashboard:
        import threading
        from src.web.server import run_dashboard_server
        web_server = run_dashboard_server(
            config=config,
            gateway=gateway,
            state_store=state_store,
            orchestrator=orchestrator,
            port=args.port,
        )
        t = threading.Thread(target=web_server.serve_forever, daemon=True)
        t.start()
        logger.info("⚡ Live Web Dashboard running at http://localhost:%d", args.port)

        # Ensure AUTO_PULL (auto_updater.sh) is active immediately on server start
        try:
            updater_script = Path(__file__).parent / "scripts" / "auto_updater.sh"
            project_root = Path(__file__).parent.parent
            if updater_script.exists():
                import subprocess
                subprocess.Popen([str(updater_script), "start"], cwd=str(project_root))
                logger.info("🟢 Git Auto-Updater (AUTO_PULL) initiated automatically on server start.")
        except Exception as ex:
            logger.warning("Could not auto-start Git Auto-Updater: %s", ex)

    # Run
    if args.once:
        logger.info("Running single cycle...")
        success = orchestrator.run_once()
        sys.exit(0 if success else 1)
    else:
        orchestrator.run_loop()


if __name__ == "__main__":
    main()
