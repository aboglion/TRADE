package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"TRADE/pkg/logger"
	"TRADE/pkg/manager"
)

func main() {
	// Parse command line arguments
	mode := flag.String("mode", "live", "Trading mode: live or backtest")
	flag.Parse()

	// Initialize logger
	log := logger.NewLogger()
	log.Info("Starting Trading System")

	// Create and initialize the trading manager
	tradingManager := manager.NewManager(log)

	// Start the trading system in the specified mode
	switch *mode {
	case "live":
		fmt.Println("Starting live market data analysis...")
		fmt.Println("Press Ctrl+C to exit")
		tradingManager.StartLiveMode()

	case "backtest":
		fmt.Println("Starting backtest mode...")
		tradingManager.StartBacktestMode()

	default:
		fmt.Printf("Unknown mode: %s\n", *mode)
		fmt.Println("Available modes:")
		fmt.Println("  --mode=live     # Run in live trading mode")
		fmt.Println("  --mode=backtest # Run in backtest mode")
		return
	}

	// Wait for interrupt signal
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	<-sigChan

	fmt.Println("\nShutting down gracefully...")
	tradingManager.Shutdown()
}