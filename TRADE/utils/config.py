import json
import os
import logging
import traceback, datetime
from typing import Dict, Optional, Any

# for rv_upper in [0.90, 0.80, 0.70, 0.60]:
#     for rv_lower in [0.50, 0.40, 0.30, 0.20, 0.10]:
#         for rs_upper in [0.90, 0.80, 0.70, 0.60]:
#             for rs_lower in [0.50, 0.40, 0.30, 0.20, 0.10]:
#                 for ts in [5, 7, 9, 12]:
#                     for avg_ts in [5, 7, 9]:
#                         for oi in [0.65, 0.80, 1]:

                         
class TradingConfig:
    DEFAULT_WARMUP_TICKS = 300
    DEFAULT_RISK_FACTOR = 0.02  # סיכון של 2% לעסקה
    DEFAULT_DYNAMIC_WINDOW = True
    DEFAULT_ADAPTIVE_ActiveTrade_SIZING = True
    

    # metrics['realized_volatility']  #  התנודות 
    # metrics['relative_strength']    #    RS כאשר המחיר עולה יותר מהורד מהממוצע
    #metrics['trend_strength']     # חזקה כאשר המחיר עולה 5 פעמים רצופות
    # metrics['order_imbalance']    # כניסה בקניה כאשר יחס הזמנות גבוה
    # metrics['market_efficiency_ratio']     # כניסה בקניה כאשר השוק יעיל
    @classmethod
    def check_buy_conditions(cls: 'TradingConfig', metrics: dict, metrics_sp: dict = {
        'realized_volatility_hi': 0.70,
        'realized_volatility_lo': 0.35,
        'relative_strength_hi': 0.75,
        'relative_strength_lo': 0.25,
        'trend_strength': 5,
        'avg_trend_strength': 3,
        'order_imbalance': 0.65,
        'market_efficiency_ratio': 0.93}) -> bool:
            return (
                metrics['realized_volatility'] <= metrics_sp['realized_volatility_hi'] and #0.70
                metrics['realized_volatility'] >= metrics_sp['realized_volatility_lo'] and #0.35
                metrics['relative_strength'] <= metrics_sp['relative_strength_hi'] and #0.75
                metrics['relative_strength'] >= metrics_sp['relative_strength_lo'] and #0.25
                metrics['trend_strength'] >= metrics_sp['trend_strength'] and #5    
                metrics['avg_trend_strength'] >= metrics_sp['avg_trend_strength'] and #3
                metrics['trend_strength'] > metrics['avg_trend_strength'] and   
                metrics['order_imbalance'] >= metrics_sp['order_imbalance'] and #0.65
                metrics['market_efficiency_ratio'] >= metrics_sp['market_efficiency_ratio'] #0.93
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
        MIN_PROFIT = 0.3             # רווח מינימלי לסגירת עסקה

        # Check if there is an active trade
        if not active_trade_data:
            return False
        # Calculate current profit percentage
        profit = (price / entry_price - 1) * 100
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
            if trade_duration > 4 and profit >= MIN_PROFIT:  # Exit after 4 hours
                stop_triggered = True
                reason = 'time_exit'
                
        # Check trend reversal exit
        if metrics['trend_strength'] < TREND_STRENGTH_THRESHOLD and profit >= MIN_PROFIT:
            stop_triggered = True
            reason = 'trend_reversal'
        return (stop_triggered, reason, stop_loss, profit)

    
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