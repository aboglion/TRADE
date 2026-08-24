package strategy

import (
	"sync"
	"time"

	"TRADE/pkg/analyzer"
	"TRADE/pkg/logger"
	"TRADE/pkg/types"
)

// Strategy generates trading signals based on market conditions
type Strategy struct {
	analyzer       *analyzer.Analyzer
	logger         *logger.Logger
	activeTrade    *types.TradeData
	mutex          sync.RWMutex
}

// NewStrategy creates a new trading strategy
func NewStrategy(analyzer *analyzer.Analyzer, log *logger.Logger) *Strategy {
	return &Strategy{
		analyzer:    analyzer,
		logger:      log,
		activeTrade: types.NewTradeData(),
	}
}

// GenerateSignal generates trading signals based on market conditions
func (s *Strategy) GenerateSignal(price float64, timestamp time.Time, metrics *types.MarketMetrics) *types.Signal {
	s.mutex.Lock()
	defer s.mutex.Unlock()
	
	// Check if we have an active trade
	if s.activeTrade.Active {
		return s.checkExitConditions(price, timestamp, metrics)
	} else {
		return s.checkEntryConditions(price, timestamp, metrics)
	}
}

// checkEntryConditions checks for entry conditions based on market metrics
func (s *Strategy) checkEntryConditions(price float64, timestamp time.Time, metrics *types.MarketMetrics) *types.Signal {
	// Check buy conditions
	if s.checkBuyConditions(metrics) {
		s.logger.Info("Buy conditions met")
		
		// Create active trade
		s.activeTrade.Active = true
		s.activeTrade.Direction = "buy"
		s.activeTrade.EntryPrice = price
		s.activeTrade.EntryTime = timestamp
		s.activeTrade.HighestPrice = price
		s.activeTrade.LowestPrice = price
		
		// Generate buy signal
		return types.NewBuySignal(price, timestamp, metrics)
	}
	
	return nil
}

// checkExitConditions checks for exit conditions for an active trade
func (s *Strategy) checkExitConditions(price float64, timestamp time.Time, metrics *types.MarketMetrics) *types.Signal {
	// Update highest and lowest prices
	if price > s.activeTrade.HighestPrice {
		s.activeTrade.HighestPrice = price
	}
	if price < s.activeTrade.LowestPrice {
		s.activeTrade.LowestPrice = price
	}
	
	// Check sell conditions
	stopTriggered, reason, stopLoss, profit := s.checkSellConditions(
		s.activeTrade.EntryTime,
		s.activeTrade.EntryPrice,
		s.activeTrade.HighestPrice,
		price,
		timestamp,
		metrics,
	)
	
	if stopTriggered {
		s.logger.Info("Sell conditions met: " + reason)
		
		// Generate sell signal
		signal := types.NewSellSignal(price, timestamp, reason, profit*100, stopLoss)
		
		// Reset active trade
		s.activeTrade.Active = false
		
		return signal
	}
	
	return nil
}

// checkBuyConditions checks if buy conditions are met
func (s *Strategy) checkBuyConditions(metrics *types.MarketMetrics) bool {
	// Default thresholds
	thresholds := map[string]float64{
		"realized_volatility_hi": 0.70,
		"realized_volatility_lo": 0.35,
		"relative_strength_hi":   0.75,
		"relative_strength_lo":   0.25,
		"trend_strength":         5.0,
		"avg_trend_strength":     3.0,
		"order_imbalance":        0.65,
		"market_efficiency_ratio": 0.93,
	}
	
	// Check all conditions
	return (
		metrics.RealizedVolatility <= thresholds["realized_volatility_hi"] &&
		metrics.RealizedVolatility >= thresholds["realized_volatility_lo"] &&
		metrics.RelativeStrength <= thresholds["relative_strength_hi"] &&
		metrics.RelativeStrength >= thresholds["relative_strength_lo"] &&
		metrics.TrendStrength >= thresholds["trend_strength"] &&
		metrics.AvgTrendStrength >= thresholds["avg_trend_strength"] &&
		metrics.TrendStrength > metrics.AvgTrendStrength &&
		metrics.OrderImbalance >= thresholds["order_imbalance"] &&
		metrics.MarketEfficiencyRatio >= thresholds["market_efficiency_ratio"]
	)
}

