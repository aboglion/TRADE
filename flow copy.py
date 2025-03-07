import asyncio
import json
import logging
import numpy as np
import pandas as pd
from scipy import stats
from collections import deque
from typing import Dict, Optional, List, Tuple, Callable, Any, Union, TypeVar, cast
import threading
import time
import websocket
from datetime import datetime, timedelta
import os
import csv
import uuid
import requests
from dataclasses import dataclass, field, asdict
from enum import Enum
import traceback
from contextlib import contextmanager

# Define type variables for better type hinting
T = TypeVar('T')

# ===== מערכת אירועים =====
class EventType(Enum):
    TICK = "tick"
    SIGNAL = "signal"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    STRATEGY_UPDATE = "strategy_update"
    METRIC_UPDATE = "metric_update"
    ERROR = "error"
    CONNECTION = "connection"

@dataclass
class Event:
    type: EventType
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary format for serialization"""
        return {
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp.isoformat()
        }

class EventEmitter:
    def __init__(self):
        self.listeners: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._lock = threading.RLock()  # Thread-safe operation
        
    def on(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """רישום מאזין לאירוע"""
        with self._lock:
            if event_type not in self.listeners:
                self.listeners[event_type] = []
            self.listeners[event_type].append(callback)
        
    def emit(self, event: Event) -> None:
        """הפצת אירוע לכל המאזינים הרשומים"""
        with self._lock:
            listeners = self.listeners.get(event.type, []).copy()
            
        for callback in listeners:
            try:
                callback(event)
            except Exception as e:
                logging.error(f"Error in event listener for {event.type}: {str(e)}")
                logging.debug(traceback.format_exc())

    def remove(self, event_type: EventType, callback: Callable[[Event], None]) -> bool:
        """הסרת מאזין מרשימת המאזינים"""
        with self._lock:
            if event_type in self.listeners and callback in self.listeners[event_type]:
                self.listeners[event_type].remove(callback)
                return True
        return False

# ===== יומן עסקאות =====
class TradeJournal:
    def __init__(self, file_path: str = 'trade_journal.csv'):
        self.file_path = file_path
        self.trades: List[Dict[str, Any]] = []
        self._lock = threading.RLock()  # Thread-safe file operations
        self.create_journal_file()
        self._load_existing_trades()
        
    def create_journal_file(self) -> None:
        """יצירת קובץ יומן עם כותרות במידה והוא לא קיים"""
        with self._lock:
            if not os.path.exists(self.file_path):
                os.makedirs(os.path.dirname(os.path.abspath(self.file_path)), exist_ok=True)
                with open(self.file_path, 'w', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        'trade_id', 'entry_time', 'exit_time', 'duration_minutes', 
                        'direction', 'entry_price', 'exit_price', 'ActiveTrade_size',
                        'pnl_percent', 'pnl_value', 'exit_reason', 'market_volatility',
                        'market_trend', 'relative_strength', 'order_imbalance',
                        'notes'
                    ])
    
    def _load_existing_trades(self) -> None:
        """טעינת עסקאות קיימות מהקובץ"""
        try:
            if os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 0:
                with open(self.file_path, 'r', newline='', encoding='utf-8') as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        try:
                            # Convert string dates to datetime objects
                            if row['entry_time']:
                                row['entry_time'] = datetime.fromisoformat(row['entry_time'])
                            if row['exit_time']:
                                row['exit_time'] = datetime.fromisoformat(row['exit_time'])
                                
                            # Convert numeric fields
                            for numeric_field in ['duration_minutes', 'entry_price', 'exit_price', 
                                                 'ActiveTrade_size', 'pnl_percent', 'pnl_value',
                                                 'market_volatility', 'market_trend', 
                                                 'relative_strength', 'order_imbalance']:
                                if row[numeric_field]:
                                    row[numeric_field] = float(row[numeric_field])
                                    
                            self.trades.append(row)
                        except (ValueError, KeyError) as e:
                            logging.warning(f"Error loading trade from journal: {e}. Row: {row}")
        except Exception as e:
            logging.error(f"Error loading existing trades: {e}")
            logging.debug(traceback.format_exc())
    
    def add_trade(self, trade_data: Dict[str, Any]) -> str:
        """הוספת עסקה ליומן ושמירה לקובץ CSV"""
        trade_id = str(uuid.uuid4())[:8]  # מזהה ייחודי לעסקה
        trade_data['trade_id'] = trade_id
        
        # Ensure market_metrics is a dictionary
        if 'market_metrics' not in trade_data:
            trade_data['market_metrics'] = {}
            
        with self._lock:
            self.trades.append(trade_data)
            
            # כתיבה לקובץ CSV
            try:
                with open(self.file_path, 'a', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        trade_id,
                        trade_data.get('entry_time', '').isoformat() if isinstance(trade_data.get('entry_time'), datetime) else trade_data.get('entry_time', ''),
                        trade_data.get('exit_time', '').isoformat() if isinstance(trade_data.get('exit_time'), datetime) else trade_data.get('exit_time', ''),
                        trade_data.get('duration', 0),
                        trade_data.get('direction', ''),
                        trade_data.get('entry_price', 0),
                        trade_data.get('exit_price', 0),
                        trade_data.get('size', 0),
                        trade_data.get('pnl', 0),
                        trade_data.get('pnl_value', 0),
                        trade_data.get('exit_reason', ''),
                        trade_data.get('market_metrics', {}).get('realized_volatility', 0),
                        trade_data.get('market_metrics', {}).get('trend_strength', 0),
                        trade_data.get('market_metrics', {}).get('relative_strength', 0),
                        trade_data.get('market_metrics', {}).get('order_imbalance', 0),
                        trade_data.get('notes', '')
                    ])
            except Exception as e:
                logging.error(f"Error writing trade to journal: {e}")
                logging.debug(traceback.format_exc())
        
        return trade_id
    
    def get_trades(self, days: Optional[int] = None) -> List[Dict[str, Any]]:
        """שליפת עסקאות, עם אפשרות לסינון לפי מספר ימים"""
        with self._lock:
            if not days:
                return self.trades.copy()
                
            cutoff_date = datetime.now() - timedelta(days=days)
            return [trade for trade in self.trades 
                    if isinstance(trade.get('entry_time'), datetime) and trade['entry_time'] >= cutoff_date]
    
    def analyze_performance(self, days: Optional[int] = None) -> Dict[str, Any]:
        """ניתוח ביצועי המסחר"""
        trades = self.get_trades(days)
        
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "average_pnl": 0,
                "best_trade": 0,
                "worst_trade": 0
            }
        
        try:
            win_trades = [t for t in trades if t.get('pnl', 0) > 0]
            loss_trades = [t for t in trades if t.get('pnl', 0) <= 0]
            
            win_rate = len(win_trades) / len(trades) if trades else 0
            
            total_profit = sum(t.get('pnl', 0) for t in win_trades)
            total_loss = abs(sum(t.get('pnl', 0) for t in loss_trades))
            profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
            
            average_pnl = sum(t.get('pnl', 0) for t in trades) / len(trades) if trades else 0
            
            best_trade = max([t.get('pnl', 0) for t in trades]) if trades else 0
            worst_trade = min([t.get('pnl', 0) for t in trades]) if trades else 0
            
            return {
                "total_trades": len(trades),
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "average_pnl": average_pnl,
                "best_trade": best_trade,
                "worst_trade": worst_trade
            }
        except Exception as e:
            logging.error(f"Error analyzing performance: {e}")
            logging.debug(traceback.format_exc())
            return {
                "total_trades": len(trades),
                "win_rate": 0,
                "profit_factor": 0,
                "average_pnl": 0,
                "best_trade": 0,
                "worst_trade": 0,
                "error": str(e)
            }
    
    def get_trade_distribution(self, days: Optional[int] = None) -> Dict[str, Dict[Union[str, int], int]]:
        """קבלת התפלגות עסקאות לפי שעה, יום וכו'"""
        trades = self.get_trades(days)
        
        if not trades:
            return {"hourly": {}, "daily": {}, "duration": {}}
        
        hourly: Dict[int, int] = {i: 0 for i in range(24)}
        daily: Dict[str, int] = {day: 0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
        duration_bins: Dict[str, int] = {"0-30min": 0, "30-60min": 0, "1-4hrs": 0, "4-12hrs": 0, "12+hrs": 0}
        
        try:
            for trade in trades:
                if isinstance(trade.get('entry_time'), datetime):
                    hour = trade['entry_time'].hour
                    hourly[hour] += 1
                    day = trade['entry_time'].strftime("%A")
                    daily[day] += 1
                    duration = trade.get('duration', 0)
                    if duration < 30:
                        duration_bins["0-30min"] += 1
                    elif duration < 60:
                        duration_bins["30-60min"] += 1
                    elif duration < 240:
                        duration_bins["1-4hrs"] += 1
                    elif duration < 720:
                        duration_bins["4-12hrs"] += 1
                    else:
                        duration_bins["12+hrs"] += 1
            
            return {
                "hourly": hourly,
                "daily": daily,
                "duration": duration_bins
            }
        except Exception as e:
            logging.error(f"Error getting trade distribution: {e}")
            logging.debug(traceback.format_exc())
            return {"hourly": {}, "daily": {}, "duration": {}, "error": str(e)}


# ===== הגדרת לוגינג =====
class LoggerSetup:
    @staticmethod
    def get_logger(name: str = 'MarketAnalyzer') -> logging.Logger:
        """Configure and return a logger with file and console handlers"""
        logger = logging.getLogger(name)
        
        # Only configure if handlers don't exist
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s|%(levelname)s|%(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            
            # Create logs directory if it doesn't exist
            os.makedirs('logs', exist_ok=True)
            
            # Use a timestamped log file
            timestamp = datetime.now().strftime('%Y%m%d')
            file_handler = logging.FileHandler(f'logs/market_trading_{timestamp}.log')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
        return logger

# ===== הגדרות מסחר =====
class TradingConfig:
    DEFAULT_WARMUP_TICKS = 300
    DEFAULT_RISK_FACTOR = 0.02  # סיכון של 2% לעסקה
    DEFAULT_DYNAMIC_WINDOW = True
    DEFAULT_ADAPTIVE_ActiveTrade_SIZING = True
    
    MARKET_CONDITIONS = {
        'BUY': {
            'volatility_threshold': 0.3,     # כניסה בקניה כאשר התנודות גבוהות
            'relative_strength_threshold': 0.2,    # כניסה בקניה כאשר RS גבוה
            'trend_strength': 6,                # חזקה כאשר המחיר עולה 5 פעמים רצופות
            'order_imbalance': 0.25,   # כניסה בקניה כאשר יחס הזמנות גבוה
            'market_efficiency_ratio': 1.0,      # כניסה בקניה כאשר השוק יעיל
        },
        'EXIT': {
            'profit_target_multiplier': 2.5,     # יעד רווח ביחס לסיכון
            'trailing_stop_act_ivation': 1.0,     # הפעלת trailing stop כאשר הרווח מגיע אחוז מסוים
            'trailing_stop_distance': 1.5
        }
    }
    
    @classmethod
    def load_from_file(cls, filename: str = 'trading_config.json') -> bool:
        """Load configuration from a JSON file"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as file:
                    config = json.load(file)
                    
                    # Update default settings if they exist in the file
                    if 'DEFAULT_WARMUP_TICKS' in config:
                        cls.DEFAULT_WARMUP_TICKS = config['DEFAULT_WARMUP_TICKS']
                    if 'DEFAULT_RISK_FACTOR' in config:
                        cls.DEFAULT_RISK_FACTOR = config['DEFAULT_RISK_FACTOR']
                    if 'DEFAULT_DYNAMIC_WINDOW' in config:
                        cls.DEFAULT_DYNAMIC_WINDOW = config['DEFAULT_DYNAMIC_WINDOW']
                    if 'DEFAULT_ADAPTIVE_ActiveTrade_SIZING' in config:
                        cls.DEFAULT_ADAPTIVE_ActiveTrade_SIZING = config['DEFAULT_ADAPTIVE_ActiveTrade_SIZING']
                    
                    # Update market conditions
                    if 'MARKET_CONDITIONS' in config:
                        for key, value in config['MARKET_CONDITIONS'].items():
                            if key in cls.MARKET_CONDITIONS:
                                cls.MARKET_CONDITIONS[key].update(value)
                                
                    return True
            return False
        except Exception as e:
            logging.error(f"Error loading trading config: {e}")
            logging.debug(traceback.format_exc())
            return False
    
    @classmethod
    def save_to_file(cls, filename: str = 'trading_config.json') -> bool:
        """Save current configuration to a JSON file"""
        try:
            config = {
                'DEFAULT_WARMUP_TICKS': cls.DEFAULT_WARMUP_TICKS,
                'DEFAULT_RISK_FACTOR': cls.DEFAULT_RISK_FACTOR,
                'DEFAULT_DYNAMIC_WINDOW': cls.DEFAULT_DYNAMIC_WINDOW,
                'DEFAULT_ADAPTIVE_ActiveTrade_SIZING': cls.DEFAULT_ADAPTIVE_ActiveTrade_SIZING,
                'MARKET_CONDITIONS': cls.MARKET_CONDITIONS
            }
            
            with open(filename, 'w', encoding='utf-8') as file:
                json.dump(config, file, indent=2)
            return True
        except Exception as e:
            logging.error(f"Error saving trading config: {e}")
            logging.debug(traceback.format_exc())
            return False

# ===== ניהול עמדות =====
@dataclass
class ActiveTrade:
    active: bool = False
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = field(default=float('inf'))
    direction: Optional[str] = None  # נשתמש ב-"buy" לעמדות long בלבד
    size: float = 0.0
    entry_time: Optional[datetime] = None
    
    def reset(self) -> None:
        """Reset ActiveTrade to default values"""
        self.active = False
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.highest_price = 0.0
        self.lowest_price = float('inf')
        self.direction = None
        self.size = 0.0
        self.entry_time = None
    
    def update(self, **kwargs: Any) -> None:
        """Update ActiveTrade attributes"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert ActiveTrade to dictionary format"""
        return {
            'active': self.active,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'highest_price': self.highest_price,
            'lowest_price': self.lowest_price,
            'direction': self.direction,
            'size': self.size,
            'entry_time': self.entry_time.isoformat() if self.entry_time else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActiveTrade':
        """Create ActiveTrade from dictionary data"""
        ActiveTrade = cls()
        for key, value in data.items():
            if key == 'entry_time' and value:
                ActiveTrade.entry_time = datetime.fromisoformat(value)
            elif hasattr(ActiveTrade, key):
                setattr(ActiveTrade, key, value)
        return ActiveTrade

# ===== מעקב ביצועים =====
class PerformanceTracker:
    def __init__(self):
        self.trade_history: List[Dict[str, Any]] = []
        self.metrics: Dict[str, float] = {
            'total_trades': 0,
            'winning_trades': 0,
            'win_rate': 0.0,
            'profit_factor': 1.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'max_drawdown': 0.0,
            'sharpe_ratio': 0.0
        }
        self._lock = threading.RLock()  # Thread-safe operation
    
    def add_trade(self, trade: Dict[str, Any]) -> None:
        """Add a completed trade to history and update metrics"""
        with self._lock:
            self.trade_history.append(trade)
            self._update_metrics()
    
    def _update_metrics(self) -> None:
        """Update performance metrics based on trade history"""
        if not self.trade_history:
            return
            
        with self._lock:
            try:
                self.metrics['total_trades'] = len(self.trade_history)
                self.metrics['winning_trades'] = sum(1 for trade in self.trade_history if trade.get('pnl', 0) > 0)
                
                if self.metrics['total_trades'] > 0:
                    self.metrics['win_rate'] = self.metrics['winning_trades'] / self.metrics['total_trades']
                
                # Calculate average profits and losses
                profits = [trade.get('pnl', 0) for trade in self.trade_history if trade.get('pnl', 0) > 0]
                losses = [abs(trade.get('pnl', 0)) for trade in self.trade_history if trade.get('pnl', 0) < 0]
                
                self.metrics['avg_win'] = np.mean(profits) if profits else 0
                self.metrics['avg_loss'] = np.mean(losses) if losses else 0
                
                # Calculate profit factor (total profit / total loss)
                total_profit = sum(profits)
                total_loss = sum(losses)
                self.metrics['profit_factor'] = total_profit / total_loss if total_loss else float('inf')
                
                # Calculate drawdown
                equity_curve = np.cumsum([trade.get('pnl_value', 0) for trade in self.trade_history])
                if len(equity_curve) > 0:
                    peak = np.maximum.accumulate(equity_curve)
                    drawdowns = peak - equity_curve
                    self.metrics['max_drawdown'] = np.max(drawdowns) if len(drawdowns) > 0 else 0
                
                # Calculate Sharpe ratio
                returns = np.array([trade.get('pnl', 0) for trade in self.trade_history])
                if len(returns) > 1:
                    mean_return = np.mean(returns)
                    std_return = np.std(returns, ddof=1)
                    self.metrics['sharpe_ratio'] = (mean_return / std_return) * np.sqrt(252) if std_return > 0 else 0
                
            except Exception as e:
                logging.error(f"Error updating performance metrics: {str(e)}")
                logging.debug(traceback.format_exc())
    
    def get_metrics(self) -> Dict[str, float]:
        """Get current performance metrics"""
        with self._lock:
            return self.metrics.copy()
    
    def reset(self) -> None:
        """Reset performance tracker"""
        with self._lock:
            self.trade_history = []
            self.metrics = {
                'total_trades': 0,
                'winning_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 1.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'max_drawdown': 0.0,
                'sharpe_ratio': 0.0
            }
    
    def save_history(self, filename: str = 'performance_history.json') -> bool:
        """Save trade history to a file"""
        with self._lock:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
                
                # Convert datetime objects to strings
                def json_serialize(obj):
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    return str(obj)
                
                with open(filename, 'w', encoding='utf-8') as file:
                    json.dump({
                        'metrics': self.metrics,
                        'trade_history': self.trade_history
                    }, file, default=json_serialize, indent=2)
                return True
            except Exception as e:
                logging.error(f"Error saving performance history: {str(e)}")
                logging.debug(traceback.format_exc())
                return False

# ===== מחלקת נתוני שוק =====
class MarketData:
    def __init__(self, max_size: int = 1000):
        self.price_history: deque = deque(maxlen=max_size)
        self.volume_history: deque = deque(maxlen=max_size)
        self.bid_volume: deque = deque(maxlen=1000)
        self.ask_volume: deque = deque(maxlen=1000)
        self.time_stamps: deque = deque(maxlen=max_size)
        self.high_prices: deque = deque(maxlen=1000)  # עבור חישוב ATR
        self.low_prices: deque = deque(maxlen=1000)   # עבור חישוב ATR
        self.round_num: int = 0
        self._lock = threading.RLock()  # Thread-safe operation

    def add_tick(self, price: float, volume: float, is_ask: bool, timestamp: datetime) -> bool:
            """הוספת נתוני tick עם עיגול נכון ועדכון מעקב"""
            with self._lock:
                if self.round_num == 0 :
                    # Determine rounding precision based on the price magnitude
                    price_str = str(price)
                    if '.' in price_str:
                        self.round_num = 6 - len(price_str.split(".")[0])
                        self.round_num = max(1, min(8, self.round_num))  # Keep between 1-8 decimal places
                    else:
                        self.round_num = 2
                    self.prev_price = round(price, self.round_num)
                
                price = round(price, self.round_num)
                
                # Check if price change is significant
                self.price_history.append(price)
                self.volume_history.append(volume)
                self.time_stamps.append(timestamp)
                
                # Update high and low prices for ATR calculation
                if not self.high_prices or price > self.high_prices[-1]:
                    self.high_prices.append(price)
                else:
                    self.high_prices.append(self.high_prices[-1])
                    
                if not self.low_prices or price < self.low_prices[-1]:
                    self.low_prices.append(price)
                else:
                    self.low_prices.append(self.low_prices[-1])
                
                # Update volume data
                if is_ask:
                    self.ask_volume.append(volume)
                else:
                    self.bid_volume.append(volume)
                    
            

        
    def get_current_price(self) -> float:
        """Get the most recent price"""
        with self._lock:
            return self.price_history[-1] if self.price_history else 0
    
    def get_price_array(self) -> np.ndarray:
        """Get price history as numpy array"""
        with self._lock:
            return np.array(self.price_history)
    
    def get_volume_array(self) -> np.ndarray:
        """Get volume history as numpy array"""
        with self._lock:
            return np.array(self.volume_history)
        
    def has_minimum_data(self, min_ticks: int) -> bool:
        """Check if we have enough data for analysis"""
        with self._lock:
            return len(self.price_history) >= min_ticks
    
    def reset(self) -> None:
        """Reset all market data"""
        with self._lock:
            self.price_history.clear()
            self.volume_history.clear()
            self.bid_volume.clear()
            self.ask_volume.clear()
            self.time_stamps.clear()
            self.high_prices.clear()
            self.low_prices.clear()
            self.prev_price = 0
            self.round_num = 0
    
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert market data to pandas DataFrame"""
        with self._lock:
            df = pd.DataFrame({
                'timestamp': list(self.time_stamps),
                'price': list(self.price_history),
                'volume': list(self.volume_history)
            })
            return df

# ===== מחשב מדדי שוק =====
class MarketMetricsCalculator:
    def __init__(self, market_data: MarketData):
        self.market_data = market_data
        self.metrics: Dict[str, float] = {
            'realized_volatility': 0.0,
            'atr': 0.0,
            'relative_strength': 0.5,
            'order_imbalance': 0.5,
            'trend_strength': 0.0,
            'market_efficiency_ratio': 0.0,
        }
        self._lock = threading.RLock()  # Thread-safe operation
    
    def calculate_metrics(self) -> bool:
        """חישוב כל מדדי השוק באמצעות מתודות סטטיסטיות"""
        with self._lock:
            if not self.market_data.has_minimum_data(20):
                return False
            
            try:
                # Get price and volume data as numpy arrays for efficient computation
                prices = self.market_data.get_price_array()
                volumes = self.market_data.get_volume_array()
                
                if len(prices) < 2:
                    return False
                
                # Verify data quality
                if np.isnan(prices).any() or np.isnan(volumes).any():
                    logging.warning("NaN values detected in price or volume data")
                    return False
                    
                # Calculate returns and volatility
                returns = np.diff(prices) / prices[:-1]
                realized_volatility = np.std(returns, ddof=1) * np.sqrt(252 * 1440) * 100
                
                # Calculate other metrics
                atr = self._calculate_atr(prices)
                relative_strength = self._calculate_relative_strength(returns)
                order_imbalance = self._calculate_order_imbalance()
                trend_strength = self._calculate_trend_strength(prices)
                mer = self._calculate_market_efficiency_ratio(prices)
    
                # Update metrics dictionary
                self.metrics.update({
                    'realized_volatility': float(realized_volatility),#==>  אחוז שינוי המחיר הממוצע באחוזים
                    'atr': float(atr), #==> מודד תנודות השוק
                    'relative_strength': float(relative_strength), #==> כוח המטבע
                    'order_imbalance': float(order_imbalance), #==> יחס בין bid ל-ask
                    'trend_strength': float(trend_strength), #==> עוצמת מגמה
                    'market_efficiency_ratio': float(mer)   #==> יחס יעילות השוק   חישוב התזוזה הנטו - השינוי המוחלט בין המחיר הנוכחי למחיר לפני 30 נקודות זמן
                })
                
                return True
                
            except Exception as e:
                logging.error(f"Failed to calculate market metrics: {str(e)}")
                logging.debug(traceback.format_exc())
                return False
            
    def _calculate_atr(self, prices: np.ndarray) -> float:
        """חישוב ממוצע טווח אמת (ATR)"""
        atr = 0.0
        try:
            with self._lock:
                if len(self.market_data.high_prices) >= 14 and len(self.market_data.low_prices) >= 14:
                    highs = np.array(list(self.market_data.high_prices)[-14:])
                    lows = np.array(list(self.market_data.low_prices)[-14:])
                    
                    # Get closing prices for ATR calculation
                    len_prices = 150
                    if len(prices) >= len_prices:
                        closes = prices[-len_prices:-1]
                        if len(closes) >= len_prices-1:
                            # Calculate true ranges
                            tr1 = highs - lows
                            tr2 = np.abs(highs - closes[:len_prices])
                            tr3 = np.abs(lows - closes[:len_prices])
                            true_ranges = np.maximum(tr1, np.maximum(tr2, tr3))
                            atr = np.mean(true_ranges)
                        else:
                            atr = self.metrics['realized_volatility'] * prices[-1] / 100
                    else:
                        atr = self.metrics['realized_volatility'] * prices[-1] / 100
                else:
                    atr = self.metrics['realized_volatility'] * prices[-1] / 100
                    
            return atr
        except Exception as e:
            logging.debug(f"ATR calculation error: {e}")
            return self.metrics['realized_volatility'] * prices[-1] / 100 if len(prices) > 0 else 0.0
        
    def _calculate_relative_strength(self, returns: np.ndarray) -> float:
        """חישוב Relative Strength (כדומה ל-RSI)"""
        try:
            window = min(500, len(returns))
            if window < 2:
                return 0.5
                
            # Get returns for the selected window
            window_returns = returns[-window:]
            
            # Calculate gains and losses
            gains = np.sum(np.where(window_returns > 0, window_returns, 0))
            losses = np.sum(np.where(window_returns < 0, -window_returns, 0))
            
            # Calculate RS
            if gains + losses == 0:
                return 0.5
            else:
                return float(gains / (gains + losses))
        except Exception as e:
            logging.debug(f"RS calculation error: {e}")
            return 0.5
    
    def _calculate_order_imbalance(self) -> float:
        """חישוב יחס בין bid ל-ask"""
        try:
            with self._lock:
                bid_vol = np.sum(self.market_data.bid_volume) if self.market_data.bid_volume else 0
                ask_vol = np.sum(self.market_data.ask_volume) if self.market_data.ask_volume else 0
                
                if bid_vol + ask_vol == 0:
                    return 0.5
                else:
                    return float(bid_vol / (bid_vol + ask_vol))
        except Exception as e:
            logging.debug(f"Order imbalance calculation error: {e}")
            return 0.5
    
    def _calculate_trend_strength(self, prices: np.ndarray) -> float:
        """חישוב עוצמת מגמה באמצעות רגרסיה לינארית"""
        try:
            if len(prices) < 30:
                return 0.0
                
            # Use last 30 prices for trend calculation
            window_prices = prices[-30:]
            x = np.arange(len(window_prices))
            
            if len(x) >= 2:
                # Calculate linear regression
                slope, _, r_value, _, _ = stats.linregress(x, window_prices)
                # Scale slope by r-squared and price level
                trend_strength = slope * r_value**2 * (30 / np.mean(window_prices))
                return float(trend_strength)
            else:
                return 0.0
        except Exception as e:
            logging.debug(f"Trend strength calculation error: {e}")
            return 0.0
    
    def _calculate_market_efficiency_ratio(self, prices: np.ndarray) -> float:
        """חישוב יחס יעילות השוק (MER)"""
        try:
            if len(prices) >= 30:
                # Net directional movement
                net_movement = abs(prices[-1] - prices[-30])
                # Total price path length
                path_length = np.sum(np.abs(np.diff(prices[-30:])))
                # Calculate MER
                return float(net_movement / path_length) if path_length > 0 else 0.5
            else:
                return 0.5
        except Exception as e:
            logging.debug(f"MER calculation error: {e}")
            return 0.5
    
    def reset(self) -> None:
        """איפוס מדדים לערכי ברירת מחדל"""
        with self._lock:
            self.metrics = {
                'realized_volatility': 0.0,
                'atr': 0.0,
                'relative_strength': 0.5,
                'order_imbalance': 0.5,
                'trend_strength': 0.0,
                'market_efficiency_ratio': 0.0,
            }

# ===== המנתח הראשי =====
class MarketAnalyzer:
    """מערכת ניתוח שוק באמצעות מתודות סטטיסטיות וניתוח זרימת הזמנות"""

    def __init__(self,
                 warmup_ticks: int = TradingConfig.DEFAULT_WARMUP_TICKS,
                 dynamic_window: bool = TradingConfig.DEFAULT_DYNAMIC_WINDOW,
                 risk_factor: float = TradingConfig.DEFAULT_RISK_FACTOR,
                 adaptive_ActiveTrade: bool = TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING):
        
        self.warmup_ticks = warmup_ticks
        self.dynamic_window = dynamic_window
        self.risk_factor = risk_factor
        self.adaptive_ActiveTrade = adaptive_ActiveTrade
        
        self.market_data = MarketData()
        self.metrics_calculator = MarketMetricsCalculator(self.market_data)
        self.ActiveTrade = ActiveTrade()
        self.performance = PerformanceTracker()
        self.trade_journal = TradeJournal('trades/market_analyzer_journal.csv')
        
        self.warmup_complete = False
        self.last_data_time = datetime.now()
        self.connection_retries = 0
        
        self.event_emitter = EventEmitter()
        self.logger = LoggerSetup.get_logger()
        self._lock = threading.RLock()  # Thread-safe operation
    
    def reset(self) -> None:
        """איפוס מצב המנתח לבקטסטינג או אתחול מחדש"""
        with self._lock:
            self.market_data.reset()
            self.metrics_calculator.reset()
            self.ActiveTrade.reset()
            self.performance.reset()
            self.warmup_complete = False
            self.last_data_time = datetime.now()
            self.connection_retries = 0

    def process_tick(self, tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """עיבוד נתוני tick עם בדיקת תקינות ועדכון מדדים"""
        with self._lock:
            try:
                required_fields = ['p', 'q', 'm']  # מחיר, כמות, דגל מסמן
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
                
                self.event_emitter.emit(Event(
                    type=EventType.TICK,
                    data={'price': price, 'volume': volume, 'is_ask': is_ask}
                ))
                self.market_data.add_tick(price, volume, is_ask, timestamp) 
                # print(f"price: {price}, volume: {volume}, is_ask: {is_ask}, timestamp: {timestamp}")
                # Update market data

                self.last_data_time = timestamp
                    
                if not self.warmup_complete and self.market_data.has_minimum_data(self.warmup_ticks):
                    self.warmup_complete = True
                    self.logger.info(f"Warmup phase completed with {self.warmup_ticks} ticks")
                    self.event_emitter.emit(Event(
                        type=EventType.STRATEGY_UPDATE,
                        data={'status': 'warmup_complete', 'ticks': self.warmup_ticks}
                    ))
                    
                metrics_updated = self.metrics_calculator.calculate_metrics()
                
                if metrics_updated:
                    self.event_emitter.emit(Event(
                        type=EventType.METRIC_UPDATE,
                        data=self.metrics_calculator.metrics
                    ))
                    
                signal = self._signal_generator(price, timestamp)
                
                if signal:
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
    
    def _signal_generator(self, price: float, timestamp: datetime) -> Optional[Dict[str, Any]]:
        """יצירת אות מסחר על בסיס תנאי שוק סטטיסטיים"""
        if not self.warmup_complete:
            return None

        try:
            if self.ActiveTrade.active:
                return self._check_exit_conditions(price, timestamp)
            
            metrics = self.metrics_calculator.metrics
            conditions = TradingConfig.MARKET_CONDITIONS

            # Available metrics:
                    # realized_volatility
                    # atr
                    # relative_strength
                    # order_imbalance
                    # market_efficiency_ratio
                    # trend_strength

            # Buy entry conditions
            buy_entry = (
                metrics['realized_volatility'] >= conditions['BUY']['volatility_threshold'] and
                metrics['relative_strength'] >= conditions['BUY']['relative_strength_threshold'] and 
                metrics['trend_strength'] >= conditions['BUY']['trend_strength'] and
                metrics['order_imbalance'] >= conditions['BUY']['order_imbalance'] and
                metrics['market_efficiency_ratio'] >= conditions['BUY']['market_efficiency_ratio']
            )

            if buy_entry:
                self._action(price, 'buy', timestamp)
                exit(0)
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

    def _check_exit_conditions(self, price: float, timestamp: datetime) -> Optional[Dict[str, Any]]:

        if not self.ActiveTrade.active:
            return None
            
        try:
            # לעמדת Long מעדכנים את המחיר הגבוה ביותר
            self.ActiveTrade.highest_price = max(self.ActiveTrade.highest_price, price)
            
            stop_triggered = False
            reason = None
            
            # Check stop loss
            if price <= self.ActiveTrade.stop_loss:
                stop_triggered = True
                reason = 'stop_loss'
                
            # Check take profit
            if price >= self.ActiveTrade.take_profit:
                stop_triggered = True
                reason = 'take_profit'
                
            # Calculate current profit percentage
            profit_pct = (price / self.ActiveTrade.entry_price - 1)
            
            # Adjust trailing stop if profit exceeds activation threshold
            activation_threshold = TradingConfig.MARKET_CONDITIONS['EXIT']['trailing_stop_act_ivation'] / 100
            if profit_pct >= activation_threshold:
                # Calculate trailing stop level
                trail_distance = TradingConfig.MARKET_CONDITIONS['EXIT']['trailing_stop_distance'] * \
                                 self.metrics_calculator.metrics['atr'] / self.ActiveTrade.highest_price
                trail_level = self.ActiveTrade.highest_price * (1 - trail_distance)
                
                # Update stop loss if trailing stop is higher
                if trail_level > self.ActiveTrade.stop_loss:
                    self.ActiveTrade.stop_loss = trail_level
                    self.logger.debug(f"Trailing stop updated to {trail_level:.6f} (profit: {profit_pct*100:.2f}%)")
                    
            # Check time-based exit
            if self.ActiveTrade.entry_time:
                ActiveTrade_duration = (timestamp - self.ActiveTrade.entry_time).total_seconds() / 3600
                if ActiveTrade_duration > 4:  # Exit after 4 hours
                    stop_triggered = True
                    reason = 'time_exit'
                    
            # Check trend reversal exit
            if self.metrics_calculator.metrics['trend_strength'] < -0.4:
                stop_triggered = True
                reason = 'trend_reversal'
                
            if stop_triggered:
                self._close_ActiveTrade(price, reason, timestamp)
                return {
                    'action': 'close', 
                    'price': price, 
                    'reason': reason, 
                    'time': timestamp,
                    'profit_pct': profit_pct * 100
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

    def _calculate_ActiveTrade_size(self) -> float:
        """חישוב גודל העמדה בהתאם לפרמטרי סיכון ותנאי שוק"""
        if not self.warmup_complete:
            return 0.0
        try:
            base_risk = self.risk_factor
            
            if self.adaptive_ActiveTrade:
                # Adjust ActiveTrade size based on market conditions
                
                # Volatility factor - reduce size when volatility is high
                vol = self.metrics_calculator.metrics['realized_volatility']
                vol_factor = 1.0 - min(0.5, vol / 50)
                
                # Trend alignment - reduce size when going against the trend
                trend_alignment = 0.0
                trend = self.metrics_calculator.metrics['trend_strength']
                # For long ActiveTrades, reduce size if trend is down
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
                
                # Ensure ActiveTrade size is within reasonable bounds
                return min(0.05, max(0.005, adjusted_risk))
            else:
                return base_risk
                
        except Exception as e:
            error_msg = f"ActiveTrade sizing error: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'calculate_ActiveTrade_size', 'details': traceback.format_exc()}
            ))
            return self.risk_factor  # Fall back to base risk

    def _action(self, price: float, direction: str, timestamp: datetime) -> None:
        """פתיחת עמדה עם ניהול סיכונים דינמי – עבור Long בלבד"""
        if price <= 0:
            self.logger.error("Invalid price for opening ActiveTrade")
            return

        try:
            # Calculate ActiveTrade size based on risk parameters
            ActiveTrade_size = self._calculate_ActiveTrade_size()
            
            # Calculate stop loss and take profit levels
            atr = max(self.metrics_calculator.metrics['atr'], price * 0.001)  # Use minimum 0.1% ATR
            stop_distance = 1.5 * atr
            risk_reward = TradingConfig.MARKET_CONDITIONS['EXIT']['profit_target_multiplier']
            profit_distance = stop_distance * risk_reward
            
            # For long ActiveTrades: stop below entry, target above entry
            stop_loss = price - stop_distance
            take_profit = price + profit_distance
                
            # Update ActiveTrade details
            self.ActiveTrade.update(
                active=True,
                entry_price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_price=price,
                lowest_price=price,
                direction=direction,
                size=ActiveTrade_size,
                entry_time=timestamp
            )
            
            # Emit trade opened event
            self.event_emitter.emit(Event(
                type=EventType.TRADE_OPENED,
                data={
                    'direction': direction,
                    'entry_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'size': ActiveTrade_size,
                    'time': timestamp,
                    'risk_reward': risk_reward,
                    'metrics': {k: round(v, 4) for k, v in self.metrics_calculator.metrics.items()}
                }
            ))
            
            self.logger.info(f"Opened {direction} ActiveTrade at {price:.6f} (Stop: {stop_loss:.6f}, Target: {take_profit:.6f}, RR: {risk_reward:.1f})")
            
        except Exception as e:
            error_msg = f"Error opening ActiveTrade: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'open_ActiveTrade', 'details': traceback.format_exc()}
            ))

    def _close_ActiveTrade(self, exit_price: float, reason: str, timestamp: datetime) -> None:
        """סגירת עמדה ורישום נתוני העסקה – עבור Long בלבד"""
        if not self.ActiveTrade.active or exit_price <= 0:
            return

        try:
            # Get ActiveTrade details
            entry_price = self.ActiveTrade.entry_price
            direction = self.ActiveTrade.direction
            ActiveTrade_size = self.ActiveTrade.size
            entry_time = self.ActiveTrade.entry_time
            
            # Calculate profit/loss
            pnl_pct = (exit_price / entry_price - 1) * 100
            pnl_value = ActiveTrade_size * (pnl_pct / 100)
                
            # Record trade details
            trade_record = {
                'entry_time': entry_time,
                'exit_time': timestamp,
                'duration': (timestamp - entry_time).total_seconds() / 60 if entry_time else 0,
                'direction': direction,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'size': ActiveTrade_size,
                'pnl': pnl_pct,
                'pnl_value': pnl_value,
                'exit_reason': reason
            }
            
            # Update performance metrics
            self.performance.add_trade(trade_record)
            
            # Add trade to journal with additional metrics
            journal_record = trade_record.copy()
            journal_record['pnl_value'] = pnl_value
            journal_record['market_metrics'] = self.metrics_calculator.metrics.copy()
            journal_record['notes'] = (
                f"Exit reason: {reason}. "
                f"Market conditions: Volatility {self.metrics_calculator.metrics['realized_volatility']:.2f}%, "
                f"RS: {self.metrics_calculator.metrics['relative_strength']:.2f}, "
                f"Trend: {self.metrics_calculator.metrics['trend_strength']}"
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
                    'duration': (timestamp - entry_time).total_seconds() / 60 if entry_time else 0,
                    'metrics': {k: round(v, 4) for k, v in self.metrics_calculator.metrics.items()}
                }
            ))
            
            # Log trade result
            result_desc = "PROFIT" if pnl_pct > 0 else "LOSS"
            self.logger.info(
                f"Closed {direction} ActiveTrade: Entry={entry_price:.6f}, Exit={exit_price:.6f}, "
                f"{result_desc}={pnl_pct:.2f}%, Reason={reason}"
            )
            
            # Reset ActiveTrade
            self.ActiveTrade.reset()
            
        except Exception as e:
            error_msg = f"Error closing ActiveTrade: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            self.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'close_ActiveTrade', 'details': traceback.format_exc()}
            ))

    def get_market_state(self) -> Dict[str, Any]:
        """קבלת מצב השוק הנוכחי ומדדי המנתח"""
        with self._lock:
            current_price = self.market_data.get_current_price()
            
            state = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'current_price': current_price,
                'metrics': self.metrics_calculator.metrics.copy(),
                'ActiveTrade': {
                    'active': self.ActiveTrade.active,
                    'direction': self.ActiveTrade.direction,
                    'entry_price': self.ActiveTrade.entry_price,
                    'current_pnl': 0.0,
                    'stop_loss': self.ActiveTrade.stop_loss,
                    'take_profit': self.ActiveTrade.take_profit
                },
                'performance': self.performance.get_metrics()
            }
            
            if self.ActiveTrade.active and current_price > 0:
                state['ActiveTrade']['current_pnl'] = (current_price / self.ActiveTrade.entry_price - 1) * 100
                    
            return state

# ===== ניהול חיבורי WebSocket =====
class MarketWebSocketManager:
    """ניהול חיבורי WebSocket לזרימת נתוני שוק"""

    def __init__(self, analyzer: MarketAnalyzer, symbols: List[str] = ['btcusdt']):
        self.analyzer = analyzer
        self.symbols = symbols
        self.ws_connections: Dict[str, websocket.WebSocketApp] = {}
        self.active = True
        self.connection_check_interval = 30  # שניות
        self.last_connection_check = time.time()
        self._lock = threading.RLock()  # Thread-safe operation
        self._reconnect_delays: Dict[str, float] = {}

    def _create_connection(self, symbol: str) -> websocket.WebSocketApp:
        """Create WebSocket connection with proper handlers"""
        url = f"wss://stream.binance.com:9443/ws/{symbol}@trade"
        
        def on_message(ws, message):
            self._handle_message(ws, message)
            
        def on_error(ws, error):
            self._handle_error(ws, error)
            
        def on_close(ws, close_status_code, close_msg):
            self._handle_close(ws, close_status_code, close_msg)
            
        def on_open(ws):
            self._handle_open(ws)
            
        return websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

    def _handle_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            
            # Normalize the data to use expected field names
            normalized_data = {
                'p': float(data.get('p', 0)),       # price
                'q': float(data.get('q', 0)),       # quantity
                'm': bool(data.get('m', False)),    # is buyer maker
                'T': int(data.get('T', 0))          # timestamp
            }
            # Process tick and check for trading signals
            signal = self.analyzer.process_tick(normalized_data)
            
            if signal:
                symbol = data.get('s', 'UNKNOWN').upper()
                self._execute_signal(signal, symbol)
                
        except json.JSONDecodeError:
            self.analyzer.logger.error("Invalid JSON message received")
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': "Invalid JSON message", 'source': 'websocket'}
            ))
        except Exception as e:
            error_msg = f"Error handling message: {str(e)}"
            self.analyzer.logger.error(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'websocket', 'details': traceback.format_exc()}
            ))

    def _execute_signal(self, signal: Dict[str, Any], symbol: str) -> None:
        """Execute trading signal and log details"""
        try:
            action = signal.get('action', '').upper()
            
            if not action:
                return
                
            details = ""
            if action == 'BUY':
                details = f"{signal.get('side', '').upper()} @ {signal['price']:.6f}"
            elif action in ('CLOSE', 'close', 'SELL'):
                details = f"@ {signal['price']:.6f} ({signal.get('reason', 'unknown')})"
                
            # Get current market state
            market_state = self.analyzer.get_market_state()
            metrics = market_state['metrics']
            
            # Prepare formatted log message
            log_msg = (
                f"\n{' MARKET SIGNAL ':=^60}\n"
                f"| {action} {details} |\n"
                f"| Symbol: {symbol} | Vol: {metrics['realized_volatility']:.2f}% | RS: {metrics['relative_strength']:.2f} |\n"
                f"| Order Imbalance: {metrics['order_imbalance']:.2f} | Trend: {metrics['trend_strength']:.2f} |\n"
                f"{'':=^60}"
            )
            
            print(log_msg)
            self.analyzer.logger.info(f"Signal: {action} {details} on {symbol}")
            
        except Exception as e:
            error_msg = f"Error executing signal: {str(e)}"
            self.analyzer.logger.error(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'execute_signal', 'details': traceback.format_exc()}
            ))

    def _check_connection_health(self) -> None:
        """Periodically check and maintain WebSocket connection health"""
        current_time = time.time()
        
        if current_time - self.last_connection_check >= self.connection_check_interval:
            self.last_connection_check = current_time
            
            # Check if we're receiving data
            data_age = (datetime.now() - self.analyzer.last_data_time).total_seconds()
            
            if data_age > 120:  # No data for 2 minutes
                self.analyzer.logger.warning(f"Stale data detected ({data_age:.0f} seconds old). Reconnecting...")
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.CONNECTION,
                    data={'status': 'reconnecting', 'reason': 'stale_data', 'age': data_age}
                ))
                
                with self._lock:
                    # Close all connections to force reconnect
                    for symbol, ws in self.ws_connections.items():
                        try:
                            ws.close()
                        except Exception as e:
                            self.analyzer.logger.debug(f"Error closing stale connection: {e}")
                    self.ws_connections = {}

    def _handle_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """Handle WebSocket connection errors"""
        error_msg = f"WebSocket error: {str(error)}"
        self.analyzer.logger.error(error_msg)
        self.analyzer.logger.debug(traceback.format_exc())
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.ERROR,
            data={'error': str(error), 'source': 'websocket'}
        ))
        
    def _handle_close(self, ws: websocket.WebSocketApp, close_status_code: Optional[int], close_msg: Optional[str]) -> None:
        """Handle WebSocket connection closure"""
        close_reason = close_msg if close_msg else 'unknown reason'
        self.analyzer.logger.warning(f"Connection closed: {close_reason}")
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.CONNECTION,
            data={'status': 'closed', 'reason': close_reason, 'code': close_status_code}
        ))
        
    def _handle_open(self, ws: websocket.WebSocketApp) -> None:
        """Handle successful WebSocket connection opening"""
        self.analyzer.logger.info("Connection established successfully")
        self.analyzer.connection_retries = 0
        
        # Reset reconnect delay for this connection
        with self._lock:
            for symbol, conn in self.ws_connections.items():
                if conn is ws:
                    self._reconnect_delays[symbol] = 1
                    break
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.CONNECTION,
            data={'status': 'connected'}
        ))
        print("Successfully connected to the exchange.")

    def _connection_thread(self, symbol: str) -> None:
        """Thread that maintains a WebSocket connection for a symbol"""
        if symbol not in self._reconnect_delays:
            self._reconnect_delays[symbol] = 1
            
        while self.active:
            try:
                with self._lock:
                    need_new_connection = (
                        symbol not in self.ws_connections or 
                        not self.ws_connections[symbol].sock or 
                        not self.ws_connections[symbol].sock.connected
                    )
                
                if need_new_connection:
                    with self._lock:
                        self.ws_connections[symbol] = self._create_connection(symbol)
                        
                    # Run the connection
                    self.ws_connections[symbol].run_forever(
                        ping_interval=30,
                        ping_timeout=10
                    )
                    
                    # If we get here, the connection is closed
                    if self.active:
                        self.analyzer.connection_retries += 1
                        
                        # Calculate backoff delay with jitter
                        with self._lock:
                            base_delay = min(2 ** min(self.analyzer.connection_retries, 6), 60)
                            jitter = np.random.uniform(0, 1)
                            retry_delay = base_delay + jitter
                            self._reconnect_delays[symbol] = retry_delay
                        
                        self.analyzer.logger.info(f"Reconnecting to {symbol} in {retry_delay:.2f} seconds (Attempt {self.analyzer.connection_retries})")
                        self.analyzer.event_emitter.emit(Event(
                            type=EventType.CONNECTION,
                            data={
                                'status': 'reconnecting',
                                'symbol': symbol,
                                'attempt': self.analyzer.connection_retries,
                                'delay': retry_delay
                            }
                        ))
                        time.sleep(retry_delay)
                else:
                    time.sleep(1)  # Sleep when connection is active
                    
            except Exception as e:
                error_msg = f"Connection thread error for {symbol}: {str(e)}"
                self.analyzer.logger.error(error_msg)
                self.analyzer.logger.debug(traceback.format_exc())
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.ERROR,
                    data={'error': error_msg, 'source': 'connection_thread', 'details': traceback.format_exc()}
                ))
                time.sleep(5)

    def _run_health_check(self) -> None:
        """Thread that periodically checks connection health"""
        while self.active:
            try:
                self._check_connection_health()
            except Exception as e:
                error_msg = f"Error in health check: {str(e)}"
                self.analyzer.logger.error(error_msg)
                self.analyzer.logger.debug(traceback.format_exc())
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.ERROR,
                    data={'error': error_msg, 'source': 'health_check', 'details': traceback.format_exc()}
                ))
            time.sleep(1)

    def _graceful_shutdown(self) -> None:
        """Clean up resources during shutdown"""
        self.analyzer.logger.info("Initiating graceful shutdown")
        
        with self._lock:
            self.active = False
            
            self.analyzer.event_emitter.emit(Event(
                type=EventType.CONNECTION,
                data={'status': 'shutdown'}
            ))
            
            # Close all connections
            for symbol, ws in self.ws_connections.items():
                try:
                    ws.close()
                    self.analyzer.logger.debug(f"Closed connection for {symbol}")
                except Exception as e:
                    self.analyzer.logger.error(f"Error closing connection for {symbol}: {str(e)}")
                    self.analyzer.logger.debug(traceback.format_exc())

    def start(self) -> None:
        """Start WebSocket connections for all symbols"""
        try:
            # Start health check thread
            health_check_thread = threading.Thread(
                target=self._run_health_check, 
                daemon=True,
                name="HealthCheckThread"
            )
            health_check_thread.start()
            
            # Start symbol connection threads
            connection_threads = []
            for symbol in self.symbols:
                thread = threading.Thread(
                    target=self._connection_thread, 
                    args=(symbol.lower(),),
                    daemon=True,
                    name=f"WSConnection-{symbol}"
                )
                thread.start()
                connection_threads.append(thread)
                
            # Wait for all threads to complete (which should be never unless interrupted)
            for thread in connection_threads:
                thread.join()
                
        except KeyboardInterrupt:
            self._graceful_shutdown()
        except Exception as e:
            error_msg = f"Critical error in WebSocket manager: {str(e)}"
            self.analyzer.logger.critical(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'websocket_manager', 'level': 'critical', 'details': traceback.format_exc()}
            ))
            self._graceful_shutdown()

# ===== מדווח סטטוס =====
class MarketStatusReporter:
    """דיווח קבוע על תנאי השוק וביצועי המסחר"""
    
    def __init__(self, analyzer: MarketAnalyzer):
        self.analyzer = analyzer
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.prev_price = 0.0
        self.last_print_time = 0.0
        
    def _reporter_thread(self) -> None:
        """Background thread that reports market status periodically"""
        while self.running:
            try:
                # Get current market state and metrics
                state = self.analyzer.get_market_state()
                metrics = state['metrics']
                current_price = state['current_price']
                current_time = time.time()
                if current_price == self.prev_price :
                    if self.prev_price == 0:
                        print("Waiting for market data...")
                    continue
                self.prev_price = current_price
                self.last_print_time = current_time

                
                # Format basic market status
                status = (
                    f"Price: {current_price} | "
                    f"Volatility: {metrics['realized_volatility']:.2f}% | "
                    f"RS: {metrics['relative_strength']:.2f} | "
                    f"Trend: {metrics['trend_strength']} | "
                    f"Imbalance: {metrics['order_imbalance']:.2f} | "
                    f"market_efficiency_ratio: {metrics['market_efficiency_ratio']:.2f} | "
                )
                
                # Add ActiveTrade info if active
                if state['ActiveTrade']['active']:
                    entry = state['ActiveTrade']['entry_price']
                    pnl = state['ActiveTrade']['current_pnl']
                    status += f" | BUY @ {entry:.6f} | PnL: {pnl:.2f}%"
                    
                # Add performance metrics if available
                if state['performance']['total_trades'] > 0:
                    status += (
                        f" | Trades: {state['performance']['total_trades']} | "
                        f"Win: {state['performance']['win_rate']*100:.1f}%"
                    )
                    
                print(status)
                
                # Report recent trade analysis if available
                recent_trades = self.analyzer.trade_journal.get_trades(days=7)
                if recent_trades:
                    recent_analysis = self.analyzer.trade_journal.analyze_performance(days=7)
                    if recent_analysis['total_trades'] > 0:
                        print(
                            f"Last 7 days: {recent_analysis['total_trades']} trades, "
                            f"Win rate: {recent_analysis['win_rate']*100:.1f}%, "
                            f"Avg P&L: {recent_analysis['average_pnl']:.2f}%"
                        )
                
            except Exception as e:
                print(f"Status report error: {str(e)}")
                logging.debug(traceback.format_exc())

    
    def start(self) -> None:
        """Start the market status reporter"""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(
            target=self._reporter_thread,
            daemon=True,
            name="StatusReporterThread"
        )
        self.thread.start()
        
    def stop(self) -> None:
        """Stop the market status reporter"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

# ===== נקודת כניסה ראשית =====
def main() -> None:
    """Main entry point with command-line argument handling"""
    # Configure logging first
    logger = LoggerSetup.get_logger()
    logger.info("Starting Market Trading System")
    
    # Load trading configuration
    TradingConfig.load_from_file()
    
    # Initialize market analyzer
    market_analyzer = MarketAnalyzer(
        warmup_ticks=TradingConfig.DEFAULT_WARMUP_TICKS,
        dynamic_window=TradingConfig.DEFAULT_DYNAMIC_WINDOW,
        risk_factor=TradingConfig.DEFAULT_RISK_FACTOR,
        adaptive_ActiveTrade=TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING
    )

    # Initialize WebSocket manager and backtest engine
    ws_manager = MarketWebSocketManager(market_analyzer, ['btcusdt'])

    # Set up event handlers
    def on_trade_opened(event: Event) -> None:
        print(f"TRADE OPENED: {event.data['direction']} at {event.data['entry_price']:.6f}")

    def on_trade_closed(event: Event) -> None:
        print(f"TRADE CLOSED: PnL {event.data['pnl']:.2f}% ({event.data['reason']})")

    def on_error(event: Event) -> None:
        print(f"ERROR: {event.data['error']} in {event.data['source']}")

    # Register event handlers
    market_analyzer.event_emitter.on(EventType.TRADE_OPENED, on_trade_opened)
    market_analyzer.event_emitter.on(EventType.TRADE_CLOSED, on_trade_closed)
    market_analyzer.event_emitter.on(EventType.ERROR, on_error)

    # Create and start status reporter
    status_reporter = MarketStatusReporter(market_analyzer)
    status_reporter.start()

    try:
        print("Starting market data analysis...")
        
        import sys
        if len(sys.argv) > 1:
            command = sys.argv[1].lower()
            
            if command is None or command == "live":
                # Run in live trading mode
                print("Starting live market data analysis...")
                print("Press Ctrl+C to exit")
                ws_manager.start()
        else:
            print("No command provided. Examples:")
            print("  python flow.py live    # Run in live trading mode")
            print("  python flow.py test    # Run in test mode (if implemented)")
            
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
        logger.debug(traceback.format_exc())
        print(f"Shutdown due to error: {str(e)}")
    finally:
        # Clean up resources
        status_reporter.stop()

if __name__ == "__main__":
    main()