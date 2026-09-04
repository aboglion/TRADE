"""
Core enumerations for the trading bot.

All mode, status, and classification enums live here to provide a single
source of truth for the entire system.
"""

from enum import Enum, auto


class RunMode(Enum):
    """Operational mode of the bot."""
    BACKTEST = auto()
    DRY_RUN = auto()
    TESTNET = auto()
    LIVE = auto()


class OrderSide(Enum):
    """Trade direction."""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order execution type."""
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    """Lifecycle status of an order from intent to completion."""
    INTENT = "intent"           # Created locally, not yet submitted
    SUBMITTED = "submitted"     # Sent to exchange, awaiting confirmation
    OPEN = "open"               # Confirmed open on exchange
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"           # Fully executed
    CANCELLED = "cancelled"     # Cancelled (by us or exchange)
    FAILED = "failed"           # Submission failed
    UNKNOWN = "unknown"         # Network error — status uncertain
    EXPIRED = "expired"         # Order expired on exchange


class Regime(Enum):
    """Macro market regime classification from BTC SMA-150."""
    BULL = "bull"
    BEAR = "bear"


class AssetRegime(Enum):
    """Per-asset micro regime classification from indicators."""
    STRONG_BULL_TREND = "STRONG_BULL_TREND"
    TREND = "TREND"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


class MicroRegime(Enum):
    """Micro satellite regime classification."""
    HIGH_CONVICTION_MICRO = "HIGH_CONVICTION_MICRO"
    MICRO_TREND_ACCELERATION = "MICRO_TREND_ACCELERATION"
    MICRO_NEUTRAL = "MICRO_NEUTRAL"


class PositionAction(Enum):
    """What the strategy wants to do with a position."""
    OPEN = auto()
    CLOSE = auto()
    ADD = auto()        # Pyramid add
    REDUCE = auto()     # Partial take-profit
    HOLD = auto()
    NO_ACTION = auto()
