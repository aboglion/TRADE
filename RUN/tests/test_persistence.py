"""
Unit tests for data persistence & recovery across updates and restarts.
"""

import os
from pathlib import Path

from src.config.config_manager import ConfigManager
from src.core.models import BotState
from src.services.state_store import JsonStateStore


def test_completed_orders_retention_limit(tmp_path: Path):
    """Verify BotState.to_dict() preserves up to 5000 completed orders."""
    state = BotState()
    # Add 250 dummy orders
    for i in range(250):
        state.completed_orders.append({"order_id": f"ord_{i}", "symbol": "BTC/USDT"})

    serialized = state.to_dict()
    assert len(serialized["completed_orders"]) == 250, "Should retain all 250 orders, not truncate to 100"

    # Test loading back via JsonStateStore
    state_file = tmp_path / "bot_state.json"
    store = JsonStateStore(str(state_file))
    store.save_state(state)

    reloaded = store.load_state()
    assert len(reloaded.completed_orders) == 250


def test_telegram_config_recovery_when_yaml_reset(tmp_path: Path):
    """
    Verify Telegram settings can be restored from bot_state.json
    if config.yaml was reset to defaults (e.g. after a git pull).
    """
    config_file = tmp_path / "config.yaml"
    state_file = tmp_path / "bot_state.json"

    # Clear any residual environment variables to test pure state store recovery
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ENABLED", "TELEGRAM_DASHBOARD_URL"):
        os.environ.pop(k, None)

    # 1. Create initial state with backed-up telegram settings
    store = JsonStateStore(str(state_file))
    state = BotState()
    state.strategy_state["telegram"] = {
        "enabled": True,
        "bot_token": "123456:ABCdefGHIjklMNOpqrsTUVwxyz",
        "chat_id": "987654321",
        "dashboard_url": "http://my-trading-dashboard.local:8090",
    }
    store.save_state(state)

    # 2. Save reset/default config.yaml (simulating git pull of repo default config.yaml)
    default_config_content = f"""
run_mode: DRY_RUN
state:
  path: {state_file}
telegram:
  enabled: false
  bot_token: ''
  chat_id: ''
  dashboard_url: ''
"""
    config_file.write_text(default_config_content, encoding="utf-8")

    # 3. Load config via ConfigManager
    cm = ConfigManager(str(config_file))
    cfg = cm.load()

    # 4. Verify Telegram settings were successfully recovered from bot_state.json!
    assert cfg.telegram.enabled is True
    assert cfg.telegram.bot_token == "123456:ABCdefGHIjklMNOpqrsTUVwxyz"
    assert cfg.telegram.chat_id == "987654321"
    assert cfg.telegram.dashboard_url == "http://my-trading-dashboard.local:8090"


def test_auto_create_config_yaml_from_example(tmp_path: Path):
    """Verify config.yaml is auto-created from config.example.yaml if missing."""
    config_file = tmp_path / "config.yaml"
    example_file = tmp_path / "config.example.yaml"
    example_file.write_text("run_mode: DRY_RUN\n", encoding="utf-8")

    assert not config_file.exists()

    cm = ConfigManager(str(config_file))
    cfg = cm.load()

    assert config_file.exists(), "config.yaml should be auto-created"
    assert cfg.run_mode.name == "DRY_RUN"
