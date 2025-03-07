import json
import logging
import threading
import time
import traceback
import numpy as np
import websocket
from datetime import datetime
from typing import Dict, List, Optional, Any, Union

# Use relative imports
from ..analysis.market_analyzer import MarketAnalyzer
from ..events.event import Event
from ..events.event_type import EventType
from ..storage.data_storage import DataStorage

class MarketWebSocketManager:
    """WebSocket connection manager for market data streaming"""

    def __init__(self, analyzer: MarketAnalyzer, symbols: List[str] = ['btcusdt'], record_data: bool = False):
        """
        Initialize the WebSocket manager
        
        Args:
            analyzer: Market analyzer instance
            symbols: List of trading symbols to connect to
            record_data: Whether to record market data
        """
        self.analyzer = analyzer
        self.symbols = symbols
        self.ws_connections: Dict[str, websocket.WebSocketApp] = {}
        self.active = True
        self.connection_check_interval = 30  # seconds
        self.last_connection_check = time.time()
        self._lock = threading.RLock()  # Thread-safe operation
        self._reconnect_delays: Dict[str, float] = {}
        
        # Data recording support
        self.record_data = record_data
        self.data_storage = DataStorage() if record_data else None

    def _create_connection(self, symbol: str) -> websocket.WebSocketApp:
        """
        Create WebSocket connection with proper handlers
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Configured WebSocketApp instance
        """
        url = f"wss://stream.binance.com:9443/ws/{symbol}@trade"
        
        def on_message(ws, message):
            self._handle_message(ws, message)
            
        def on_error(ws, error):
            self._handle_error(ws, error)
            
        def on_close(ws, close_status_code, close_msg):
            self._handle_close(ws, close_status_code, close_msg)
            
        def on_open(ws):
            self._handle_open(ws)
            
        return websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

    def _handle_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """
        Handle incoming WebSocket messages
        
        Args:
            ws: WebSocket connection
            message: Message string
        """
        try:
            data = json.loads(message)
            
            # Normalize the data to use expected field names
            normalized_data = {
                'p': float(data.get('p', 0)),       # price
                'q': float(data.get('q', 0)),       # quantity
                'm': bool(data.get('m', False)),    # is buyer maker
                'T': int(data.get('T', 0))          # timestamp
            }
            
            # Record data if enabled
            if self.record_data and self.data_storage:
                self.data_storage.record_tick(normalized_data)
                
            # Process tick and check for trading signals
            signal = self.analyzer.process_tick(normalized_data)
            
            if signal:
                symbol = data.get('s', 'UNKNOWN').upper()
                self._execute_signal(signal, symbol)
                
        except json.JSONDecodeError:
            self.analyzer.logger.error("Invalid JSON message received")
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': "Invalid JSON message", 'source': 'websocket'}
            ))
        except Exception as e:
            error_msg = f"Error handling message: {str(e)}"
            self.analyzer.logger.error(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'websocket', 'details': traceback.format_exc()}
            ))

    def _execute_signal(self, signal: Dict[str, Any], symbol: str) -> None:
        """
        Execute trading signal and log details
        
        Args:
            signal: Signal dictionary
            symbol: Trading symbol
        """
        try:
            action = signal.get('action', '').upper()
            
            if not action:
                return
                
            details = ""
            if action == 'BUY':
                details = f"{signal.get('side', '').upper()} @ {signal['price']:.6f}"
            elif action in ('CLOSE', 'SELL'):
                details = f"@ {signal['price']:.6f} ({signal.get('reason', 'unknown')})"
                
            # Get current market state
            market_state = self.analyzer.get_market_state()
            metrics = market_state['metrics']
            
            # Prepare formatted log message
            log_msg = (
                f"\n{' MARKET SIGNAL ':=^60}\n"
                f"| {action} {details} |\n"
                f"| Symbol: {symbol} | Vol: {metrics['realized_volatility']:.2f}% | RS: {metrics['relative_strength']:.2f} |\n"
                f"| Order Imbalance: {metrics['order_imbalance']:.2f} | Trend: {metrics['trend_strength']:.2f} |\n"
                f"{'':=^60}"
            )
            
            print(log_msg)
            self.analyzer.logger.info(f"Signal: {action} {details} on {symbol}")
            
        except Exception as e:
            error_msg = f"Error executing signal: {str(e)}"
            self.analyzer.logger.error(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'execute_signal', 'details': traceback.format_exc()}
            ))

    def _check_connection_health(self) -> None:
        """Periodically check and maintain WebSocket connection health"""
        current_time = time.time()
        
        if current_time - self.last_connection_check >= self.connection_check_interval:
            self.last_connection_check = current_time
            
            # Check if we're receiving data
            data_age = (datetime.now() - self.analyzer.last_data_time).total_seconds()
            
            if data_age > 120:  # No data for 2 minutes
                self.analyzer.logger.warning(f"Stale data detected ({data_age:.0f} seconds old). Reconnecting...")
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.CONNECTION,
                    data={'status': 'reconnecting', 'reason': 'stale_data', 'age': data_age}
                ))
                
                with self._lock:
                    # Close all connections to force reconnect
                    for symbol, ws in self.ws_connections.items():
                        try:
                            ws.close()
                        except Exception as e:
                            self.analyzer.logger.debug(f"Error closing stale connection: {e}")
                    self.ws_connections = {}

    def _handle_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """
        Handle WebSocket connection errors
        
        Args:
            ws: WebSocket connection
            error: Exception that occurred
        """
        error_msg = f"WebSocket error: {str(error)}"
        self.analyzer.logger.error(error_msg)
        self.analyzer.logger.debug(traceback.format_exc())
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.ERROR,
            data={'error': str(error), 'source': 'websocket'}
        ))
        
    def _handle_close(self, ws: websocket.WebSocketApp, close_status_code: Optional[int], close_msg: Optional[str]) -> None:
        """
        Handle WebSocket connection closure
        
        Args:
            ws: WebSocket connection
            close_status_code: Status code for closure
            close_msg: Closure message
        """
        close_reason = close_msg if close_msg else 'unknown reason'
        self.analyzer.logger.warning(f"Connection closed: {close_reason}")
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.CONNECTION,
            data={'status': 'closed', 'reason': close_reason, 'code': close_status_code}
        ))
        
    def _handle_open(self, ws: websocket.WebSocketApp) -> None:
        """
        Handle successful WebSocket connection opening
        
        Args:
            ws: WebSocket connection
        """
        self.analyzer.logger.info("Connection established successfully")
        self.analyzer.connection_retries = 0
        
        # Reset reconnect delay for this connection
        with self._lock:
            for symbol, conn in self.ws_connections.items():
                if conn is ws:
                    self._reconnect_delays[symbol] = 1
                    break
        
        self.analyzer.event_emitter.emit(Event(
            type=EventType.CONNECTION,
            data={'status': 'connected'}
        ))
        print("Successfully connected to the exchange.")

    def _connection_thread(self, symbol: str) -> None:
        """
        Thread that maintains a WebSocket connection for a symbol
        
        Args:
            symbol: Trading symbol
        """
        if symbol not in self._reconnect_delays:
            self._reconnect_delays[symbol] = 1
            
        while self.active:
            try:
                with self._lock:
                    need_new_connection = (
                        symbol not in self.ws_connections or 
                        not self.ws_connections[symbol].sock or 
                        not self.ws_connections[symbol].sock.connected
                    )
                
                if need_new_connection:
                    with self._lock:
                        self.ws_connections[symbol] = self._create_connection(symbol)
                        
                    # Run the connection
                    self.ws_connections[symbol].run_forever(
                        ping_interval=30,
                        ping_timeout=10
                    )
                    
                    # If we get here, the connection is closed
                    if self.active:
                        self.analyzer.connection_retries += 1
                        
                        # Calculate backoff delay with jitter
                        with self._lock:
                            base_delay = min(2 ** min(self.analyzer.connection_retries, 6), 60)
                            jitter = np.random.uniform(0, 1)
                            retry_delay = base_delay + jitter
                            self._reconnect_delays[symbol] = retry_delay
                        
                        self.analyzer.logger.info(f"Reconnecting to {symbol} in {retry_delay:.2f} seconds (Attempt {self.analyzer.connection_retries})")
                        self.analyzer.event_emitter.emit(Event(
                            type=EventType.CONNECTION,
                            data={
                                'status': 'reconnecting',
                                'symbol': symbol,
                                'attempt': self.analyzer.connection_retries,
                                'delay': retry_delay
                            }
                        ))
                        time.sleep(retry_delay)
                else:
                    time.sleep(1)  # Sleep when connection is active
                    
            except Exception as e:
                error_msg = f"Connection thread error for {symbol}: {str(e)}"
                self.analyzer.logger.error(error_msg)
                self.analyzer.logger.debug(traceback.format_exc())
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.ERROR,
                    data={'error': error_msg, 'source': 'connection_thread', 'details': traceback.format_exc()}
                ))
                time.sleep(5)

    def _run_health_check(self) -> None:
        """Thread that periodically checks connection health"""
        while self.active:
            try:
                self._check_connection_health()
            except Exception as e:
                error_msg = f"Error in health check: {str(e)}"
                self.analyzer.logger.error(error_msg)
                self.analyzer.logger.debug(traceback.format_exc())
                self.analyzer.event_emitter.emit(Event(
                    type=EventType.ERROR,
                    data={'error': error_msg, 'source': 'health_check', 'details': traceback.format_exc()}
                ))
            time.sleep(1)

    def _graceful_shutdown(self) -> None:
        """Clean up resources during shutdown"""
        self.analyzer.logger.info("Initiating graceful shutdown")
        
        # Stop data recording
        if self.record_data and self.data_storage:
            self.data_storage.stop_recording()
        
        with self._lock:
            self.active = False
            
            self.analyzer.event_emitter.emit(Event(
                type=EventType.CONNECTION,
                data={'status': 'shutdown'}
            ))
            
            # Close all connections
            for symbol, ws in self.ws_connections.items():
                try:
                    ws.close()
                    self.analyzer.logger.debug(f"Closed connection for {symbol}")
                except Exception as e:
                    self.analyzer.logger.error(f"Error closing connection for {symbol}: {str(e)}")
                    self.analyzer.logger.debug(traceback.format_exc())

    def start(self) -> None:
        """Start WebSocket connections for all symbols"""
        try:
            # Start data recording if enabled
            if self.record_data and self.data_storage and self.symbols:
                # Use the first symbol in the list
                self.data_storage.start_recording(self.symbols[0])
                self.analyzer.logger.info(f"Started recording market data for {self.symbols[0]}")
            
            # Start health check thread
            health_check_thread = threading.Thread(
                target=self._run_health_check,
                daemon=True,
                name="HealthCheckThread"
            )
            health_check_thread.start()
            
            # Start symbol connection threads
            connection_threads = []
            for symbol in self.symbols:
                thread = threading.Thread(
                    target=self._connection_thread,
                    args=(symbol.lower(),),
                    daemon=True,
                    name=f"WSConnection-{symbol}"
                )
                thread.start()
                connection_threads.append(thread)
                
            # Wait for all threads to complete (which should be never unless interrupted)
            for thread in connection_threads:
                thread.join()
                
        except KeyboardInterrupt:
            self._graceful_shutdown()
        except Exception as e:
            error_msg = f"Critical error in WebSocket manager: {str(e)}"
            self.analyzer.logger.critical(error_msg)
            self.analyzer.logger.debug(traceback.format_exc())
            self.analyzer.event_emitter.emit(Event(
                type=EventType.ERROR,
                data={'error': error_msg, 'source': 'websocket_manager', 'level': 'critical', 'details': traceback.format_exc()}
            ))
            self._graceful_shutdown()