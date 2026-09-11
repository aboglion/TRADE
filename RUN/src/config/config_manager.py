"""
Configuration manager.

Loads settings from YAML file and overlays environment variables.
API secrets are read ONLY from environment variables — never from files.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.enums import RunMode
from src.core.exceptions import ConfigError

logger = logging.getLogger("bot.config")



# ── Typed config sections ────────────────────────────────────

@dataclass
class ExchangeConfig:
    name: str = "binance"
    timeout_ms: int = 30000
    rate_limit: bool = True
    max_retries: int = 3
    retry_delay_base_ms: int = 1000
    api_key: str = ""           # Populated from env var
    api_secret: str = ""        # Populated from env var
    market_type: str = "future" # "spot" or "future"
    portfolio_margin: bool = False


@dataclass
class AssetConfig:
    weight: float = 0.0
    pair: str = ""


@dataclass
class StrategyConfig:
    timeframe: str = "4h"
    assets: dict[str, AssetConfig] = field(default_factory=dict)
    warmup_candles: int = 1200
    sma_regime_period: int = 150
    conviction_leverage: float = 10.0
    bull_leverage: float = 10.0
    mid_leverage: float = 5.0
    base_leverage: float = 2.5
    min_leverage: float = 1.0
    momentum_cutoff_pct: float = -0.02
    safe_cash_weight: float = 0.60
    safe_spot_weight: float = 0.30
    safe_micro_weight: float = 0.10
    flash_wick_limit: float = -0.038
    ladder_steps: list[float] = field(default_factory=lambda: [1.0, 2.0, 4.0, 10.0])
    bear_short_hedge_weight: float = 0.35   # 0.35 for 35% margin @ 2.0x short hedge on BTC
    short_leverage: float = 2.0            # 2.0x leverage for short hedge
    cash_apr: float = 0.04
    core_ratio: float = 0.80               # Macro/Micro allocation split
    # Macro per-asset configs are kept as dicts matching engine.py constants
    macro_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    micro_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_orders_per_cycle: int = 6
    max_single_order_usd: float = 10000.0
    max_portfolio_change_pct: float = 3.5
    min_seconds_between_orders: int = 10
    allowed_symbols: list[str] = field(default_factory=list)
    banned_symbols: list[str] = field(default_factory=list)
    allow_market_orders: bool = False
    kill_switch: bool = False
    max_drawdown_pct: float = 0.60
    min_order_value_usd: float = 11.0     # Binance minimum


@dataclass
class StateConfig:
    backend: str = "json"
    path: str = "data/bot_state.json"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/bot.log"


@dataclass
class SchedulerConfig:
    poll_interval_seconds: int = 300      # 5 minutes
    max_consecutive_errors: int = 10


@dataclass
class DryRunConfig:
    initial_balances: dict[str, float] = field(
        default_factory=lambda: {"USDT": 1000.0}
    )


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    dashboard_url: str = ""


# ── Main config container ────────────────────────────────────

@dataclass
class BotConfig:
    run_mode: RunMode = RunMode.DRY_RUN
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    state: StateConfig = field(default_factory=StateConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    dry_run: DryRunConfig = field(default_factory=DryRunConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


class ConfigManager:
    """
    Loads and validates bot configuration.

    Priority: environment variables > YAML file > defaults.
    """

    def __init__(self, config_path: str | None = None):
        self._config_path = config_path
        self._config: BotConfig | None = None

    def load(self) -> BotConfig:
        """Load and validate configuration."""
        raw: dict[str, Any] = {}
        if self._config_path:
            cfg_p = Path(self._config_path)
            if not cfg_p.exists():
                parent_dir = cfg_p.parent
                example_p = parent_dir / "config.example.yaml"
                if not example_p.exists():
                    example_p = Path("RUN/config.example.yaml")
                if example_p.exists():
                    try:
                        shutil.copy2(example_p, cfg_p)
                        logger.info("Auto-created missing config file %s from %s", cfg_p, example_p)
                    except Exception as ex:
                        logger.warning("Could not auto-create config file: %s", ex)
            if cfg_p.exists():
                with open(cfg_p, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}

        config = BotConfig()
        self._load_run_mode(config, raw)
        self._load_exchange(config, raw)
        self._load_strategy(config, raw)
        self._load_risk(config, raw)
        self._load_state(config, raw)
        self._load_logging(config, raw)
        self._load_scheduler(config, raw)
        self._load_dry_run(config, raw)
        self._load_telegram(config, raw)
        self._validate(config)

        self._config = config
        return config

    @property
    def config(self) -> BotConfig:
        if self._config is None:
            raise ConfigError("Config not loaded. Call load() first.")
        return self._config

    # ── Section loaders ──────────────────────────────────────

    def _load_run_mode(self, config: BotConfig, raw: dict) -> None:
        mode_str = os.environ.get("RUN_MODE", raw.get("run_mode", "DRY_RUN"))
        try:
            config.run_mode = RunMode[mode_str.upper()]
        except KeyError as err:
            raise ConfigError(
                f"Invalid run_mode '{mode_str}'. "
                f"Valid: {[m.name for m in RunMode]}"
            ) from err

    def _load_exchange(self, config: BotConfig, raw: dict) -> None:
        ex_raw = raw.get("exchange", {})
        config.exchange = ExchangeConfig(
            name=ex_raw.get("name", "binance"),
            timeout_ms=ex_raw.get("timeout_ms", 30000),
            rate_limit=ex_raw.get("rate_limit", True),
            max_retries=ex_raw.get("max_retries", 3),
            retry_delay_base_ms=ex_raw.get("retry_delay_base_ms", 1000),
            api_key=os.environ.get("BINANCE_API_KEY", "").strip().strip("'\"").strip(),
            api_secret=os.environ.get("BINANCE_API_SECRET", "").strip().strip("'\"").strip(),
            market_type=ex_raw.get("market_type", "future"),
            portfolio_margin=ex_raw.get("portfolio_margin", False),
        )

    def _load_strategy(self, config: BotConfig, raw: dict) -> None:
        s_raw = raw.get("strategy", {})
        assets_raw = s_raw.get("assets", {
            "BTC": {"weight": 0.40, "pair": "BTC/USDT"},
            "ETH": {"weight": 0.30, "pair": "ETH/USDT"},
            "SOL": {"weight": 0.30, "pair": "SOL/USDT"},
        })
        assets = {}
        for name, cfg in assets_raw.items():
            assets[name] = AssetConfig(
                weight=cfg.get("weight", 0.0),
                pair=cfg.get("pair", f"{name}/USDT"),
            )
        config.strategy = StrategyConfig(
            timeframe=s_raw.get("timeframe", "4h"),
            assets=assets,
            warmup_candles=s_raw.get("warmup_candles", 1200),
            sma_regime_period=s_raw.get("sma_regime_period", 150),
            conviction_leverage=s_raw.get("conviction_leverage", 10.0),
            bull_leverage=s_raw.get("bull_leverage", 10.0),
            mid_leverage=s_raw.get("mid_leverage", 5.0),
            base_leverage=s_raw.get("base_leverage", 2.5),
            min_leverage=s_raw.get("min_leverage", 1.0),
            momentum_cutoff_pct=s_raw.get("momentum_cutoff_pct", -0.02),
            safe_cash_weight=s_raw.get("safe_cash_weight", 0.60),
            safe_spot_weight=s_raw.get("safe_spot_weight", 0.30),
            safe_micro_weight=s_raw.get("safe_micro_weight", 0.10),
            flash_wick_limit=s_raw.get("flash_wick_limit", -0.038),
            ladder_steps=s_raw.get("ladder_steps", [1.0, 2.0, 4.0, 10.0]),
            bear_short_hedge_weight=s_raw.get("bear_short_hedge_weight", 0.35),
            short_leverage=s_raw.get("short_leverage", 2.0),
            cash_apr=s_raw.get("cash_apr", 0.04),
            core_ratio=s_raw.get("core_ratio", 0.80),
            macro_configs=s_raw.get("macro_configs", {}),
            micro_config=s_raw.get("micro_config", {}),
        )

    def _load_risk(self, config: BotConfig, raw: dict) -> None:
        r_raw = raw.get("risk", {})
        config.risk = RiskConfig(
            max_orders_per_cycle=r_raw.get("max_orders_per_cycle", 6),
            max_single_order_usd=r_raw.get("max_single_order_usd", 10000.0),
            max_portfolio_change_pct=r_raw.get("max_portfolio_change_pct", 3.5),
            min_seconds_between_orders=r_raw.get("min_seconds_between_orders", 10),
            allowed_symbols=r_raw.get("allowed_symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT"]),
            banned_symbols=r_raw.get("banned_symbols", []),
            allow_market_orders=r_raw.get("allow_market_orders", False),
            kill_switch=r_raw.get("kill_switch", False),
            max_drawdown_pct=r_raw.get("max_drawdown_pct", 0.60),
            min_order_value_usd=r_raw.get("min_order_value_usd", 11.0),
        )

    def _load_state(self, config: BotConfig, raw: dict) -> None:
        s_raw = raw.get("state", {})
        raw_state_path = s_raw.get("path", "data/bot_state.json")
        state_path = Path(raw_state_path)
        if not state_path.is_absolute() and self._config_path:
            cfg_parent = Path(self._config_path).resolve().parent
            if (cfg_parent / raw_state_path).exists():
                state_path = cfg_parent / raw_state_path
        config.state = StateConfig(
            backend=s_raw.get("backend", "json"),
            path=str(state_path),
        )

    def _load_logging(self, config: BotConfig, raw: dict) -> None:
        l_raw = raw.get("logging", {})
        raw_log_file = l_raw.get("file", "logs/bot.log")
        log_file = Path(raw_log_file)
        if not log_file.is_absolute() and self._config_path:
            cfg_parent = Path(self._config_path).resolve().parent
            if (cfg_parent / raw_log_file).exists():
                log_file = cfg_parent / raw_log_file
        config.logging = LoggingConfig(
            level=l_raw.get("level", "INFO"),
            file=str(log_file),
        )

    def _load_scheduler(self, config: BotConfig, raw: dict) -> None:
        sc_raw = raw.get("scheduler", {})
        config.scheduler = SchedulerConfig(
            poll_interval_seconds=sc_raw.get("poll_interval_seconds", 300),
            max_consecutive_errors=sc_raw.get("max_consecutive_errors", 10),
        )

    def _load_dry_run(self, config: BotConfig, raw: dict) -> None:
        dr_raw = raw.get("dry_run", {})
        init_bal = dr_raw.get("initial_balances", {"USDT": 1000.0})
        # Ensure values are float
        parsed_bal = {str(k).upper(): float(v) for k, v in init_bal.items()}
        config.dry_run = DryRunConfig(initial_balances=parsed_bal)

    def _load_telegram(self, config: BotConfig, raw: dict) -> None:
        is_test = self._config_path and any(x in str(self._config_path) for x in ("/tmp", "pytest", "tempfile"))
        if not is_test:
            try:
                from src.utils.env_manager import load_dotenv
                load_dotenv()
            except Exception:
                pass

        tg_raw = raw.get("telegram", {})
        env_enabled = os.environ.get("TELEGRAM_ENABLED") if not is_test else None
        if env_enabled is not None:
            enabled = env_enabled.lower() in ("true", "1", "yes")
        else:
            enabled = bool(tg_raw.get("enabled", False))

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", tg_raw.get("bot_token", "")) if not is_test else tg_raw.get("bot_token", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", tg_raw.get("chat_id", "")) if not is_test else tg_raw.get("chat_id", "")
        dashboard_url = os.environ.get("TELEGRAM_DASHBOARD_URL", tg_raw.get("dashboard_url", "")) if not is_test else tg_raw.get("dashboard_url", "")

        # Fallback: Recover telegram settings from state store (bot_state.json) if missing in config.yaml
        if not bot_token or not chat_id:
            try:
                state_path_str = config.state.path if (config and hasattr(config, "state")) else "data/bot_state.json"
                state_p = Path(state_path_str)
                if state_p.exists():
                    with open(state_p, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                    saved_tg = state_data.get("strategy_state", {}).get("telegram", {})
                    saved_token = saved_tg.get("bot_token", "")
                    saved_chat = saved_tg.get("chat_id", "")
                    if saved_token and saved_chat:
                        if not bot_token:
                            bot_token = saved_token
                        if not chat_id:
                            chat_id = saved_chat
                        if not dashboard_url:
                            dashboard_url = saved_tg.get("dashboard_url", "")
                        if env_enabled is None and "enabled" in saved_tg:
                            enabled = bool(saved_tg.get("enabled", enabled))
                        logger.info("Recovered Telegram settings from bot_state.json backup")
            except Exception as ex:
                logger.debug("Failed to load Telegram backup from state store: %s", ex)

        config.telegram = TelegramConfig(
            enabled=enabled,
            bot_token=bot_token,
            chat_id=chat_id,
            dashboard_url=dashboard_url,
        )

    def save_telegram_config(
        self, enabled: bool, bot_token: str, chat_id: str, dashboard_url: str = ""
    ) -> None:
        """Persist updated telegram settings back to .env, config.yaml AND bot_state.json backup."""
        if self._config:
            self._config.telegram.enabled = enabled
            self._config.telegram.bot_token = bot_token
            self._config.telegram.chat_id = chat_id
            self._config.telegram.dashboard_url = dashboard_url

        is_test = self._config_path and any(x in str(self._config_path) for x in ("/tmp", "pytest", "tempfile"))
        if not is_test:
            # 1. Persist directly to .env file for permanent storage protected from git
            try:
                from src.utils.env_manager import update_env_file
                update_env_file({
                    "TELEGRAM_ENABLED": enabled,
                    "TELEGRAM_BOT_TOKEN": bot_token,
                    "TELEGRAM_CHAT_ID": chat_id,
                    "TELEGRAM_DASHBOARD_URL": dashboard_url,
                })
            except Exception as ex:
                logger.warning("Could not persist Telegram configuration to .env: %s", ex)

        if self._config_path and Path(self._config_path).exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                if "telegram" not in data:
                    data["telegram"] = {}
                data["telegram"]["enabled"] = enabled
                data["telegram"]["bot_token"] = bot_token
                data["telegram"]["chat_id"] = chat_id
                data["telegram"]["dashboard_url"] = dashboard_url

                with open(self._config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
            except Exception as e:
                logger.error("Failed to save telegram config to %s: %s", self._config_path, e)

        # Backup copy to bot_state.json if state path exists
        if self._config and hasattr(self._config, "state") and self._config.state.path:
            state_path_str = self._config.state.path
            state_p = Path(state_path_str)
            try:
                if state_p.exists():
                    with open(state_p, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    if "strategy_state" not in s_data or not isinstance(s_data["strategy_state"], dict):
                        s_data["strategy_state"] = {}
                    s_data["strategy_state"]["telegram"] = {
                        "enabled": enabled,
                        "bot_token": bot_token,
                        "chat_id": chat_id,
                        "dashboard_url": dashboard_url,
                    }
                    tmp_p = state_p.with_suffix(".tmp")
                    with open(tmp_p, "w", encoding="utf-8") as f:
                        json.dump(s_data, f, indent=2, ensure_ascii=False)
                    tmp_p.replace(state_p)
                    logger.info("Backed up Telegram settings to state store %s", state_p)
            except Exception as ex:
                logger.warning("Could not backup Telegram settings to state store: %s", ex)


    def save_dry_run_balances(self, balances: dict[str, float]) -> None:
        """Persist updated dry_run.initial_balances back to config.yaml."""
        parsed = {str(k).upper(): round(float(v), 8) for k, v in balances.items() if float(v) >= 0}
        if self._config:
            self._config.dry_run.initial_balances = parsed

        if not self._config_path or not Path(self._config_path).exists():
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if "dry_run" not in data:
                data["dry_run"] = {}
            data["dry_run"]["initial_balances"] = parsed

            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
        except Exception as e:
            logger.error("Failed to save dry run balances to %s: %s", self._config_path, e)

    def save_run_mode(self, mode: str) -> None:
        """Persist updated run_mode back to config.yaml and logs/last_mode."""
        mode_upper = mode.upper()
        if self._config:
            try:
                self._config.run_mode = RunMode[mode_upper]
            except KeyError:
                pass

        if self._config_path and Path(self._config_path).exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                data["run_mode"] = mode_upper

                with open(self._config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
            except Exception as e:
                logger.error("Failed to save run_mode to %s: %s", self._config_path, e)

        # Write to logs/last_mode for Makefile / restart persistence
        for lm_path in (Path("logs/last_mode"), Path("RUN/logs/last_mode")):
            try:
                lm_path.parent.mkdir(parents=True, exist_ok=True)
                lm_path.write_text(mode_upper)
            except Exception:
                pass

    def save_kill_switch(self, enabled: bool) -> None:
        """Persist updated risk.kill_switch state back to config.yaml."""
        if self._config:
            self._config.risk.kill_switch = enabled

        if not self._config_path or not Path(self._config_path).exists():
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if "risk" not in data:
                data["risk"] = {}
            data["risk"]["kill_switch"] = bool(enabled)

            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
            logger.info("Persisted risk.kill_switch=%s to %s", enabled, self._config_path)
        except Exception as e:
            logger.error("Failed to save kill_switch to %s: %s", self._config_path, e)

    # ── Validation ───────────────────────────────────────────

    def _validate(self, config: BotConfig) -> None:
        """Validate critical configuration."""
        # LIVE mode requires explicit API keys
        if config.run_mode == RunMode.LIVE:
            if not config.exchange.api_key or not config.exchange.api_secret:
                raise ConfigError(
                    "LIVE mode requires BINANCE_API_KEY and BINANCE_API_SECRET "
                    "environment variables."
                )

        # TESTNET also needs keys
        if config.run_mode == RunMode.TESTNET:
            if not config.exchange.api_key or not config.exchange.api_secret:
                raise ConfigError(
                    "TESTNET mode requires API keys for the testnet endpoint."
                )

        # Strategy weights must sum to ~1.0
        total_weight = sum(a.weight for a in config.strategy.assets.values())
        if abs(total_weight - 1.0) > 0.01:
            raise ConfigError(
                f"Asset weights sum to {total_weight:.4f}, must be ~1.0."
            )

        if config.risk.max_single_order_usd <= 0:
            raise ConfigError("max_single_order_usd must be positive.")
        if config.risk.max_portfolio_change_pct <= 0 or config.risk.max_portfolio_change_pct > 5.0:
            raise ConfigError("max_portfolio_change_pct must be in (0, 5.0].")

        # Warmup candles check for 4h SMA-150 regime detection
        required_candles = config.strategy.sma_regime_period * 6
        if config.strategy.timeframe == "4h" and config.strategy.warmup_candles < required_candles:
            raise ConfigError(
                f"warmup_candles ({config.strategy.warmup_candles}) is less than required "
                f"({required_candles}) for {config.strategy.sma_regime_period}-day SMA on 4h timeframe."
            )
