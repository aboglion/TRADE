"""
Candle service — orchestrates candle fetching, validation, and gap detection.

Responsibilities:
- Track which candles have been processed (via last_processed_candle_ts)
- Fetch only new closed candles since last processing
- Validate continuity (no gaps in the 4H sequence)
- Deduplicate by timestamp
- Fetch warmup history on cold start
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.core.exceptions import DataGapError, InsufficientDataError
from src.core.interfaces import IMarketDataProvider
from src.core.models import Candle
from src.utils.time_utils import candle_open_ms, is_candle_closed, timeframe_to_ms

logger = logging.getLogger("bot.data.candle_service")


class CandleService:
    """
    High-level candle management.

    Wraps a data provider (live or historical) and adds validation,
    gap detection, and state tracking.
    """

    def __init__(
        self,
        provider: IMarketDataProvider,
        timeframe: str = "4h",
        warmup_candles: int = 300,
    ):
        self._provider = provider
        self._timeframe = timeframe
        self._warmup_candles = warmup_candles
        self._tf_ms = timeframe_to_ms(timeframe)

    def get_new_closed_candles(
        self,
        symbol: str,
        last_processed_ts: Optional[int],
        now_ms: int,
    ) -> List[Candle]:
        """
        Fetch closed candles that haven't been processed yet.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT".
            last_processed_ts: Timestamp (ms) of last processed candle open.
                               None on first run.
            now_ms: Current time in milliseconds UTC.

        Returns:
            List of new closed candles, chronologically sorted.
            Empty if no new closed candles exist.
        """
        if last_processed_ts is not None:
            # Fetch from the candle AFTER the last processed one
            since_ms = last_processed_ts + self._tf_ms
        else:
            # Cold start — we need to know what's available
            # Fetch last few candles to see where we are
            since_ms = now_ms - (self._tf_ms * 10)

        candles = self._provider.fetch_candles(
            symbol=symbol,
            timeframe=self._timeframe,
            since_ms=since_ms,
            limit=100,
        )

        # Filter: only closed candles that are after last_processed_ts
        new_closed: List[Candle] = []
        for c in candles:
            if not c.is_closed:
                continue
            if not is_candle_closed(c.timestamp_ms, self._timeframe, now_ms):
                continue
            if last_processed_ts is not None and c.timestamp_ms <= last_processed_ts:
                continue
            new_closed.append(c)

        # Deduplicate and sort
        new_closed = self._deduplicate(new_closed)

        if new_closed:
            logger.info(
                "Found %d new closed candle(s) for %s [%s → %s]",
                len(new_closed),
                symbol,
                new_closed[0].timestamp_iso,
                new_closed[-1].timestamp_iso,
            )

        return new_closed

    def get_full_history(
        self,
        symbol: str,
        up_to_ts: int,
        min_candles: Optional[int] = None,
    ) -> List[Candle]:
        """
        Fetch enough historical candles for indicator computation.

        Args:
            symbol: Trading pair.
            up_to_ts: The latest candle timestamp to include (ms).
            min_candles: Minimum number of candles needed.  Defaults to
                         warmup_candles.

        Returns:
            Chronologically sorted list of closed candles.

        Raises:
            InsufficientDataError: If not enough candles are available.
        """
        needed = min_candles or self._warmup_candles
        since_ms = up_to_ts - (self._tf_ms * (needed + 50))  # Extra buffer

        candles = self._provider.fetch_candles(
            symbol=symbol,
            timeframe=self._timeframe,
            since_ms=since_ms,
            limit=needed + 50,
        )

        # Filter to closed candles up to the target
        closed = [
            c for c in candles
            if c.is_closed and c.timestamp_ms <= up_to_ts
        ]
        closed = self._deduplicate(closed)

        if len(closed) < needed:
            raise InsufficientDataError(
                f"Need {needed} candles for {symbol}, got {len(closed)}"
            )

        return closed

    def validate_continuity(self, candles: List[Candle]) -> bool:
        """
        Check that candles form a continuous sequence with no gaps.

        Returns True if valid, raises DataGapError if gaps detected.
        """
        if len(candles) < 2:
            return True

        for i in range(1, len(candles)):
            expected_ts = candles[i - 1].timestamp_ms + self._tf_ms
            actual_ts = candles[i].timestamp_ms

            if actual_ts != expected_ts:
                gap_hours = (actual_ts - expected_ts) / 3_600_000
                logger.error(
                    "Candle gap detected between %s and %s (%.1f hours)",
                    candles[i - 1].timestamp_iso,
                    candles[i].timestamp_iso,
                    gap_hours,
                )
                raise DataGapError(
                    f"Gap of {gap_hours:.1f}h between "
                    f"{candles[i-1].timestamp_iso} and {candles[i].timestamp_iso}"
                )

        return True

    def _deduplicate(self, candles: List[Candle]) -> List[Candle]:
        """Remove duplicate candles by timestamp, keep first occurrence."""
        seen: set = set()
        unique: List[Candle] = []
        for c in candles:
            if c.timestamp_ms not in seen:
                seen.add(c.timestamp_ms)
                unique.append(c)
        return sorted(unique, key=lambda c: c.timestamp_ms)
