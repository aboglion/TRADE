"""
Tests for candle service — gap detection, deduplication, closed candle filtering.
"""

import pytest
from tests import make_candle, make_candle_series
from src.core.exceptions import DataGapError, InsufficientDataError
from src.core.models import Candle
from src.data.candle_service import CandleService


TF_MS = 14_400_000  # 4h


class FakeProvider:
    """Test provider that returns pre-configured candles."""

    def __init__(self, candles):
        self._candles = candles

    def fetch_candles(self, symbol, timeframe, since_ms=None, limit=500):
        result = self._candles
        if since_ms is not None:
            result = [c for c in result if c.timestamp_ms >= since_ms]
        if limit:
            result = result[:limit]
        return result


class TestCandleServiceClosedDetection:
    """Verify that only closed candles are returned."""

    def test_filters_open_candles(self):
        base_ts = 1_700_000_000_000
        candles = [
            make_candle(base_ts, is_closed=True),
            make_candle(base_ts + TF_MS, is_closed=True),
            make_candle(base_ts + 2 * TF_MS, is_closed=False),  # Open
        ]
        provider = FakeProvider(candles)
        service = CandleService(provider, timeframe="4h", warmup_candles=10)

        now_ms = base_ts + 3 * TF_MS
        new = service.get_new_closed_candles("BTC/USDT", None, now_ms)
        assert all(c.is_closed for c in new)


class TestCandleServiceDeduplication:
    """Verify deduplication by timestamp."""

    def test_removes_duplicates(self):
        base_ts = 1_700_000_000_000
        candles = [
            make_candle(base_ts),
            make_candle(base_ts),  # Duplicate
            make_candle(base_ts + TF_MS),
        ]
        provider = FakeProvider(candles)
        service = CandleService(provider, timeframe="4h", warmup_candles=10)

        now_ms = base_ts + 3 * TF_MS
        new = service.get_new_closed_candles("BTC/USDT", None, now_ms)
        timestamps = [c.timestamp_ms for c in new]
        assert len(timestamps) == len(set(timestamps))


class TestCandleServiceContinuity:
    """Verify gap detection in candle sequences."""

    def test_valid_sequence_passes(self):
        candles = make_candle_series(1_700_000_000_000, count=10)
        provider = FakeProvider(candles)
        service = CandleService(provider, timeframe="4h", warmup_candles=5)

        assert service.validate_continuity(candles) is True

    def test_gap_raises_error(self):
        base_ts = 1_700_000_000_000
        candles = [
            make_candle(base_ts),
            make_candle(base_ts + TF_MS),
            # Gap: missing base_ts + 2 * TF_MS
            make_candle(base_ts + 3 * TF_MS),
        ]
        provider = FakeProvider(candles)
        service = CandleService(provider, timeframe="4h", warmup_candles=5)

        with pytest.raises(DataGapError):
            service.validate_continuity(candles)


class TestCandleServiceNoDuplicateProcessing:
    """Verify that already-processed candles are not returned again."""

    def test_skips_already_processed(self):
        base_ts = 1_700_000_000_000
        candles = make_candle_series(base_ts, count=5)
        provider = FakeProvider(candles)
        service = CandleService(provider, timeframe="4h", warmup_candles=5)

        # Process first 3 candles
        last_processed = candles[2].timestamp_ms
        now_ms = base_ts + 10 * TF_MS

        new = service.get_new_closed_candles("BTC/USDT", last_processed, now_ms)
        for c in new:
            assert c.timestamp_ms > last_processed
