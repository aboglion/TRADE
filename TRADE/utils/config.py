import json
import os
import logging
import traceback, datetime
from typing import Dict, Optional, Any


class TradingConfig:
    DEFAULT_WARMUP_TICKS = 300
    DEFAULT_RISK_FACTOR = 0.02  # סיכון של 2% לעסקה
    DEFAULT_DYNAMIC_WINDOW = True
    DEFAULT_ADAPTIVE_ActiveTrade_SIZING = True
    

    # metrics['realized_volatility']  # כניסה בקניה כאשר התנודות גבוהות
    # metrics['relative_strength']    # כניסה בקניה כאשר RS גבוה
    #metrics['trend_strength']     # חזקה כאשר המחיר עולה 5 פעמים רצופות
    # metrics['order_imbalance']    # כניסה בקניה כאשר יחס הזמנות גבוה
    # metrics['market_efficiency_ratio']     # כניסה בקניה כאשר השוק יעיל
    @classmethod
    def check_buy_conditions(cls:'TradingConfig',metrics: dict) -> bool:
        return (
        metrics['realized_volatility'] >= 0.3 and
        metrics['relative_strength'] >= 0.2 and
        metrics['relative_strength'] <= 0.5 and
        metrics['trend_strength'] >= 6 and
        metrics['order_imbalance'] >= 0.25 and
        metrics['market_efficiency_ratio'] >= 1.0 
        )

    @classmethod
    def check_sell_conditions (cls: 'TradingConfig',
                        entry_time: datetime,
                        entry_price: float,
                        highest_price: float,
                        price: float, 
                        timestamp: datetime, 
                        metrics: Dict[str, float], 
                        active_trade_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    
        TRAILING_STOP_ACT_IVATION=1.0     # הפעלת trailing stop כאשר הרווח מגיע אחוז מסוים
        PROFIT_TARGET_MULTIPLIER=2.5     # יעד רווח ביחס לסיכון
        TRAILING_STOP_DISTANCE=1.5       # מרחק מהמחיר הנוכחי להפעלת trailing stop
        TREND_STRENGTH_THRESHOLD = -7.0  # סף עוצמת מגמה ליציאה

        # Check if there is an active trade
        if not active_trade_data:
            return False
        # Calculate current profit percentage
        profit = (price / entry_price - 1)
        stop_triggered = False
        reason = None

        # Calculate stop loss and take profit levels
        atr = max(metrics['atr'], price * 0.001)  # Use minimum 0.1% ATR
        stop_distance = TRAILING_STOP_DISTANCE * atr
        profit_distance = stop_distance * PROFIT_TARGET_MULTIPLIER
        
        # For long trades: stop below entry, target above entry
        stop_loss = price - stop_distance
        take_profit = price + profit_distance

        # Check stop loss
        if price <= stop_loss:
            stop_triggered = True
            reason = 'stop_loss'

        # Check take profit
        if price >= take_profit:
            stop_triggered = True
            reason = 'take_profit'

         # Adjust trailing stop if profit exceeds activation threshold
        activation_threshold = TRAILING_STOP_ACT_IVATION / 100
        if profit >= activation_threshold:
            # Calculate trailing stop level
            trail_distance = TRAILING_STOP_ACT_IVATION * (metrics['atr'] / highest_price)
            trail_level = highest_price * (1 - trail_distance)
            
            # Update stop loss if trailing stop is higher
            if trail_level > stop_loss:
                stop_loss = trail_level
                logging.getLogger('MarketAnalyzer.SignalGenerator').debug(f"Trailing stop updated to {trail_level:.6f} (profit: {profit*100:.2f}%)")
                
        # Check time-based exit
        if entry_time:
            trade_duration = (timestamp - entry_time).total_seconds() / 3600
            if trade_duration > 4:  # Exit after 4 hours
                stop_triggered = True
                reason = 'time_exit'
                
        # Check trend reversal exit
        if metrics['trend_strength'] < TREND_STRENGTH_THRESHOLD:
            stop_triggered = True
            reason = 'trend_reversal'
        return {stop_triggered, reason, stop_loss, profit}

    
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