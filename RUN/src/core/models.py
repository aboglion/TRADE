"""
Core domain models.

Immutable data containers used across all layers.  Every model is a
frozen dataclass so it can be hashed, compared, and safely passed
between services without accidental mutation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core.enums import (
    AssetRegime,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionAction,
    Regime,
)

# ── Market Data ──────────────────────────────────────────────

@dataclass(frozen=True)
class Candle:
    """A single OHLCV candle."""
    timestamp_ms: int          # Open time in milliseconds UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True     # False for the current live candle

    @property
    def timestamp_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc)

    @property
    def timestamp_iso(self) -> str:
        return self.timestamp_utc.isoformat()


# ── Portfolio ────────────────────────────────────────────────

@dataclass(frozen=True)
class AssetHolding:
    """Balance state for one asset or futures position."""
    symbol: str                # e.g. "BTC", "ETH", "USDT" (or "BTC/USDT" for positions)
    free: float                # Available for trading
    locked: float              # In open orders
    total: float               # free + locked (can be negative for shorts)
    value_usd: float           # Estimated USD notional value at current price
    
    # Futures specific fields
    unrealized_pnl: float = 0.0
    entry_price: float = 0.0
    leverage: float = 1.0


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Point-in-time view of the entire account."""
    timestamp_ms: int
    holdings: dict[str, AssetHolding]   # symbol → AssetHolding
    total_value_usd: float

    def get_weight(self, symbol: str) -> float:
        """Current allocation weight of *symbol*."""
        if self.total_value_usd <= 0:
            return 0.0
        h = self.holdings.get(symbol)
        if h is None and ("/" in symbol or ":" in symbol):
            clean_base = symbol.split("/")[0].split(":")[0]
            h = self.holdings.get(clean_base)
        if h is None:
            return 0.0
        weight = h.value_usd / self.total_value_usd
        return -weight if h.total < 0 else weight


@dataclass(frozen=True)
class TargetAllocation:
    """Desired portfolio allocation."""
    weights: dict[str, float]           # symbol -> target weight (0.0-1.0)
    regime: Regime
    timestamp_ms: int
    leverage: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Orders ───────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderIntent:
    """
    A trade the strategy *wants* to execute.

    Created by the strategy/portfolio layer, validated by risk manager,
    then handed to the order manager for execution.
    """
    client_order_id: str
    symbol: str                         # e.g. "BTC/USDT"
    side: OrderSide
    order_type: OrderType
    amount: float                       # In base currency units
    price: float | None = None       # Required for LIMIT orders
    estimated_price: float | None = None # Reference price for risk check / market order valuation
    reason: str = ""                    # Human-readable justification
    candle_ts: int | None = None     # Candle that triggered this intent
    reduce_only: bool = False           # Flag for position-reducing / closing orders in futures
    leverage: float = 1.0               # Leverage for futures margin check / exchange sync

    @staticmethod
    def generate_id() -> str:
        """Create a unique client order ID."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"bot_{ts}_{short_uuid}"


@dataclass(frozen=True)
class OrderResult:
    """Result of submitting an order to the exchange."""
    client_order_id: str
    symbol: str = ""
    exchange_order_id: str | None = None
    status: OrderStatus = OrderStatus.UNKNOWN
    filled_amount: float = 0.0
    average_price: float = 0.0
    fees: float = 0.0
    fee_currency: str = ""
    timestamp_ms: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""


# ── Strategy ─────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategySignal:
    """Signal for a single asset from the strategy engine."""
    symbol: str
    action: PositionAction
    asset_regime: AssetRegime
    target_weight: float        # Desired allocation (0.0-1.0)
    reason: str = ""


@dataclass(frozen=True)
class StrategyDecision:
    """Complete strategy output for one cycle."""
    regime: Regime
    target_allocation: TargetAllocation
    signals: list[StrategySignal]
    timestamp_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Rebalancing ──────────────────────────────────────────────

@dataclass(frozen=True)
class RebalancePlan:
    """Minimal set of trades to move from current to target allocation."""
    orders: list[OrderIntent]
    current_snapshot: PortfolioSnapshot
    target_allocation: TargetAllocation
    total_deviation_pct: float          # Sum of absolute weight diffs


# ── Bot State (mutable — the one exception) ─────────────────

@dataclass
class BotState:
    """
    Persistent bot state.  This is the *only* mutable model — it gets
    serialized to disk after every cycle.
    """
    last_processed_candle_ts: dict[str, int] = field(default_factory=dict)  # symbol → ts_ms
    last_regime: str | None = None
    pending_orders: list[dict[str, Any]] = field(default_factory=list)
    completed_orders: list[dict[str, Any]] = field(default_factory=list)
    last_run_ts: int | None = None
    last_cycle_success: bool = True
    critical_errors: list[str] = field(default_factory=list)
    strategy_state: dict[str, Any] = field(default_factory=dict)
    session_initial_value_usd: float | None = None
    session_fees: dict[str, float] = field(default_factory=dict)
    session_initial_prices: dict[str, float] = field(default_factory=dict)
    pnl_history: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_processed_candle_ts": self.last_processed_candle_ts,
            "last_regime": self.last_regime,
            "pending_orders": self.pending_orders,
            "completed_orders": self.completed_orders[-5000:],  # Keep last 5000 trades
            "last_run_ts": self.last_run_ts,
            "last_cycle_success": self.last_cycle_success,
            "critical_errors": self.critical_errors[-50:],
            "strategy_state": self.strategy_state,
            "session_initial_value_usd": self.session_initial_value_usd,
            "session_fees": self.session_fees,
            "session_initial_prices": self.session_initial_prices,
            "pnl_history": self.pnl_history[-5000:],  # Keep last 5000 points
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotState:
        if not isinstance(data, dict):
            return cls()
        return cls(
            version=int(data.get("version") or 1),
            last_processed_candle_ts=data.get("last_processed_candle_ts") or {},
            last_regime=data.get("last_regime"),
            pending_orders=data.get("pending_orders") if data.get("pending_orders") is not None else [],
            completed_orders=data.get("completed_orders") if data.get("completed_orders") is not None else [],
            last_run_ts=data.get("last_run_ts"),
            last_cycle_success=data.get("last_cycle_success", True) if data.get("last_cycle_success") is not None else True,
            critical_errors=data.get("critical_errors") if data.get("critical_errors") is not None else [],
            strategy_state=data.get("strategy_state") if data.get("strategy_state") is not None else {},
            session_initial_value_usd=data.get("session_initial_value_usd"),
            session_fees=data.get("session_fees") if data.get("session_fees") is not None else {},
            session_initial_prices=data.get("session_initial_prices") if data.get("session_initial_prices") is not None else {},
            pnl_history=data.get("pnl_history") if data.get("pnl_history") is not None else [],
        )
