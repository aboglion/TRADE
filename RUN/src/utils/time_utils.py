"""
Time utilities for candle boundary calculations.

All timestamps are in milliseconds UTC.
The bot operates on 4-hour candles aligned to midnight UTC:
  00:00, 04:00, 08:00, 12:00, 16:00, 20:00
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.core.interfaces import IClock

# Milliseconds per timeframe
TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,     # 4 * 60 * 60 * 1000
    "1d": 86_400_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    """Convert a timeframe string to milliseconds."""
    if timeframe in TIMEFRAME_MS:
        return TIMEFRAME_MS[timeframe]
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def candle_open_ms(timestamp_ms: int, timeframe: str) -> int:
    """
    Align a timestamp to its candle open boundary.

    Example: for 4h candles, 05:30 UTC → 04:00 UTC.
    """
    tf_ms = timeframe_to_ms(timeframe)
    return (timestamp_ms // tf_ms) * tf_ms


def next_candle_open_ms(timestamp_ms: int, timeframe: str) -> int:
    """Timestamp of the next candle open after *timestamp_ms*."""
    tf_ms = timeframe_to_ms(timeframe)
    current_open = candle_open_ms(timestamp_ms, timeframe)
    return current_open + tf_ms


def is_candle_closed(candle_open_ms_val: int, timeframe: str, now_ms: Optional[int] = None) -> bool:
    """Check whether the candle that opened at *candle_open_ms_val* is now closed."""
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tf_ms = timeframe_to_ms(timeframe)
    return now_ms >= candle_open_ms_val + tf_ms


def ms_to_utc(ms: int) -> datetime:
    """Convert milliseconds since epoch to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def utc_to_ms(dt: datetime) -> int:
    """Convert a datetime to milliseconds since epoch."""
    return int(dt.timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    """Convert milliseconds to ISO-8601 string."""
    return ms_to_utc(ms).isoformat()


# ── Concrete clock implementation ────────────────────────────

class SystemClock(IClock):
    """Real system clock."""

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_ms(self) -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def current_candle_open_ms(self, timeframe: str) -> int:
        return candle_open_ms(self.now_ms(), timeframe)

    def is_candle_closed(self, candle_open_ms_val: int, timeframe: str) -> bool:
        return is_candle_closed(candle_open_ms_val, timeframe, self.now_ms())
