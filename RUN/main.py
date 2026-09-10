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
import signal
import sys
from pathlib import Path

# SIGHUP = terminal closed / disconnected. Ignore it so background runs 24/7
if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))


def _ensure_venv() -> None:
    """Ensure runtime executes within a virtual environment where dependencies are installed."""
    try:
        import ccxt  # noqa: F401
        return
    except ImportError:
        pass

    # 1. Search for any installed site-packages on the system and add them to sys.path
    import glob
    site_candidates = (
        glob.glob("/root/**/site-packages", recursive=True)
        + glob.glob("/home/**/site-packages", recursive=True)
        + glob.glob(str(Path(__file__).resolve().parent.parent) + "/**/site-packages", recursive=True)
    )
    for sp in site_candidates:
        if os.path.isdir(sp) and sp not in sys.path:
            sys.path.insert(0, sp)

    try:
        import ccxt  # noqa: F401
        return
    except ImportError:
        pass

    # 2. Locate virtual environment python executable without resolving symlinks
    cur_file = Path(__file__).resolve()
    run_dir = cur_file.parent
    proj_dir = run_dir.parent

    candidates = [
        proj_dir / "venv" / "bin" / "python3",
        proj_dir / "venv" / "bin" / "python",
        proj_dir / ".venv" / "bin" / "python3",
        proj_dir / ".venv" / "bin" / "python",
        run_dir / "venv" / "bin" / "python3",
        run_dir / "venv" / "bin" / "python",
        run_dir / ".venv" / "bin" / "python3",
        Path("/root/TRADE/venv/bin/python3"),
        Path("/root/TRADE/venv/bin/python"),
    ]
    if "VIRTUAL_ENV" in os.environ:
        candidates.insert(0, Path(os.environ["VIRTUAL_ENV"]) / "bin" / "python3")
        candidates.insert(1, Path(os.environ["VIRTUAL_ENV"]) / "bin" / "python")

    for cand in candidates:
        if cand.is_file() and os.access(str(cand), os.X_OK):
            target = str(cand)  # DO NOT call cand.resolve() to keep venv path!
            if target != sys.executable:
                os.environ["VIRTUAL_ENV"] = str(cand.parent.parent)
                os.environ["PATH"] = f"{cand.parent}:{os.environ.get('PATH', '')}"
                os.execv(target, [target] + sys.argv)


_ensure_venv()


def load_dotenv(path: str = ".env") -> None:
    """Load .env files into environment variables from multiple candidate locations."""
    candidates = []
    if path:
        candidates.append(Path(path))

    cur_dir = Path.cwd()
    run_dir = Path(__file__).resolve().parent
    proj_dir = run_dir.parent

    candidates.extend([
        run_dir / ".env",
        proj_dir / ".env",
        cur_dir / "RUN" / ".env",
        cur_dir / ".env",
        Path("/root/TRADE/RUN/.env"),
        Path("/root/TRADE/.env"),
    ])

    seen = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if cand.is_file():
                with open(cand, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        if not key or not value:
                            continue
                        # Never overwrite a real key with a placeholder dummy value
                        if value in ("your_api_key_here", "your_api_secret_here"):
                            continue
                        if key not in os.environ or not os.environ[key] or os.environ[key] in ("your_api_key_here", "your_api_secret_here"):
                            os.environ[key] = value
        except Exception:
            pass


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
    logger.info("Dynamic Regime-Adaptive 10x Trading Bot (Institutional Crash Shield & Conviction Rocket)")
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
        conviction_leverage=getattr(config.strategy, "conviction_leverage", 10.0),
        bull_leverage=config.strategy.bull_leverage,
        mid_leverage=config.strategy.mid_leverage,
        base_leverage=getattr(config.strategy, "base_leverage", 2.5),
        min_leverage=config.strategy.min_leverage,
        momentum_cutoff_pct=getattr(config.strategy, "momentum_cutoff_pct", -0.02),
        safe_cash_weight=getattr(config.strategy, "safe_cash_weight", 0.60),
        safe_spot_weight=getattr(config.strategy, "safe_spot_weight", 0.30),
        safe_micro_weight=getattr(config.strategy, "safe_micro_weight", 0.10),
        flash_wick_limit=config.strategy.flash_wick_limit,
        ladder_steps=config.strategy.ladder_steps,
        bear_short_hedge_weight=config.strategy.bear_short_hedge_weight,
        short_leverage=getattr(config.strategy, "short_leverage", 2.0),
        core_ratio=config.strategy.core_ratio,
    )
    
    micro_strategy = MicroSatelliteStrategy(
        asset_weights=asset_weights,
    )
    
    strategy = HybridStrategy(
        macro_strategy=macro_strategy,
        micro_strategy=micro_strategy,
        core_ratio=config.strategy.core_ratio,
    )

    # Initialize Telegram service
    from src.services.telegram_service import TelegramService
    telegram_service = TelegramService(
        bot_token=config.telegram.bot_token,
        chat_id=config.telegram.chat_id,
        enabled=config.telegram.enabled,
        dashboard_url=config.telegram.dashboard_url,
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
        telegram_service=telegram_service,
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
            telegram_service=telegram_service,
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
