"""
Live market data provider.

Fetches candles from the exchange via the gateway.  Implements
IMarketDataProvider so the candle service doesn't care whether data
comes from CSV files or from the exchange.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from src.core.interfaces import IMarketDataProvider
from src.core.models import Candle

logger = logging.getLogger("bot.data.live")


class LiveDataProvider(IMarketDataProvider):
    """Fetches candles from the exchange via ExchangeGateway."""

    def __init__(self, gateway):
        """
        Args:
            gateway: An ExchangeGateway or DryRunExchange instance.
                     We accept 'any' to support both without import coupling.
        """
        self._gateway = gateway

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        since_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """
        Fetch candles from the live exchange.

        Large requests are automatically paginated via the gateway.
        Returns only CLOSED candles (the last candle is stripped if open).
        """
        logger.debug(
            "Fetching candles: symbol=%s, tf=%s, since=%s, limit=%d",
            symbol, timeframe, since_ms, limit,
        )

        candles = self._gateway.fetch_ohlcv_all(
            symbol=symbol,
            timeframe=timeframe,
            since_ms=since_ms,
            limit=limit,
        )

        # Filter to closed candles only
        closed = [c for c in candles if c.is_closed]

        logger.debug(
            "Fetched %d candles (%d closed) for %s",
            len(candles), len(closed), symbol,
        )

        return closed
