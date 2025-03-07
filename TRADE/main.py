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
from .connectivity.simulator import MarketSimulator
from .storage.data_storage import DataStorage
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
                # Run in live trading mode with data recording
                print("Starting live market data analysis...")
                print("Raw market data will be recorded to data/ directory")
                print("Press Ctrl+C to exit")
                
                # Initialize WebSocket manager with recording enabled
                ws_manager = MarketWebSocketManager(market_analyzer, ['btcusdt'], record_data=True)
                ws_manager.start()
                
            elif command == "backtest":
                # Run in backtest mode with dataset selection
                print("\nAvailable historical datasets:")
                data_storage = DataStorage()
                datasets = data_storage.get_available_datasets()
                
                if not datasets:
                    print("No datasets found in data/ directory")
                    print("Record some live data first using 'live' mode")
                    return
                    
                for idx, dataset in enumerate(datasets, 1):
                    print(f"{idx}. {dataset}")
                    
                try:
                    selection = int(input("\nSelect dataset to use (number): "))
                    if selection < 1 or selection > len(datasets):
                        raise ValueError
                        
                    selected_dataset = datasets[selection-1]
                    
                    # Initialize simulator
                    simulator = MarketSimulator(market_analyzer.process_tick)
                    if not simulator.load_dataset(selected_dataset):
                        print(f"Failed to load dataset: {selected_dataset}")
                        return
                        
                    print(f"\nStarting backtest with {selected_dataset}")
                    simulator.start()
                    
                    # Keep main thread alive while simulator runs
                    while simulator.running:
                        pass
                        
                except ValueError:
                    print("Invalid selection - please enter a valid number")
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