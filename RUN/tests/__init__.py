"""
Shared test fixtures and mock exchange.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.enums import OrderSide, OrderStatus, OrderType
from src.core.models import BotState, Candle, OrderIntent, OrderResult


# ── Candle fixtures ──────────────────────────────────────────

def make_candle(
    ts_ms: int,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 1000.0,
    is_closed: bool = True,
) -> Candle:
    """Create a test candle."""
    return Candle(
        timestamp_ms=ts_ms,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=is_closed,
    )


def make_candle_series(
    start_ts_ms: int,
    count: int,
    tf_ms: int = 14_400_000,  # 4h
    base_price: float = 100.0,
    trend: float = 0.1,       # Price increment per candle
) -> List[Candle]:
    """Create a series of sequential candles with a gentle uptrend."""
    candles = []
    for i in range(count):
        price = base_price + i * trend
        candles.append(make_candle(
            ts_ms=start_ts_ms + i * tf_ms,
            open=price,
            high=price + 2.0,
            low=price - 2.0,
            close=price + 0.5,
            volume=1000.0 + i * 10,
        ))
    return candles


@pytest.fixture
def sample_candles() -> List[Candle]:
    """350 candles for indicator warmup."""
    return make_candle_series(
        start_ts_ms=1_600_000_000_000,
        count=350,
        base_price=50000.0,
        trend=10.0,
    )


@pytest.fixture
def fresh_state() -> BotState:
    """A clean bot state with no history."""
    return BotState()


@pytest.fixture
def sample_order_intent() -> OrderIntent:
    """A sample buy order intent."""
    return OrderIntent(
        client_order_id="test_order_001",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.001,
        price=50000.0,
        reason="Test order",
    )
