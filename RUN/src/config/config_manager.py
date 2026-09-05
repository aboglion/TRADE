"""
Configuration manager.

Loads settings from YAML file and overlays environment variables.
API secrets are read ONLY from environment variables — never from files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.core.enums import RunMode
from src.core.exceptions import ConfigError


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
    assets: Dict[str, AssetConfig] = field(default_factory=dict)
    warmup_candles: int = 1200
    sma_regime_period: int = 150
    bull_leverage: float = 2.0
    bear_short_hedge_weight: float = 0.0   # 0 for spot-only (hold USDT)
    cash_apr: float = 0.0
    core_ratio: float = 0.80               # Macro/Micro allocation split
    # Macro per-asset configs are kept as dicts matching engine.py constants
    macro_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    micro_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_orders_per_cycle: int = 6
    max_single_order_usd: float = 10000.0
    max_portfolio_change_pct: float = 0.20
    min_seconds_between_orders: int = 10
    allowed_symbols: List[str] = field(default_factory=list)
    banned_symbols: List[str] = field(default_factory=list)
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
    initial_balances: Dict[str, float] = field(
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

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path
        self._config: Optional[BotConfig] = None

    def load(self) -> BotConfig:
        """Load and validate configuration."""
        raw: Dict[str, Any] = {}
        if self._config_path and Path(self._config_path).exists():
            with open(self._config_path, "r") as f:
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

    def _load_run_mode(self, config: BotConfig, raw: Dict) -> None:
        mode_str = os.environ.get("RUN_MODE", raw.get("run_mode", "DRY_RUN"))
        try:
            config.run_mode = RunMode[mode_str.upper()]
        except KeyError:
            raise ConfigError(
                f"Invalid run_mode '{mode_str}'. "
                f"Valid: {[m.name for m in RunMode]}"
            )

    def _load_exchange(self, config: BotConfig, raw: Dict) -> None:
        ex_raw = raw.get("exchange", {})
        config.exchange = ExchangeConfig(
            name=ex_raw.get("name", "binance"),
            timeout_ms=ex_raw.get("timeout_ms", 30000),
            rate_limit=ex_raw.get("rate_limit", True),
            max_retries=ex_raw.get("max_retries", 3),
            retry_delay_base_ms=ex_raw.get("retry_delay_base_ms", 1000),
            api_key=os.environ.get("BINANCE_API_KEY", ""),
            api_secret=os.environ.get("BINANCE_API_SECRET", ""),
            market_type=ex_raw.get("market_type", "future"),
            portfolio_margin=ex_raw.get("portfolio_margin", False),
        )

    def _load_strategy(self, config: BotConfig, raw: Dict) -> None:
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
            bull_leverage=s_raw.get("bull_leverage", 2.0),
            bear_short_hedge_weight=s_raw.get("bear_short_hedge_weight", 0.0),
            cash_apr=s_raw.get("cash_apr", 0.0),
            core_ratio=s_raw.get("core_ratio", 0.80),
            macro_configs=s_raw.get("macro_configs", {}),
            micro_config=s_raw.get("micro_config", {}),
        )

    def _load_risk(self, config: BotConfig, raw: Dict) -> None:
        r_raw = raw.get("risk", {})
        config.risk = RiskConfig(
            max_orders_per_cycle=r_raw.get("max_orders_per_cycle", 6),
            max_single_order_usd=r_raw.get("max_single_order_usd", 10000.0),
            max_portfolio_change_pct=r_raw.get("max_portfolio_change_pct", 0.20),
            min_seconds_between_orders=r_raw.get("min_seconds_between_orders", 10),
            allowed_symbols=r_raw.get("allowed_symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT"]),
            banned_symbols=r_raw.get("banned_symbols", []),
            allow_market_orders=r_raw.get("allow_market_orders", False),
            kill_switch=r_raw.get("kill_switch", False),
            max_drawdown_pct=r_raw.get("max_drawdown_pct", 0.60),
            min_order_value_usd=r_raw.get("min_order_value_usd", 11.0),
        )

    def _load_state(self, config: BotConfig, raw: Dict) -> None:
        s_raw = raw.get("state", {})
        config.state = StateConfig(
            backend=s_raw.get("backend", "json"),
            path=s_raw.get("path", "data/bot_state.json"),
        )

    def _load_logging(self, config: BotConfig, raw: Dict) -> None:
        l_raw = raw.get("logging", {})
        config.logging = LoggingConfig(
            level=l_raw.get("level", "INFO"),
            file=l_raw.get("file", "logs/bot.log"),
        )

    def _load_scheduler(self, config: BotConfig, raw: Dict) -> None:
        sc_raw = raw.get("scheduler", {})
        config.scheduler = SchedulerConfig(
            poll_interval_seconds=sc_raw.get("poll_interval_seconds", 300),
            max_consecutive_errors=sc_raw.get("max_consecutive_errors", 10),
        )

    def _load_dry_run(self, config: BotConfig, raw: Dict) -> None:
        dr_raw = raw.get("dry_run", {})
        init_bal = dr_raw.get("initial_balances", {"USDT": 1000.0})
        # Ensure values are float
        parsed_bal = {str(k).upper(): float(v) for k, v in init_bal.items()}
        config.dry_run = DryRunConfig(initial_balances=parsed_bal)

    def _load_telegram(self, config: BotConfig, raw: Dict) -> None:
        tg_raw = raw.get("telegram", {})
        env_enabled = os.environ.get("TELEGRAM_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled.lower() in ("true", "1", "yes")
        else:
            enabled = bool(tg_raw.get("enabled", False))

        config.telegram = TelegramConfig(
            enabled=enabled,
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", tg_raw.get("bot_token", "")),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", tg_raw.get("chat_id", "")),
            dashboard_url=os.environ.get("TELEGRAM_DASHBOARD_URL", tg_raw.get("dashboard_url", "")),
        )

    def save_telegram_config(
        self, enabled: bool, bot_token: str, chat_id: str, dashboard_url: str = ""
    ) -> None:
        """Persist updated telegram settings back to config.yaml."""
        if self._config:
            self._config.telegram.enabled = enabled
            self._config.telegram.bot_token = bot_token
            self._config.telegram.chat_id = chat_id
            self._config.telegram.dashboard_url = dashboard_url

        if not self._config_path or not Path(self._config_path).exists():
            return

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

    def save_dry_run_balances(self, balances: Dict[str, float]) -> None:
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
        if config.risk.max_portfolio_change_pct <= 0 or config.risk.max_portfolio_change_pct > 1.0:
            raise ConfigError("max_portfolio_change_pct must be in (0, 1.0].")

        # Warmup candles check for 4h SMA-150 regime detection
        required_candles = config.strategy.sma_regime_period * 6
        if config.strategy.timeframe == "4h" and config.strategy.warmup_candles < required_candles:
            raise ConfigError(
                f"warmup_candles ({config.strategy.warmup_candles}) is less than required "
                f"({required_candles}) for {config.strategy.sma_regime_period}-day SMA on 4h timeframe."
            )
