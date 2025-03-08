import json
import os
import logging
import traceback

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
            'trailing_stop_distance': 1.5       # מרחק מהמחיר הנוכחי להפעלת trailing stop
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