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
from typing import Any, Dict, List, Optional

from src.core.enums import (
    AssetRegime,
    MicroRegime,
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
    """Balance state for one asset."""
    symbol: str                # e.g. "BTC", "ETH", "USDT"
    free: float                # Available for trading
    locked: float              # In open orders
    total: float               # free + locked
    value_usd: float           # Estimated USD value at current price


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Point-in-time view of the entire account."""
    timestamp_ms: int
    holdings: Dict[str, AssetHolding]   # symbol → AssetHolding
    total_value_usd: float

    def get_weight(self, symbol: str) -> float:
        """Current allocation weight of *symbol*."""
        if self.total_value_usd <= 0:
            return 0.0
        h = self.holdings.get(symbol)
        if h is None:
            return 0.0
        return h.value_usd / self.total_value_usd


@dataclass(frozen=True)
class TargetAllocation:
    """Desired portfolio allocation."""
    weights: Dict[str, float]           # symbol → target weight (0.0–1.0)
    regime: Regime
    timestamp_ms: int


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
    price: Optional[float] = None       # Required for LIMIT orders
    reason: str = ""                    # Human-readable justification
    candle_ts: Optional[int] = None     # Candle that triggered this intent

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
    exchange_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.UNKNOWN
    filled_amount: float = 0.0
    average_price: float = 0.0
    fees: float = 0.0
    fee_currency: str = ""
    timestamp_ms: int = 0
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error_message: str = ""


# ── Strategy ─────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategySignal:
    """Signal for a single asset from the strategy engine."""
    symbol: str
    action: PositionAction
    asset_regime: AssetRegime
    target_weight: float        # Desired allocation (0.0–1.0)
    reason: str = ""


@dataclass(frozen=True)
class StrategyDecision:
    """Complete strategy output for one cycle."""
    regime: Regime
    target_allocation: TargetAllocation
    signals: List[StrategySignal]
    timestamp_ms: int
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Rebalancing ──────────────────────────────────────────────

@dataclass(frozen=True)
class RebalancePlan:
    """Minimal set of trades to move from current to target allocation."""
    orders: List[OrderIntent]
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
    last_processed_candle_ts: Dict[str, int] = field(default_factory=dict)  # symbol → ts_ms
    last_regime: Optional[str] = None
    pending_orders: List[Dict[str, Any]] = field(default_factory=list)
    completed_orders: List[Dict[str, Any]] = field(default_factory=list)
    last_run_ts: Optional[int] = None
    last_cycle_success: bool = True
    critical_errors: List[str] = field(default_factory=list)
    strategy_state: Dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "last_processed_candle_ts": self.last_processed_candle_ts,
            "last_regime": self.last_regime,
            "pending_orders": self.pending_orders,
            "completed_orders": self.completed_orders[-100:],  # Keep last 100
            "last_run_ts": self.last_run_ts,
            "last_cycle_success": self.last_cycle_success,
            "critical_errors": self.critical_errors[-50:],
            "strategy_state": self.strategy_state,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotState":
        return cls(
            version=data.get("version", 1),
            last_processed_candle_ts=data.get("last_processed_candle_ts", {}),
            last_regime=data.get("last_regime"),
            pending_orders=data.get("pending_orders", []),
            completed_orders=data.get("completed_orders", []),
            last_run_ts=data.get("last_run_ts"),
            last_cycle_success=data.get("last_cycle_success", True),
            critical_errors=data.get("critical_errors", []),
            strategy_state=data.get("strategy_state", {}),
        )
