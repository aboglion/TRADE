"""
Comprehensive test suite for Binance Tiered Margin Brackets and Decision Pipeline API parity.
"""

import json
from unittest.mock import MagicMock
import pytest

from src.utils.math_utils import clamp_leverage_by_binance_bracket, get_binance_bracket_info


def test_binance_bracket_btc_tiers():
    # BTC Tier 1: <= $50,000 -> Max 20x
    assert clamp_leverage_by_binance_bracket("BTC/USDT", 20.0, 45_000.0) == 20.0
    info1 = get_binance_bracket_info("BTC/USDT", 20.0, 45_000.0)
    assert info1["tier"] == 1
    assert info1["max_allowed_leverage"] == 20.0
    assert info1["is_clamped"] is False

    # BTC Tier 2: <= $250,000 -> Max 10x
    assert clamp_leverage_by_binance_bracket("BTC/USDT", 20.0, 150_000.0) == 10.0
    info2 = get_binance_bracket_info("BTC/USDT", 20.0, 150_000.0)
    assert info2["tier"] == 2
    assert info2["max_allowed_leverage"] == 10.0
    assert info2["clamped_leverage"] == 10.0
    assert info2["is_clamped"] is True

    # BTC Tier 3: <= $1,000,000 -> Max 5x
    assert clamp_leverage_by_binance_bracket("BTC/USDT", 10.0, 500_000.0) == 5.0
    info3 = get_binance_bracket_info("BTC/USDT", 10.0, 500_000.0)
    assert info3["tier"] == 3
    assert info3["max_allowed_leverage"] == 5.0

    # BTC Tier 4: <= $5,000,000 -> Max 3x
    assert clamp_leverage_by_binance_bracket("BTC/USDT", 5.0, 2_500_000.0) == 3.0
    info4 = get_binance_bracket_info("BTC/USDT", 5.0, 2_500_000.0)
    assert info4["tier"] == 4
    assert info4["max_allowed_leverage"] == 3.0

    # BTC Tier 5: > $5,000,000 -> Max 2x
    assert clamp_leverage_by_binance_bracket("BTC/USDT", 5.0, 10_000_000.0) == 2.0
    info5 = get_binance_bracket_info("BTC/USDT", 5.0, 10_000_000.0)
    assert info5["tier"] == 5
    assert info5["max_allowed_leverage"] == 2.0


def test_binance_bracket_eth_tiers():
    # ETH Tier 1: <= $40,000 -> Max 20x
    assert clamp_leverage_by_binance_bracket("ETH/USDT", 20.0, 30_000.0) == 20.0
    # ETH Tier 2: <= $200,000 -> Max 10x
    assert clamp_leverage_by_binance_bracket("ETH/USDT", 20.0, 120_000.0) == 10.0
    # ETH Tier 3: <= $800,000 -> Max 5x
    assert clamp_leverage_by_binance_bracket("ETH/USDT", 20.0, 500_000.0) == 5.0


def test_binance_bracket_alt_tiers():
    # SOL / Alts Tier 1: <= $20,000 -> Max 20x
    assert clamp_leverage_by_binance_bracket("SOL/USDT", 20.0, 15_000.0) == 20.0
    # SOL Tier 2: <= $100,000 -> Max 10x
    assert clamp_leverage_by_binance_bracket("SOL/USDT", 20.0, 50_000.0) == 10.0
    # SOL Tier 3: <= $500,000 -> Max 5x
    assert clamp_leverage_by_binance_bracket("SOL/USDT", 20.0, 200_000.0) == 5.0
    # SOL Tier 4: > $500,000 -> Max 2x
    assert clamp_leverage_by_binance_bracket("SOL/USDT", 10.0, 800_000.0) == 2.0


def test_pipeline_api_includes_binance_bracket_metadata():
    from tests.test_server_logs import DummyHandler

    handler = DummyHandler("/api/strategy/conditions")
    handler.gateway = MagicMock()
    handler.config = MagicMock()
    handler.config.strategy.momentum_cutoff_pct = -0.03
    handler.config.strategy.conviction_leverage = 20.0
    handler.config.strategy.flash_wick_limit = -0.038
    handler.config.strategy.flash_wick_limit_20x = -0.022
    handler.config.strategy.mid_leverage = 5.0
    handler.config.strategy.base_leverage = 2.5
    handler.config.strategy.ladder_steps = [1.0, 2.0, 4.0, 10.0, 20.0]
    handler.config.strategy.safe_spot_weight = 0.20
    handler.config.strategy.safe_cash_weight = 0.70
    handler.config.strategy.bear_short_hedge_weight = 0.45
    handler.config.strategy.short_leverage = 2.0
    handler.state_store = MagicMock()
    handler.state_store.load_state.return_value = MagicMock(strategy_state={}, session_initial_value_usd=2000.0)

    # 200 daily candles for BTC
    candles_1d = []
    base_ts = 1600000000000
    for i in range(200):
        px = 60000.0 + i * 50.0
        candles_1d.append([base_ts + i * 86400000, px, px + 100, px - 100, px, 100.0])

    handler._fetch_ohlcv_safe = MagicMock(return_value=candles_1d)
    handler._handle_strategy_conditions()

    response_bytes = handler.wfile.getvalue()
    data = json.loads(response_bytes.decode("utf-8"))

    assert "macro_regime" in data
    macro = data["macro_regime"]
    assert "binance_tier_bracket" in macro
    bracket = macro["binance_tier_bracket"]
    assert bracket["tier"] == 1
    assert bracket["max_allowed_leverage"] == 20.0
    assert "account_equity" in bracket
    assert "bracket_desc" in bracket
    assert "ladder_steps" in macro
    assert macro["ladder_steps"] == [1.0, 2.0, 4.0, 10.0, 20.0]
