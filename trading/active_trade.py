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