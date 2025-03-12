import logging
import threading
import traceback
import numpy as np
from scipy import stats
from typing import Dict
from collections import deque

# Keep the relative import as is since it's already correct
from .market_data import MarketData

class MarketMetricsCalculator:

    def __init__(self, market_data: MarketData):
        self.market_data = market_data
        self._trend_strength_window = deque(maxlen=20)
        self.metrics: Dict[str, float] = {
            'realized_volatility': 0.0,
            'atr': 0.0,
            'relative_strength': 0.5,
            'order_imbalance': 0.5,
            'trend_strength': 0.0,
            'avg_trend_strength': 0.0,
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
                self._trend_strength_window.append(trend_strength)
                avg_trend_strength = np.mean(self._trend_strength_window) if len(self._trend_strength_window) >= 7 else 0.0

                mer = self._calculate_market_efficiency_ratio(prices)
    
                # Update metrics dictionary
                self.metrics.update({
                    'realized_volatility': float(realized_volatility),#==>  אחוז שינוי המחיר הממוצע באחוזים
                    'atr': float(atr), #==> מודד תנודות השוק
                    'relative_strength': float(relative_strength), #==> כוח המטבע
                    'order_imbalance': float(order_imbalance), #==> יחס בין bid ל-ask
                    'trend_strength': float(trend_strength), #==> עוצמת מגמה
                    'avg_trend_strength': float(avg_trend_strength), #==> עוצמת מגמה ממוצעת
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
                trend_strength = slope * r_value**2 * (30 / np.mean(window_prices)) * 100000 
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
                'avg_trend_strength': 0.0,
                'market_efficiency_ratio': 0.0,
            }