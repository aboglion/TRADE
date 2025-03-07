import os
import json
import logging
import threading
import traceback
import numpy as np
from typing import Dict, List, Any
from datetime import datetime

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