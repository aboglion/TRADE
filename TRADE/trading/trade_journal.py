import os
import csv
import uuid
import logging
import threading
import traceback
from typing import Dict, Optional, List, Any
from datetime import datetime, timedelta

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
    
    def get_trade_distribution(self, days: Optional[int] = None) -> Dict[str, Dict[str, int]]:
        """קבלת התפלגות עסקאות לפי שעה, יום וכו'"""
        trades = self.get_trades(days)
        
        if not trades:
            return {"hourly": {}, "daily": {}, "duration": {}}
        
        hourly = {i: 0 for i in range(24)}
        daily = {day: 0 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
        duration_bins = {"0-30min": 0, "30-60min": 0, "1-4hrs": 0, "4-12hrs": 0, "12+hrs": 0}
        
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