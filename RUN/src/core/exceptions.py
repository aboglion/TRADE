"""
Custom exception hierarchy.

Every exception carries enough context for the caller to decide
whether to retry, halt, or enter safe-stop mode.
"""


class BotError(Exception):
    """Base exception for all bot errors."""


# ── Exchange / Network ───────────────────────────────────────

class ExchangeConnectionError(BotError):
    """Cannot reach the exchange (network down, DNS failure, etc.)."""


class ExchangeRateLimitError(BotError):
    """Rate limit exceeded — must wait before retrying."""


class ExchangeAuthError(BotError):
    """API key/secret invalid or insufficient permissions."""


class ExchangeNotAvailableError(BotError):
    """Exchange is in maintenance or temporarily unavailable."""


# ── Data ─────────────────────────────────────────────────────

class DataGapError(BotError):
    """Missing or non-contiguous candle data detected."""


class InsufficientDataError(BotError):
    """Not enough historical candles for indicator warmup."""


# ── Orders ───────────────────────────────────────────────────

class InsufficientBalanceError(BotError):
    """Not enough free balance to place the order."""


class DuplicateOrderError(BotError):
    """An order with the same client ID already exists."""


class UnknownOrderStateError(BotError):
    """Order was submitted but status cannot be confirmed."""


class InvalidOrderError(BotError):
    """Order parameters violate exchange constraints (min size, precision)."""


class OrderRejectedByRisk(BotError):
    """Risk manager blocked the order."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Order rejected by risk manager: {reason}")


# ── Reconciliation ───────────────────────────────────────────

class ReconciliationError(BotError):
    """Local state and exchange state are inconsistent."""


# ── Risk ─────────────────────────────────────────────────────

class RiskLimitExceeded(BotError):
    """A risk limit was breached."""


class KillSwitchActiveError(BotError):
    """The kill switch is engaged — no trading allowed."""


# ── General ──────────────────────────────────────────────────

class SafeStopRequired(BotError):
    """Bot must stop immediately and preserve state."""


class ConfigError(BotError):
    """Configuration is invalid or missing required fields."""


class StateCorruptionError(BotError):
    """Persisted state file is corrupt or unreadable."""
