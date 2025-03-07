import os
import json
import csv
import logging
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional

class DataStorage:
    """
    מחלקה לאחסון נתוני שוק גולמיים ושליפתם לצורך סימולציה
    """
    
    def __init__(self, storage_dir: str = 'data'):
        """
        אתחול מחלקת אחסון נתונים
        
        Args:
            storage_dir: תיקיית האחסון (יחסית לתיקיית הפרויקט)
        """
        self.storage_dir = storage_dir
        self._ensure_storage_dir()
        self.current_file = None
        self.current_writer = None
        self.file_handle = None
    
    def _ensure_storage_dir(self) -> None:
        """יצירת תיקיית האחסון אם היא לא קיימת"""
        os.makedirs(self.storage_dir, exist_ok=True)
    
    def start_recording(self, symbol: str) -> None:
        """
        התחלת הקלטת נתונים לסמל מסוים
        
        Args:
            symbol: הסמל שעבורו מקליטים נתונים
        """
        try:
            # סגירת קובץ קודם אם פתוח
            self.stop_recording()
            
            # יצירת שם קובץ עם חותמת זמן
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{symbol}_{timestamp}.csv"
            filepath = os.path.join(self.storage_dir, filename)
            
            # פתיחת קובץ CSV לכתיבה
            self.file_handle = open(filepath, 'w', newline='')
            self.current_writer = csv.writer(self.file_handle)
            
            # כתיבת כותרות
            self.current_writer.writerow(['timestamp', 'price', 'volume', 'is_ask'])
            self.current_file = filepath
            
            full_path = os.path.abspath(filepath)
            logging.info(f"Started recording market data to {full_path}")
            print(f"\n📁 Raw market data will be saved to: {full_path}")
        except Exception as e:
            logging.error(f"Error starting data recording: {str(e)}")
            logging.debug(traceback.format_exc())
    
    def record_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        הקלטת נתוני tick בודד
        
        Args:
            tick_data: מילון עם נתוני ה-tick
        """
        if not self.current_writer:
            return
            
        try:
            # המרת חותמת זמן אם צריך
            timestamp = tick_data.get('T', int(datetime.now().timestamp() * 1000))
            
            # כתיבת שורה לקובץ CSV
            self.current_writer.writerow([
                timestamp,
                tick_data.get('p', 0),
                tick_data.get('q', 0),
                tick_data.get('m', False)
            ])
        except Exception as e:
            logging.error(f"Error recording tick data: {str(e)}")
            logging.debug(traceback.format_exc())
    
    def stop_recording(self) -> None:
        """סיום הקלטת נתונים וסגירת הקובץ"""
        if self.file_handle:
            try:
                self.file_handle.close()
                logging.info(f"Stopped recording market data to {self.current_file}")
            except Exception as e:
                logging.error(f"Error closing data file: {str(e)}")
                logging.debug(traceback.format_exc())
            finally:
                self.file_handle = None
                self.current_writer = None
                self.current_file = None
    
    def get_available_datasets(self) -> List[str]:
        """
        קבלת רשימת קבצי נתונים זמינים
        
        Returns:
            רשימת שמות קבצים
        """
        self._ensure_storage_dir()
        return [f for f in os.listdir(self.storage_dir) if f.endswith('.csv')]
    
    def load_dataset(self, filename: str) -> List[Dict[str, Any]]:
        """
        טעינת קובץ נתונים לסימולציה
        
        Args:
            filename: שם הקובץ לטעינה (בתוך תיקיית האחסון)
            
        Returns:
            רשימת נתוני tick
        """
        filepath = os.path.join(self.storage_dir, filename)
        data = []
        
        try:
            with open(filepath, 'r', newline='') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    # המרת שדות לטיפוסים המתאימים
                    tick = {
                        'T': int(row['timestamp']),
                        'p': float(row['price']),
                        'q': float(row['volume']),
                        'm': row['is_ask'].lower() == 'true'
                    }
                    data.append(tick)
            
            logging.info(f"Loaded {len(data)} ticks from {filepath}")
            return data
        except Exception as e:
            logging.error(f"Error loading dataset {filepath}: {str(e)}")
            logging.debug(traceback.format_exc())
            return []