from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime

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
        try:
            # Ensure entry_time is a datetime object before calling isoformat()
            entry_time_str = None
            if self.entry_time:
                if isinstance(self.entry_time, datetime):
                    entry_time_str = self.entry_time.isoformat()
                else:
                    # If it's already a string, just use it directly
                    entry_time_str = self.entry_time
            
            return {
                'active': self.active,
                'entry_price': self.entry_price,
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'highest_price': self.highest_price,
                'lowest_price': self.lowest_price,
                'direction': self.direction,
                'size': self.size,
                'entry_time': entry_time_str
            }
        except Exception as e:
            # Fallback in case of error
            import logging
            logging.error(f"Error in ActiveTrade.to_dict: {str(e)}")
            return {
                'active': self.active,
                'entry_price': self.entry_price,
                'stop_loss': self.stop_loss,
                'take_profit': self.take_profit,
                'highest_price': self.highest_price,
                'lowest_price': self.lowest_price,
                'direction': self.direction,
                'size': self.size,
                'entry_time': None
            }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActiveTrade':
        """Create ActiveTrade from dictionary data"""
        ActiveTrade = cls()
        try:
            for key, value in data.items():
                if key == 'entry_time' and value:
                    if isinstance(value, str):
                        try:
                            ActiveTrade.entry_time = datetime.fromisoformat(value)
                        except ValueError:
                            import logging
                            logging.warning(f"Invalid entry_time format: {value}. Using as string.")
                            ActiveTrade.entry_time = value
                    else:
                        ActiveTrade.entry_time = value
                elif hasattr(ActiveTrade, key):
                    setattr(ActiveTrade, key, value)
        except Exception as e:
            import logging
            logging.error(f"Error in ActiveTrade.from_dict: {str(e)}")
        return ActiveTrade