"""
Custom exception hierarchy.

Every exception carries enough context for the caller to decide
whether to retry, halt, or enter safe-stop mode.
"""


class BotError(Exception):
    """Base exception for all bot errors."""
    pass


# ── Exchange / Network ───────────────────────────────────────

class ExchangeConnectionError(BotError):
    """Cannot reach the exchange (network down, DNS failure, etc.)."""
    pass


class ExchangeRateLimitError(BotError):
    """Rate limit exceeded — must wait before retrying."""
    pass


class ExchangeAuthError(BotError):
    """API key/secret invalid or insufficient permissions."""
    pass


class ExchangeNotAvailableError(BotError):
    """Exchange is in maintenance or temporarily unavailable."""
    pass


# ── Data ─────────────────────────────────────────────────────

class DataGapError(BotError):
    """Missing or non-contiguous candle data detected."""
    pass


class InsufficientDataError(BotError):
    """Not enough historical candles for indicator warmup."""
    pass


# ── Orders ───────────────────────────────────────────────────

class InsufficientBalanceError(BotError):
    """Not enough free balance to place the order."""
    pass


class DuplicateOrderError(BotError):
    """An order with the same client ID already exists."""
    pass


class UnknownOrderStateError(BotError):
    """Order was submitted but status cannot be confirmed."""
    pass


class InvalidOrderError(BotError):
    """Order parameters violate exchange constraints (min size, precision)."""
    pass


class OrderRejectedByRisk(BotError):
    """Risk manager blocked the order."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Order rejected by risk manager: {reason}")


# ── Reconciliation ───────────────────────────────────────────

class ReconciliationError(BotError):
    """Local state and exchange state are inconsistent."""
    pass


# ── Risk ─────────────────────────────────────────────────────

class RiskLimitExceeded(BotError):
    """A risk limit was breached."""
    pass


class KillSwitchActiveError(BotError):
    """The kill switch is engaged — no trading allowed."""
    pass


# ── General ──────────────────────────────────────────────────

class SafeStopRequired(BotError):
    """Bot must stop immediately and preserve state."""
    pass


class ConfigError(BotError):
    """Configuration is invalid or missing required fields."""
    pass


class StateCorruptionError(BotError):
    """Persisted state file is corrupt or unreadable."""
    pass
