import logging
import os
from datetime import datetime

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