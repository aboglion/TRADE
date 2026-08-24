package manager

import (
	"fmt"
	"time"

	"TRADE/pkg/analyzer"
	"TRADE/pkg/logger"
	"TRADE/pkg/market"
	"TRADE/pkg/strategy"
	"TRADE/pkg/types"
)

// Manager coordinates all components of the trading system
type Manager struct {
	logger   *logger.Logger
	market   *market.MarketData
	analyzer *analyzer.Analyzer
	strategy *strategy.Strategy
	running  bool
}

// NewManager creates a new trading system manager
func NewManager(log *logger.Logger) *Manager {
	return &Manager{
		logger:  log,
		running: false,
	}
}

// Initialize sets up all components of the trading system
func (m *Manager) Initialize() error {
	m.logger.Info("Initializing trading system components")

	// Initialize market data component
	m.market = market.NewMarketData(m.logger)

	// Initialize analyzer with market data
	m.analyzer = analyzer.NewAnalyzer(m.market, m.logger)

	// Initialize strategy with analyzer
	m.strategy = strategy.NewStrategy(m.analyzer, m.logger)

	// Set up callbacks
	m.setupCallbacks()

	return nil
}

// setupCallbacks configures event handlers between components
func (m *Manager) setupCallbacks() {
	// Set up callback for when new market data is received
	m.market.SetTickCallback(func(tick *types.TickData) {
		// Process the tick through the analyzer
		metrics := m.analyzer.ProcessTick(tick)
		
		// If we have valid metrics and enough data, check for trading signals
		if metrics != nil && m.analyzer.HasSufficientData() {
			// Generate trading signals based on the metrics
			signal := m.strategy.GenerateSignal(tick.Price, tick.Timestamp, metrics)
			
			// Process any trading signals
			if signal != nil {
				m.processSignal(signal, tick.Price, tick.Timestamp)
			}
		}
	})
}

// processSignal handles trading signals from the strategy
func (m *Manager) processSignal(signal *types.Signal, price float64, timestamp time.Time) {
	switch signal.Action {
	case "BUY":
		m.logger.Info(fmt.Sprintf("BUY SIGNAL at price %.6f", price))
		// Execute buy logic here
		
	case "SELL", "CLOSE":
		m.logger.Info(fmt.Sprintf("SELL SIGNAL at price %.6f (reason: %s)", price, signal.Reason))
		// Execute sell logic here
		
	default:
		m.logger.Warning(fmt.Sprintf("Unknown signal action: %s", signal.Action))
	}
}

// StartLiveMode starts the system in live trading mode
func (m *Manager) StartLiveMode() error {
	if err := m.Initialize(); err != nil {
		return err
	}
	
	m.running = true
	m.logger.Info("Starting live trading mode")
	
	// Connect to live market data
	if err := m.market.ConnectLive([]string{"btcusdt"}); err != nil {
		m.logger.Error(fmt.Sprintf("Failed to connect to live market: %v", err))
		return err
	}
	
	// Start periodic status reporting
	go m.startStatusReporting()
	
	return nil
}

// startStatusReporting periodically reports system status
func (m *Manager) startStatusReporting() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	
	for {
		if !m.running {
			return
		}
		
		<-ticker.C
		
		// Get current market state
		currentPrice := m.market.GetCurrentPrice()
		metrics := m.analyzer.GetMetrics()
		tradeActive := m.strategy.IsActiveTrade()
		
		// Calculate PnL if there's an active trade
		tradePnL := 0.0
		if tradeActive {
			tradeData := m.strategy.GetActiveTradeData()
			tradePnL = tradeData.CurrentPnL
		}
		
		// Report status
		m.logger.ReportMarketStatus(currentPrice, metrics, tradeActive, tradePnL)
	}
}

// StartBacktestMode starts the system in backtest mode
func (m *Manager) StartBacktestMode() error {
	if err := m.Initialize(); err != nil {
		return err
	}
	
	m.running = true
	m.logger.Info("Starting backtest mode")
	
	// Get available datasets
	datasets, err := m.market.GetAvailableDatasets()
	if err != nil {
		m.logger.Error(fmt.Sprintf("Failed to get datasets: %v", err))
		return err
	}
	
	if len(datasets) == 0 {
		m.logger.Warning("No datasets available for backtesting")
		return fmt.Errorf("no datasets available")
	}
	
	// Display available datasets
	fmt.Println("\nAvailable historical datasets:")
	for i, dataset := range datasets {
		fmt.Printf("%d. %s\n", i+1, dataset)
	}
	
	// Select dataset (in a real implementation, this would be interactive)
	selectedDataset := datasets[0]
	fmt.Printf("\nSelected dataset: %s\n", selectedDataset)
	
	// Load and process the dataset
	if err := m.market.LoadHistoricalData(selectedDataset); err != nil {
		m.logger.Error(fmt.Sprintf("Failed to load dataset: %v", err))
		return err
	}
	
	// Report final results
	m.reportBacktestResults()
	
	return nil
}

// reportBacktestResults reports the results of the backtest
func (m *Manager) reportBacktestResults() {
	// In a real implementation, this would calculate and report performance metrics
	fmt.Println("\nBacktest Results:")
	fmt.Println("=================")
	fmt.Println("Backtest completed successfully")
	
	// If we had a performance tracker, we would report metrics like:
	// - Total trades
	// - Win rate
	// - Average profit/loss
	// - Maximum drawdown
	// - Sharpe ratio
	// etc.
}

// Shutdown gracefully stops all components
func (m *Manager) Shutdown() {
	if !m.running {
		return
	}
	
	m.logger.Info("Shutting down trading system")
	m.running = false
	
	// Disconnect market data
	if m.market != nil {
		m.market.Disconnect()
	}
	
	// Perform any other cleanup
	m.logger.Info("Trading system shutdown complete")
}