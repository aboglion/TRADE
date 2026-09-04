"""
Shared test fixtures and mock exchange.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import make_candle, make_candle_series
from src.core.models import BotState


@pytest.fixture
def sample_candles():
    """350 candles for indicator warmup."""
    return make_candle_series(
        start_ts_ms=1_600_000_000_000,
        count=350,
        base_price=50000.0,
        trend=10.0,
    )


@pytest.fixture
def fresh_state():
    """A clean bot state with no history."""
    return BotState()