// checkSellConditions checks if sell conditions are met
func (s *Strategy) checkSellConditions(
	entryTime time.Time,
	entryPrice float64,
	highestPrice float64,
	currentPrice float64,
	timestamp time.Time,
	metrics *types.MarketMetrics,
) (bool, string, float64, float64) {
	// Constants for exit conditions
	trailingStopActivation := 1.0  // Percentage gain to activate trailing stop
	profitTargetMultiplier := 2.5  // Profit target as multiple of risk
	trailingStopDistance := 1.5    // Trailing stop distance factor
	trendStrengthThreshold := -7.0 // Trend strength threshold for exit
	minProfit := 0.3               // Minimum profit percentage for time-based exit
	
	// Calculate current profit percentage
	profit := (currentPrice / entryPrice - 1)
	stopTriggered := false
	reason := ""
	
	// Calculate stop loss and take profit levels
	atr := metrics.ATR
	if atr < currentPrice*0.001 {
		atr = currentPrice * 0.001 // Use minimum 0.1% ATR
	}
	
	stopDistance := trailingStopDistance * atr
	profitDistance := stopDistance * profitTargetMultiplier
	
	// For long trades: stop below entry, target above entry
	stopLoss := currentPrice - stopDistance
	takeProfit := currentPrice + profitDistance
	
	// Check stop loss
	if currentPrice <= stopLoss {
		stopTriggered = true
		reason = "stop_loss"
	}
	
	// Check take profit
	if currentPrice >= takeProfit {
		stopTriggered = true
		reason = "take_profit"
	}
	
	// Adjust trailing stop if profit exceeds activation threshold
	activationThreshold := trailingStopActivation / 100
	if profit >= activationThreshold {
		// Calculate trailing stop level
		trailDistance := trailingStopActivation * (metrics.ATR / highestPrice)
		trailLevel := highestPrice * (1 - trailDistance)
		
		// Update stop loss if trailing stop is higher
		if trailLevel > stopLoss {
			stopLoss = trailLevel
			s.logger.Info("Trailing stop updated")
		}
	}
	
	// Check time-based exit
	if !entryTime.IsZero() {
		tradeDuration := timestamp.Sub(entryTime).Hours()
		if tradeDuration > 4 && profit >= minProfit/100 {  // Exit after 4 hours
			stopTriggered = true
			reason = "time_exit"
		}
	}
	
	// Check trend reversal exit
	if metrics.TrendStrength < trendStrengthThreshold && profit >= minProfit/100 {
		stopTriggered = true
		reason = "trend_reversal"
	}
	
	return stopTriggered, reason, stopLoss, profit
}

// IsActiveTrade returns whether there is an active trade
func (s *Strategy) IsActiveTrade() bool {
	s.mutex.RLock()
	defer s.mutex.RUnlock()
	return s.activeTrade.Active
}

// GetActiveTradeData returns data about the active trade
func (s *Strategy) GetActiveTradeData() *types.TradeData {
	s.mutex.RLock()
	defer s.mutex.RUnlock()
	
	// Create a copy of the active trade data
	tradeCopy := &types.TradeData{
		Active:       s.activeTrade.Active,
		Direction:    s.activeTrade.Direction,
		EntryPrice:   s.activeTrade.EntryPrice,
		EntryTime:    s.activeTrade.EntryTime,
		HighestPrice: s.activeTrade.HighestPrice,
		LowestPrice:  s.activeTrade.LowestPrice,
		StopLoss:     s.activeTrade.StopLoss,
	}
	
	// Calculate current PnL if active
	if tradeCopy.Active {
		currentPrice := s.activeTrade.HighestPrice // Use highest price as a proxy for current price
		tradeCopy.CurrentPnL = (currentPrice / s.activeTrade.EntryPrice - 1) * 100
	}
	
	return tradeCopy
}

// UpdateStopLoss updates the stop loss level for the active trade
func (s *Strategy) UpdateStopLoss(newStopLoss float64) {
	s.mutex.Lock()
	defer s.mutex.Unlock()
	
	if s.activeTrade.Active && newStopLoss > 0 {
		s.activeTrade.StopLoss = newStopLoss
	}
}