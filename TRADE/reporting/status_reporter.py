import time
import logging
import threading
import traceback
from typing import Optional

# Use relative imports
from ..analysis.market_analyzer import MarketAnalyzer

class MarketStatusReporter:
    """Regular reporting of market conditions and trading performance"""
    
    def __init__(self, analyzer: MarketAnalyzer):
        """
        Initialize the status reporter
        
        Args:
            analyzer: Market analyzer instance
        """
        self.analyzer = analyzer
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.prev_price = 0.0
        self.last_print_time = 0.0
        self.report_interval = 5.0  # seconds between reports
        self.logger = logging.getLogger('MarketAnalyzer.StatusReporter')
        
    def _reporter_thread(self) -> None:
        """Background thread that reports market status periodically"""
        while self.running:
            try:
                current_time = time.time()
                
                # Only report at specified intervals
                if current_time - self.last_print_time < self.report_interval:
                    time.sleep(0.1)  # Short sleep to prevent CPU spinning
                    continue
                
                # Get current market state and metrics
                state = self.analyzer.get_market_state()
                metrics = state['metrics']
                current_price = state['current_price']
                
                # Skip if no price change and not first report
                if current_price == self.prev_price and self.prev_price != 0:
                    time.sleep(0.5)
                    continue
                    
                # Update tracking variables
                self.prev_price = current_price
                self.last_print_time = current_time

                # If no data yet, show waiting message
                if current_price == 0:
                    print("Waiting for market data...")
                    time.sleep(1)
                    continue
                
                # Format basic market status
                status = (
                    f"Price: {current_price} | "
                    f"Volatility: {metrics['realized_volatility']:.2f}% | "
                    f"RS: {metrics['relative_strength']:.2f} | "
                    f"Trend: { metrics['trend_strength']:.4f} | "
                    f"Imbalance: {metrics['order_imbalance']:.2f} | "
                    f"MER: {metrics['market_efficiency_ratio']:.2f}"
                )
                
                # Add active trade info if active
                if state['ActiveTrade']['active']:
                    entry = state['ActiveTrade']['entry_price']
                    pnl = state['ActiveTrade']['current_pnl']
                    status += f" | BUY @ {entry:.6f} | PnL: {pnl:.2f}%"
                    
                # Add performance metrics if available
                if state['performance']['total_trades'] > 0:
                    status += (
                        f" | Trades: {state['performance']['total_trades']} | "
                        f"Win: {state['performance']['win_rate']*100:.1f}%"
                    )
                    
                print(status)
                
                # Report recent trade analysis if available
                # recent_trades = self.analyzer.trade_manager.trade_journal.get_trades(days=7)
                # if recent_trades:
                #     recent_analysis = self.analyzer.trade_manager.trade_journal.analyze_performance(days=7)
                #     if recent_analysis['total_trades'] > 0:
                #         print(
                #             f"Last 7 days: {recent_analysis['total_trades']} trades, "
                #             f"Win rate: {recent_analysis['win_rate']*100:.1f}%, "
                #             f"Avg P&L: {recent_analysis['average_pnl']:.2f}%"
                #         )
                
            except Exception as e:
                error_msg = f"Status report error: {str(e)}"
                print(error_msg)
                self.logger.error(error_msg)
                self.logger.debug(traceback.format_exc())
                time.sleep(5)  # Longer sleep on error
    
    def start(self) -> None:
        """Start the market status reporter"""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(
            target=self._reporter_thread,
            daemon=True,
            name="StatusReporterThread"
        )
        self.thread.start()
        self.logger.info("Status reporter started")
        
    def stop(self) -> None:
        """Stop the market status reporter"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.logger.info("Status reporter stopped")