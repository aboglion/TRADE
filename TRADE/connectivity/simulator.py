import time
import logging
import threading
import traceback
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime

from ..storage.data_storage import DataStorage
from ..events.event import Event
from ..events.event_type import EventType

class MarketSimulator:
    """
    סימולטור שוק המשתמש בנתונים מוקלטים במקום חיבור WebSocket
    """
    
    def __init__(self, data_callback: Callable[[Dict[str, Any]], None], speed_factor: float = 2.0):
        """
        אתחול סימולטור השוק
        
        Args:
            data_callback: פונקציית callback לשליחת נתונים
            speed_factor: מקדם מהירות (1.0 = מהירות רגילה, 2.0 = מהירות כפולה)
        """
        self.data_callback = data_callback
        self.speed_factor = speed_factor
        self.data_storage = DataStorage()
        self.running = False
        self.simulation_thread = None
        self.dataset = []
        self.current_index = 0
    
    def load_dataset(self, filename: str) -> bool:
        """
        טעינת קובץ נתונים לסימולציה
        
        Args:
            filename: שם הקובץ לטעינה
            
        Returns:
            האם הטעינה הצליחה
        """
        self.dataset = self.data_storage.load_dataset(filename)
        return len(self.dataset) > 0
    
    def get_available_datasets(self) -> List[str]:
        """
        קבלת רשימת קבצי נתונים זמינים
        
        Returns:
            רשימת שמות קבצים
        """
        return self.data_storage.get_available_datasets()
    
    def start(self) -> None:
        """התחלת הסימולציה"""
        if not self.dataset:
            logging.error("Cannot start simulation: No dataset loaded")
            return
            
        if self.running:
            logging.warning("Simulation already running")
            return
            
        self.running = True
        self.current_index = 0
        
        self.simulation_thread = threading.Thread(
            target=self._simulation_loop,
            daemon=True,
            name="SimulationThread"
        )
        self.simulation_thread.start()
        logging.info(f"Started market simulation with {len(self.dataset)} ticks")
    
    def stop(self) -> None:
        """עצירת הסימולציה"""
        self.running = False
        if self.simulation_thread and self.simulation_thread.is_alive():
            self.simulation_thread.join(timeout=1.0)
        logging.info("Stopped market simulation")
    
    def _simulation_loop(self) -> None:
        """לולאת הסימולציה הראשית"""
        if not self.dataset:
            return
            
        try:
            # זמן התחלה של הסימולציה
            start_time = time.time()
            first_tick_time = self.dataset[0]['T'] / 1000  # המרה ממילישניות לשניות
            
            while self.running and self.current_index < len(self.dataset):
                # קבלת ה-tick הנוכחי
                current_tick = self.dataset[self.current_index]
                
                # if self.current_index > 0:
                    # # חישוב זמן ההמתנה בין ticks
                    # prev_tick_time = self.dataset[self.current_index - 1]['T'] / 1000
                    # current_tick_time = current_tick['T'] / 1000
                    # wait_time = (current_tick_time - prev_tick_time) / self.speed_factor
                    
                    # # המתנה לזמן המתאים
                    # if wait_time > 0:
                    #     time.sleep(wait_time)
                
                # שליחת הנתונים לcallback
                if self.running:  # בדיקה נוספת במקרה שהסימולציה נעצרה בזמן ההמתנה
                    self.data_callback(current_tick)
                    self.current_index += 1
            
            if self.running:
                logging.info("Simulation completed")
                self.running = False
                
        except Exception as e:
            error_msg = f"Error in simulation loop: {str(e)}"
            logging.error(error_msg)
            logging.debug(traceback.format_exc())
            self.running = False