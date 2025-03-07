import logging
import threading
import traceback
from typing import Dict, Optional, List, Any
from datetime import datetime

# Use relative imports
from ..events.event import Event
from ..events.event_type import EventType
from ..events.event_emitter import EventEmitter
from ..market.market_data import MarketData
from ..market.market_metrics import MarketMetricsCalculator
from ..trading.trade_manager import TradeManager
from ..analysis.signal_generator import SignalGenerator
from ..utils.logger import LoggerSetup
from ..utils.config import TradingConfig

class MarketAnalyzer:
    """Market analysis system using statistical methods and order flow analysis"""

    def __init__(self,
                 warmup_ticks: int = TradingConfig.DEFAULT_WARMUP_TICKS,
                 dynamic_window: bool = TradingConfig.DEFAULT_DYNAMIC_WINDOW,
                 risk_factor: float = TradingConfig.DEFAULT_RISK_FACTOR,
                 adaptive_sizing: bool = TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING):
        """
        Initialize the market analyzer
        
        Args:
            warmup_ticks: Number of ticks required before analysis starts
            dynamic_window: Whether to use dynamic window sizing
            risk_factor: Risk factor for position sizing
            adaptive_sizing: Whether to use adaptive position sizing
        """
        self.warmup_ticks = warmup_ticks
        self.dynamic_window = dynamic_window
        self.risk_factor = risk_factor
        self.adaptive_sizing = adaptive_sizing
        
        # Initialize event system
        self.event_emitter = EventEmitter()
        
        # Initialize market data and metrics
        self.market_data = MarketData()
        self.metrics_calculator = MarketMetricsCalculator(self.market_data)
        
        # Initialize trade management
        self.trade_manager = TradeManager(
            self.event_emitter,
            risk_factor=risk_factor,
            adaptive_sizing=adaptive_sizing
        )
        
        # Initialize signal generator
        self.signal_generator = SignalGenerator(self.event_emitter)
        
        # Initialize state variables
        self.warmup_complete = False
        self.last_data_time = datetime.now()
        self.connection_retries = 0
        
        # Initialize logger
        self.logger = LoggerSetup.get_logger()
        
        # Thread safety
        self._lock = threading.RLock()
    
    def reset(self) -> None:
        """Reset analyzer state for backtesting or reinitialization"""
        with self._lock:
            self.market_data.reset()
            self.metrics_calculator.reset()
            self.trade_manager.reset()
            self.warmup_complete = False
            self.last_data_time = datetime.now()
            self.connection_retries = 0

    def process_tick(self, tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process tick data with validation and metric updates
        
        Args:
            tick_data: Dictionary containing tick data
            
        Returns:
            Signal dictionary if a trading signal was generated, None otherwise
        """
        with self._lock:
            try:
                # Validate tick data
                required_fields = ['p', 'q', 'm']  # price, quantity, maker flag
                if not all(field in tick_data for field in required_fields):
                    self.logger.warning(f"Missing required fields in tick data: {tick_data}")
                    return None
                    
                price = float(tick_data['p'])
                volume = float(tick_data['q'])
                is_ask = bool(tick_data['m'])
                
                if 'T' in tick_data:
                    timestamp = datetime.fromtimestamp(int(tick_data['T']) / 1000)
                else:
                    timestamp = datetime.now()
                    
                if price <= 0 or volume <= 0:
                    self.logger.warning(f"Invalid values in tick data: price={price}, volume={volume}")
                    return None
                
                # Emit tick event
                self.event_emitter.emit(Event(
                    type=EventType.TICK,
                    data={'price': price, 'volume': volume, 'is_ask': is_ask}
                ))
                
                # Update market data
                self.market_data.add_tick(price, volume, is_ask, timestamp)
                self.last_data_time = timestamp
                    
                # Check if warmup is complete
                if not self.warmup_complete and self.market_data.has_minimum_data(self.warmup_ticks):
                    self.warmup_complete = True
                    self.logger.info(f"Warmup phase completed with {self.warmup_ticks} ticks")
                    self.event_emitter.emit(Event(
                        type=EventType.STRATEGY_UPDATE,
                        data={'status': 'warmup_complete', 'ticks': self.warmup_ticks}
                    ))
                
                # Update market metrics
                metrics_updated = self.metrics_calculator.calculate_metrics()
                
                if metrics_updated:
                    self.event_emitter.emit(Event(
                        type=EventType.METRIC_UPDATE,
                        data=self.metrics_calculator.metrics
                    ))
                
                # Skip signal generation if warmup is not complete
                if not self.warmup_complete:
                    return None
                
                # Generate trading signals
                active_trade_data = None
                if self.trade_manager.is_active_trade():
                    active_trade_data = self.trade_manager.get_active_trade_data()
                
                signal = self.signal_generator.generate_signal(
                    price=price,
                    timestamp=timestamp,
                    metrics=self.metrics_calculator.metrics,
                    is_active_trade=self.trade_manager.is_active_trade(),
                    active_trade_data=active_trade_data
                )
                
                # Process signal if generated
                if signal:
                    self._process_signal(signal, price, timestamp)
                    self.event_emitter.emit(Event(
                        type=EventType.SIGNAL,
                        data=signal
                    ))
                    
                return signal
                    
            except Exception as e:
                error_msg = f"Error processing tick data: {str(e)}"
                self.logger.error(error_msg)
                self.logger.debug(traceback.format_exc())
                self.event_emitter.emit(Event(
                    type=EventType.ERROR,
                    data={'error': error_msg, 'source': 'process_tick', 'details': traceback.format_exc()}
                ))
                return None
    
    def _process_signal(self, signal: Dict[str, Any], price: float, timestamp: datetime) -> None:
        """
        Process and execute trading signals
        
        Args:
            signal: Signal dictionary
            price: Current price
            timestamp: Current timestamp
        """
        try:
            action = signal.get('action', '').upper()
            
            if not action:
                return
                
            if action == 'BUY':
                self.trade_manager.open_trade(
                    price=price,
                    direction='buy',
                    timestamp=timestamp,
                    metrics=self.metrics_calculator.metrics
                )
            elif action in ('CLOSE', 'SELL'):
                reason = signal.get('reason', 'unknown')
                
                # Update stop loss if provided in the signal
                if 'updated_stop_loss' in signal:
                    self.trade_manager.update_stop_loss(signal['updated_stop_loss'])
                    
                self.trade_manager.close_trade(
                    exit_price=price,
                    reason=reason,
                    timestamp=timestamp,
                    metrics=self.metrics_calculator.metrics
                )
                
        except Exception as e:
            error_msg = f"Error processing signal: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'process_signal', 'details': traceback.format_exc()}
            ))

    def get_market_state(self) -> Dict[str, Any]:
        """
        Get current market state and analyzer metrics
        
        Returns:
            Dictionary containing current market state
        """
        with self._lock:
            current_price = self.market_data.get_current_price()
            
            # Get active trade data
            active_trade_data = self.trade_manager.get_active_trade_data()
            
            # Build state dictionary
            state = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'current_price': current_price,
                'metrics': self.metrics_calculator.metrics.copy(),
                'ActiveTrade': {
                    'active': active_trade_data['active'],
                    'direction': active_trade_data['direction'],
                    'entry_price': active_trade_data['entry_price'],
                    'current_pnl': 0.0,
                    'stop_loss': active_trade_data['stop_loss'],
                    'take_profit': active_trade_data['take_profit']
                },
                'performance': self.trade_manager.get_performance_metrics()
            }
            
            # Calculate current P&L if there's an active trade
            if active_trade_data['active'] and current_price > 0:
                state['ActiveTrade']['current_pnl'] = (current_price / active_trade_data['entry_price'] - 1) * 100
                    
            return state