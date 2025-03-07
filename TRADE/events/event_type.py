from enum import Enum

class EventType(Enum):
    TICK = "tick"
    SIGNAL = "signal"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    STRATEGY_UPDATE = "strategy_update"
    METRIC_UPDATE = "metric_update"
    ERROR = "error"
    CONNECTION = "connection"