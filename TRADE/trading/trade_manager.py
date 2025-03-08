import logging
import threading
import traceback
from typing import Dict, Optional, Any
from datetime import datetime

from ..events.event import Event
from ..events.event_type import EventType
from ..events.event_emitter import EventEmitter
from ..trading.active_trade import ActiveTrade
from ..trading.performance_tracker import PerformanceTracker
from ..trading.trade_journal import TradeJournal
from ..utils.config import TradingConfig

class TradeManager:
    """Manages trade execution, tracking, and performance analysis"""

    def __init__(self, 
                 event_emitter: EventEmitter,
                 journal_path: str = 'trades/market_analyzer_journal.csv',
                 risk_factor: float = TradingConfig.DEFAULT_RISK_FACTOR,
                 adaptive_sizing: bool = TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING):
        """
        Initialize the trade manager
        
        Args:
            event_emitter: Event emitter for publishing trade events
            journal_path: Path to the trade journal file
            risk_factor: Risk factor for position sizing
            adaptive_sizing: Whether to use adaptive position sizing
        """
        self.event_emitter = event_emitter
        self.active_trade = ActiveTrade()
        self.performance = PerformanceTracker()
        self.trade_journal = TradeJournal(journal_path)
        self.risk_factor = risk_factor
        self.adaptive_sizing = adaptive_sizing
        self.logger = logging.getLogger('MarketAnalyzer.TradeManager')
        self._lock = threading.RLock()  # Thread-safe operation

    def reset(self) -> None:
        """Reset trade manager state"""
        with self._lock:
            self.active_trade.reset()
            self.performance.reset()

    def get_active_trade_data(self) -> Dict[str, Any]:
        """Get current active trade data"""
        with self._lock:
            return self.active_trade.to_dict()

    def is_active_trade(self) -> bool:
        """Check if there is an active trade"""
        with self._lock:
            return self.active_trade.active

    def open_trade(self, price: float, direction: str, timestamp: datetime, metrics: Dict[str, float]) -> None:
        """
        Open a new trade with dynamic risk management
        
        Args:
            price: Entry price
            direction: Trade direction ('buy' or 'sell')
            timestamp: Entry timestamp
            metrics: Current market metrics
        """
        if price <= 0:
            self.logger.error("Invalid price for opening trade")
            return

        try:
            # Calculate position size based on risk parameters
            position_size = self._calculate_position_size(metrics)
            
            # Calculate stop loss and take profit levels
            atr = max(metrics['atr'], price * 0.001)  # Use minimum 0.1% ATR
            stop_distance = 1.5 * atr
            risk_reward = TradingConfig.MARKET_CONDITIONS['EXIT']['profit_target_multiplier']
            profit_distance = stop_distance * risk_reward
            
            # For long trades: stop below entry, target above entry
            stop_loss = price - stop_distance
            take_profit = price + profit_distance
                
            # Update trade details
            self.active_trade.update(
                active=True,
                entry_price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_price=price,
                lowest_price=price,
                direction=direction,
                size=position_size,
                entry_time=timestamp.isoformat()
            )
            
            # Emit trade opened event
            self.event_emitter.emit(Event(
                type=EventType.TRADE_OPENED,
                data={
                    'direction': direction,
                    'entry_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'size': position_size,
                    'time': timestamp,
                    'risk_reward': risk_reward,
                    'metrics': {k: round(v, 4) for k, v in metrics.items()}
                }
            ))
            
            self.logger.info(f"Opened {direction} trade at {price:.6f} (Stop: {stop_loss:.6f}, Target: {take_profit:.6f}, RR: {risk_reward:.1f})")
            
        except Exception as e:
            error_msg = f"Error opening trade: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'open_trade', 'details': traceback.format_exc()}
            ))

    def close_trade(self, exit_price: float, reason: str, timestamp: datetime, metrics: Dict[str, float]) -> None:
        """
        Close an active trade and record trade data
        
        Args:
            exit_price: Exit price
            reason: Reason for closing the trade
            timestamp: Exit timestamp
            metrics: Current market metrics
        """
        if not self.active_trade.active or exit_price <= 0:
            return

        try:
            # Get trade details
            entry_price = self.active_trade.entry_price
            direction = self.active_trade.direction
            position_size = self.active_trade.size
            entry_time = self.active_trade.entry_time
            
            # Calculate profit/loss
            pnl_pct = (exit_price / entry_price - 1) * 100
            pnl_value = position_size * (pnl_pct / 100)
            
            # Convert entry_time from string to datetime if needed
            entry_time_dt = entry_time
            if isinstance(entry_time, str):
                try:
                    entry_time_dt = datetime.fromisoformat(entry_time)
                except ValueError:
                    self.logger.warning(f"Invalid entry_time format: {entry_time}. Using current time.")
                    entry_time_dt = timestamp
                
            # Record trade details
            trade_record = {
                'entry_time': entry_time,
                'exit_time': timestamp,
                'duration': (timestamp - entry_time_dt).total_seconds() / 60 if entry_time_dt else 0,
                'direction': direction,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'size': position_size,
                'pnl': pnl_pct,
                'pnl_value': pnl_value,
                'exit_reason': reason
            }
            
            # Update performance metrics
            self.performance.add_trade(trade_record)
            
            # Add trade to journal with additional metrics
            journal_record = trade_record.copy()
            journal_record['pnl_value'] = pnl_value
            journal_record['market_metrics'] = metrics.copy()
            journal_record['notes'] = (
                f"Exit reason: {reason}. "
                f"Market conditions: Volatility {metrics['realized_volatility']:.2f}%, "
                f"RS: {metrics['relative_strength']:.2f}, "
                f"Trend: {metrics['trend_strength']}"
            )
            
            self.trade_journal.add_trade(journal_record)
            
            # Emit trade closed event
            self.event_emitter.emit(Event(
                type=EventType.TRADE_CLOSED,
                data={
                    'direction': direction,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl_pct,
                    'pnl_value': pnl_value,
                    'reason': reason,
                    'duration': (timestamp - entry_time_dt).total_seconds() / 60 if entry_time_dt else 0,
                    'metrics': {k: round(v, 4) for k, v in metrics.items()}
                }
            ))
            
            # Log trade result
            result_desc = "PROFIT" if pnl_pct > 0 else "LOSS"
            self.logger.info(
                f"Closed {direction} trade: Entry={entry_price:.6f}, Exit={exit_price:.6f}, "
                f"{result_desc}={pnl_pct:.2f}%, Reason={reason}"
            )
            
            # Reset trade
            self.active_trade.reset()
            
        except Exception as e:
            error_msg = f"Error closing trade: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'close_trade', 'details': traceback.format_exc()}
            ))

    def update_stop_loss(self, new_stop_loss: float) -> None:
        """Update the stop loss level for the active trade"""
        if self.active_trade.active and new_stop_loss > 0:
            self.active_trade.stop_loss = new_stop_loss

    def get_performance_metrics(self) -> Dict[str, float]:
        """Get current performance metrics"""
        return self.performance.get_metrics()

    def _calculate_position_size(self, metrics: Dict[str, float]) -> float:
        """
        Calculate position size based on risk parameters and market conditions
        
        Args:
            metrics: Current market metrics
            
        Returns:
            Position size as a decimal (e.g., 0.02 for 2%)
        """
        try:
            base_risk = self.risk_factor
            
            if self.adaptive_sizing:
                # Adjust position size based on market conditions
                
                # Volatility factor - reduce size when volatility is high
                vol = metrics['realized_volatility']
                vol_factor = 1.0 - min(0.5, vol / 50)
                
                # Trend alignment - reduce size when going against the trend
                trend_alignment = 0.0
                trend = metrics['trend_strength']
                # For long trades, reduce size if trend is down
                if trend < 0:
                    trend_alignment = min(0.3, -trend)
                
                # Performance factor - adjust based on recent win rate
                win_rate = self.performance.metrics['win_rate']
                min_trades = 10
                if self.performance.metrics['total_trades'] >= min_trades:
                    perf_factor = min(1.3, win_rate * 1.5)
                else:
                    perf_factor = 1.0
                
                # Calculate adjusted risk
                adjusted_risk = base_risk * vol_factor * (1.0 - trend_alignment) * perf_factor
                
                # Ensure position size is within reasonable bounds
                return min(0.05, max(0.005, adjusted_risk))
            else:
                return base_risk
                
        except Exception as e:
            error_msg = f"Position sizing error: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'calculate_position_size', 'details': traceback.format_exc()}
            ))
            return self.risk_factor  # Fall back to base risk