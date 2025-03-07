import threading
import logging
import traceback
from typing import Dict, List, Callable

from .event_type import EventType
from .event import Event

class EventEmitter:
    def __init__(self):
        self.listeners: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._lock = threading.RLock()  # Thread-safe operation
        
    def on(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """רישום מאזין לאירוע"""
        with self._lock:
            if event_type not in self.listeners:
                self.listeners[event_type] = []
            self.listeners[event_type].append(callback)
        
    def emit(self, event: Event) -> None:
        """הפצת אירוע לכל המאזינים הרשומים"""
        with self._lock:
            listeners = self.listeners.get(event.type, []).copy()
            
        for callback in listeners:
            try:
                callback(event)
            except Exception as e:
                logging.error(f"Error in event listener for {event.type}: {str(e)}")
                logging.debug(traceback.format_exc())

    def remove(self, event_type: EventType, callback: Callable[[Event], None]) -> bool:
        """הסרת מאזין מרשימת המאזינים"""
        with self._lock:
            if event_type in self.listeners and callback in self.listeners[event_type]:
                self.listeners[event_type].remove(callback)
                return True
        return False