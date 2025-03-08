import threading
from collections import deque
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional

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
                # Ensure timestamp is a datetime object
                if not isinstance(timestamp, datetime):
                    try:
                        if isinstance(timestamp, str):
                            # Try to parse string as ISO format
                            timestamp = datetime.fromisoformat(timestamp)
                        elif isinstance(timestamp, (int, float)):
                            # Convert timestamp to datetime
                            timestamp = datetime.fromtimestamp(timestamp / 1000)
                        else:
                            # Fallback to current time
                            timestamp = datetime.now()
                    except Exception:
                        # If all conversions fail, use current time
                        timestamp = datetime.now()
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