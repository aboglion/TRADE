import logging
import traceback
from typing import Dict, Optional, Any
from datetime import datetime

from ..events.event import Event
from ..events.event_type import EventType
from ..events.event_emitter import EventEmitter
from ..utils.config import TradingConfig

class SignalGenerator:
    """Signal generator for market analysis and trading decisions"""

    def __init__(self, event_emitter: EventEmitter):
        """
        Initialize the signal generator
        
        Args:
            event_emitter: Event emitter for publishing signals and events
        """
        self.event_emitter = event_emitter
        self.logger = logging.getLogger('MarketAnalyzer.SignalGenerator')

    def generate_signal(self, 
                        price: float, 
                        timestamp: datetime, 
                        metrics: Dict[str, float], 
                        is_active_trade: bool,
                        active_trade_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        Generate trading signals based on market conditions
        
        Args:
            price: Current market price
            timestamp: Current timestamp
            metrics: Dictionary of market metrics
            is_active_trade: Whether there is an active trade
            active_trade_data: Active trade data if there is an active trade
            
        Returns:
            Signal dictionary or None if no signal
        """
        if is_active_trade:
            return self._check_exit_conditions(price, timestamp, metrics, active_trade_data)
        else:
            return self._check_entry_conditions(price, timestamp, metrics)

    def _check_entry_conditions(self, price: float, timestamp: datetime, metrics: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Check for entry conditions based on market metrics"""
        try:

            if TradingConfig.check_buy_conditions(metrics):
                return {
                    'action': 'BUY', 
                    'side': 'buy', 
                    'price': price, 
                    'time': timestamp,
                    'metrics': {k: round(v, 4) for k, v in metrics.items()}
                }
                
        except Exception as e:
            error_msg = f"Signal generation error: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'signal_generator', 'details': traceback.format_exc()}
            ))
            
        return None

    def _check_exit_conditions(self, price: float, timestamp: datetime, 
                              metrics: Dict[str, float], 
                              active_trade_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for exit conditions for an active trade"""
        if not active_trade_data:
            return None
            
        try:
            entry_price = active_trade_data.get('entry_price', 0)
            highest_price = max(active_trade_data.get('highest_price', 0),price)
            # Convert string timestamp to datetime object if needed
            entry_time = active_trade_data.get('entry_time')
            if entry_time:
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.fromisoformat(entry_time)
                    except ValueError as e:
                        self.logger.warning(f"Invalid entry_time format: {entry_time}. Using current time. Error: {e}")
                        entry_time = timestamp
            
            stop_triggered, reason,stop_loss,profit = TradingConfig.check_sell_conditions(entry_time, entry_price, highest_price, price, timestamp, metrics, active_trade_data)
                
            if stop_triggered:
                return {
                    'action': 'close', 
                    'price': price, 
                    'reason': reason, 
                    'time': timestamp,
                    'profit_pct': profit * 100,
                    'updated_stop_loss': stop_loss
                }
                
        except Exception as e:
            error_msg = f"Error checking exit conditions: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'check_exit_conditions', 'details': traceback.format_exc()}
            ))
            
        return None