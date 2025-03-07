"""
TRADE - Trading and Market Analysis System

A modular system for market data analysis, trading signal generation,
and performance tracking.
"""

from TRADE.events import EventType, Event, EventEmitter
from TRADE.utils import LoggerSetup, TradingConfig
from TRADE.trading import TradeJournal, ActiveTrade, PerformanceTracker
from TRADE.market import MarketData, MarketMetricsCalculator
from TRADE.analysis import MarketAnalyzer
from TRADE.connectivity import MarketWebSocketManager, MarketSimulator
from TRADE.reporting import MarketStatusReporter
from TRADE.storage import DataStorage

__version__ = '0.2.0'
__all__ = [
    'EventType', 'Event', 'EventEmitter',
    'LoggerSetup', 'TradingConfig',
    'TradeJournal', 'ActiveTrade', 'PerformanceTracker',
    'MarketData', 'MarketMetricsCalculator',
    'MarketAnalyzer',
    'MarketWebSocketManager', 'MarketSimulator',
    'MarketStatusReporter',
    'DataStorage'
]
