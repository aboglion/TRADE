import sys
import traceback
from datetime import datetime

# Use relative imports when running as a module within the TRADE package
from .events.event import Event
from .events.event_type import EventType
from .utils.logger import LoggerSetup
from .utils.config import TradingConfig
from .analysis.market_analyzer import MarketAnalyzer
from .connectivity.websocket_manager import MarketWebSocketManager
from .reporting.status_reporter import MarketStatusReporter

def main() -> None:
    """Main entry point with command-line argument handling"""
    # Configure logging first
    logger = LoggerSetup.get_logger()
    logger.info("Starting Market Trading System")
    
    # Load trading configuration
    TradingConfig.load_from_file()
    
    # Initialize market analyzer
    market_analyzer = MarketAnalyzer(
        warmup_ticks=TradingConfig.DEFAULT_WARMUP_TICKS,
        dynamic_window=TradingConfig.DEFAULT_DYNAMIC_WINDOW,
        risk_factor=TradingConfig.DEFAULT_RISK_FACTOR,
        adaptive_sizing=TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING
    )

    # Initialize WebSocket manager
    ws_manager = MarketWebSocketManager(market_analyzer, ['btcusdt'])

    # Set up event handlers
    def on_trade_opened(event: Event) -> None:
        print(f"TRADE OPENED: {event.data['direction']} at {event.data['entry_price']:.6f}")

    def on_trade_closed(event: Event) -> None:
        print(f"TRADE CLOSED: PnL {event.data['pnl']:.2f}% ({event.data['reason']})")

    def on_error(event: Event) -> None:
        print(f"ERROR: {event.data['error']} in {event.data['source']}")

    # Register event handlers
    market_analyzer.event_emitter.on(EventType.TRADE_OPENED, on_trade_opened)
    market_analyzer.event_emitter.on(EventType.TRADE_CLOSED, on_trade_closed)
    market_analyzer.event_emitter.on(EventType.ERROR, on_error)

    # Create and start status reporter
    status_reporter = MarketStatusReporter(market_analyzer)
    status_reporter.start()

    try:
        print("Starting market data analysis...")
        
        if len(sys.argv) > 1:
            command = sys.argv[1].lower()
            
            if command == "live":
                # Run in live trading mode
                print("Starting live market data analysis...")
                print("Press Ctrl+C to exit")
                ws_manager.start()
            elif command == "backtest":
                # Run in backtest mode if implemented
                print("Backtest mode not yet implemented")
            else:
                print(f"Unknown command: {command}")
                print("Available commands:")
                print("  python -m TRADE.main live     # Run in live trading mode")
                print("  python -m TRADE.main backtest # Run in backtest mode (if implemented)")
        else:
            print("No command provided. Examples:")
            print("  python -m TRADE.main live     # Run in live trading mode")
            print("  python -m TRADE.main backtest # Run in backtest mode (if implemented)")
            
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
        logger.debug(traceback.format_exc())
        print(f"Shutdown due to error: {str(e)}")
    finally:
        # Clean up resources
        status_reporter.stop()

if __name__ == "__main__":
    main()