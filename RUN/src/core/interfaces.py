"""
Abstract interfaces (contracts) for all pluggable components.

Each layer depends only on these ABCs, never on concrete implementations.
This enables easy swapping between live, dry-run, and test backends.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.core.enums import Regime
from src.core.models import (
    BotState,
    Candle,
    OrderIntent,
    OrderResult,
    PortfolioSnapshot,
    StrategyDecision,
    TargetAllocation,
)


class IMarketDataProvider(ABC):
    """Fetches OHLCV candle data from any source."""

    @abstractmethod
    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        since_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """
        Fetch candles for *symbol* at *timeframe* granularity.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT".
            timeframe: Candle interval, e.g. "4h".
            since_ms: Start time in milliseconds UTC.  If None, fetch latest.
            limit: Maximum number of candles to return.

        Returns:
            Chronologically sorted list of Candle objects.
        """
        ...


class IPortfolioProvider(ABC):
    """Reads current account holdings."""

    @abstractmethod
    def get_portfolio(self, prices: Dict[str, float]) -> PortfolioSnapshot:
        """
        Build a point-in-time portfolio snapshot.

        Args:
            prices: Current prices for valuation, e.g. {"BTC": 60000.0}.

        Returns:
            PortfolioSnapshot with all holdings and USD valuations.
        """
        ...


class IOrderExecutor(ABC):
    """Submits and manages orders on the exchange."""

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit an order.  Must be idempotent on *client_order_id*."""
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Cancel an open order."""
        ...

    @abstractmethod
    def get_order_status(self, symbol: str, order_id: str) -> OrderResult:
        """Check current status of an order."""
        ...

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """List all open orders, optionally filtered by symbol."""
        ...


class IStateStore(ABC):
    """Persistent state storage."""

    @abstractmethod
    def load_state(self) -> BotState:
        """Load the last saved bot state, or return a fresh default."""
        ...

    @abstractmethod
    def save_state(self, state: BotState) -> None:
        """Atomically persist bot state."""
        ...


class IClock(ABC):
    """Time abstraction for testability."""

    @abstractmethod
    def now_utc(self) -> datetime:
        """Current UTC time."""
        ...

    @abstractmethod
    def now_ms(self) -> int:
        """Current UTC time in milliseconds."""
        ...

    @abstractmethod
    def current_candle_open_ms(self, timeframe: str) -> int:
        """Timestamp (ms) of the open of the current candle."""
        ...

    @abstractmethod
    def is_candle_closed(self, candle_open_ms: int, timeframe: str) -> bool:
        """Whether the candle that opened at *candle_open_ms* is now closed."""
        ...


class IRiskManager(ABC):
    """Pre-trade risk gating."""

    @abstractmethod
    def approve_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
    ) -> Tuple[bool, str]:
        """
        Evaluate whether *intent* is safe to execute.

        Returns:
            (approved, reason) — reason is empty string if approved.
        """
        ...

    @abstractmethod
    def is_kill_switch_active(self) -> bool:
        """Check if the global kill switch is engaged."""
        ...


class IStrategy(ABC):
    """Computes trading signals from market data and portfolio state."""

    @abstractmethod
    def compute_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        portfolio: PortfolioSnapshot,
    ) -> StrategyDecision:
        """
        Given historical candles per asset and current portfolio,
        produce a trading decision.

        The strategy must NOT access the exchange or modify state.
        """
        ...
