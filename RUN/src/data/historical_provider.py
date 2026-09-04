"""
Historical market data provider.

Loads candles from CSV files for BACKTEST mode.  The CSV format matches
the data files in BACK_TEST/data/:

    Date,Open,High,Low,Close,Volume
    2019-10-20 20:00:00,8235.0,8297.0,8165.36,8223.35,6173.520717
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import pandas as pd

from src.core.interfaces import IMarketDataProvider
from src.core.exceptions import InsufficientDataError
from src.core.models import Candle

logger = logging.getLogger("bot.data.historical")


class HistoricalDataProvider(IMarketDataProvider):
    """Loads candles from CSV files."""

    def __init__(self, data_dir: str):
        """
        Args:
            data_dir: Path to directory containing CSV files.
        """
        self._data_dir = data_dir
        self._cache: dict[str, List[Candle]] = {}

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        since_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """
        Load candles from CSV.  Caches on first load.

        The *symbol* is mapped to a filename:
            BTC/USDT → BTC_USD_4h.csv
        """
        if symbol not in self._cache:
            self._load_csv(symbol)

        candles = self._cache.get(symbol, [])

        # Filter by since_ms
        if since_ms is not None:
            candles = [c for c in candles if c.timestamp_ms >= since_ms]

        # Apply limit
        if limit and limit < len(candles):
            candles = candles[-limit:]

        return candles

    def _load_csv(self, symbol: str) -> None:
        """Load and parse a CSV file into Candle objects."""
        # Map symbol to filename: "BTC/USDT" → "BTC_USD_4h.csv"
        base = symbol.split("/")[0] if "/" in symbol else symbol
        filename = f"{base}_USD_4h.csv"
        filepath = os.path.join(self._data_dir, filename)

        if not os.path.exists(filepath):
            raise InsufficientDataError(f"Data file not found: {filepath}")

        logger.info("Loading historical data from %s", filepath)
        df = pd.read_csv(filepath)

        date_col = "observation_date" if "observation_date" in df.columns else "Date"
        df["Date"] = pd.to_datetime(df[date_col])

        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "Volume" in df.columns:
            df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
        else:
            df["Volume"] = 0.0

        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        df = df.sort_values("Date")

        candles: List[Candle] = []
        for _, row in df.iterrows():
            ts_ms = int(row["Date"].timestamp() * 1000)
            candles.append(Candle(
                timestamp_ms=ts_ms,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
                is_closed=True,  # Historical candles are always closed
            ))

        self._cache[symbol] = candles
        logger.info("Loaded %d candles for %s", len(candles), symbol)
