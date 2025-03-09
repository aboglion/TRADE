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
            conditions = TradingConfig.MARKET_CONDITIONS

            # Buy entry conditions
            buy_entry = (
                metrics['realized_volatility'] >= conditions['BUY']['volatility_threshold'] and
                metrics['relative_strength'] >= conditions['BUY']['relative_strength_threshold'][0] and 
                metrics['relative_strength'] <= conditions['BUY']['relative_strength_threshold'][1] and
                metrics['trend_strength'] >= conditions['BUY']['trend_strength'] and
                metrics['order_imbalance'] >= conditions['BUY']['order_imbalance'] and
                metrics['market_efficiency_ratio'] >= conditions['BUY']['market_efficiency_ratio']
            )

            if buy_entry:
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
            stop_loss = active_trade_data.get('stop_loss', 0)
            take_profit = active_trade_data.get('take_profit', 0)
            highest_price = active_trade_data.get('highest_price', 0)
            entry_time = active_trade_data.get('entry_time')
            
            # Convert string timestamp to datetime object if needed
            if entry_time:
                if isinstance(entry_time, str):
                    try:
                        entry_time = datetime.fromisoformat(entry_time)
                    except ValueError as e:
                        self.logger.warning(f"Invalid entry_time format: {entry_time}. Using current time. Error: {e}")
                        entry_time = timestamp
            
            # Update highest price for trailing stop calculation
            highest_price = max(highest_price, price)
            
            stop_triggered = False
            reason = None
            
            # Check stop loss
            if price <= stop_loss:
                stop_triggered = True
                reason = 'stop_loss'
                
            # Check take profit
            if price >= take_profit:
                stop_triggered = True
                reason = 'take_profit'
                
            # Calculate current profit percentage
            profit_pct = (price / entry_price - 1)
            
            # Adjust trailing stop if profit exceeds activation threshold
            activation_threshold = TradingConfig.MARKET_CONDITIONS['EXIT']['trailing_stop_act_ivation'] / 100
            if profit_pct >= activation_threshold:
                # Calculate trailing stop level
                trail_distance = TradingConfig.MARKET_CONDITIONS['EXIT']['trailing_stop_distance'] * \
                                metrics['atr'] / highest_price
                trail_level = highest_price * (1 - trail_distance)
                
                # Update stop loss if trailing stop is higher
                if trail_level > stop_loss:
                    stop_loss = trail_level
                    self.logger.debug(f"Trailing stop updated to {trail_level:.6f} (profit: {profit_pct*100:.2f}%)")
                    
            # Check time-based exit
            if entry_time:
                trade_duration = (timestamp - entry_time).total_seconds() / 3600
                if trade_duration > 4:  # Exit after 4 hours
                    stop_triggered = True
                    reason = 'time_exit'
                    
            # Check trend reversal exit
            if metrics['trend_strength'] < -1*(TradingConfig.MARKET_CONDITIONS['BUY']['relative_strength_threshold'][0]):
                stop_triggered = True
                reason = 'trend_reversal'
                
            if stop_triggered:
                return {
                    'action': 'close', 
                    'price': price, 
                    'reason': reason, 
                    'time': timestamp,
                    'profit_pct': profit_pct * 100,
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