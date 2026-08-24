package analyzer

import (
	"math"
	"sync"
	"time"

	"github.com/montanaflynn/stats"
	"TRADE/pkg/logger"
	"TRADE/pkg/market"
	"TRADE/pkg/types"
)

// Analyzer calculates and analyzes market metrics
type Analyzer struct {
	market          *market.MarketData
	logger          *logger.Logger
	metrics         *types.MarketMetrics
	trendStrengthWindow []float64
	warmupTicks     int
	warmupComplete  bool
	mutex           sync.RWMutex
}

// NewAnalyzer creates a new market analyzer
func NewAnalyzer(marketData *market.MarketData, log *logger.Logger) *Analyzer {
	return &Analyzer{
		market:          marketData,
		logger:          log,
		metrics:         types.NewMarketMetrics(),
		trendStrengthWindow: make([]float64, 0, 20),
		warmupTicks:     300, // Default warmup period
		warmupComplete:  false,
	}
}

// SetWarmupTicks sets the number of ticks required before analysis starts
func (a *Analyzer) SetWarmupTicks(ticks int) {
	a.mutex.Lock()
	defer a.mutex.Unlock()
	a.warmupTicks = ticks
}

// HasSufficientData checks if we have enough data for analysis
func (a *Analyzer) HasSufficientData() bool {
	return a.warmupComplete
}

// ProcessTick processes a new market tick and updates metrics
func (a *Analyzer) ProcessTick(tick *types.TickData) *types.MarketMetrics {
	// Check if we have minimum data for analysis
	if !a.market.HasMinimumData(20) {
		return nil
	}
	
	// Calculate metrics
	a.calculateMetrics()
	
	// Check if warmup is complete
	if !a.warmupComplete && a.market.HasMinimumData(a.warmupTicks) {
		a.mutex.Lock()
		a.warmupComplete = true
		a.mutex.Unlock()
		a.logger.Info("Warmup phase completed")
	}
	
	// Return a copy of the metrics
	return a.GetMetrics()
}

// GetMetrics returns a copy of the current metrics
func (a *Analyzer) GetMetrics() *types.MarketMetrics {
	a.mutex.RLock()
	defer a.mutex.RUnlock()
	
	// Create a copy of the metrics
	metricsCopy := &types.MarketMetrics{
		RealizedVolatility:   a.metrics.RealizedVolatility,
		ATR:                  a.metrics.ATR,
		RelativeStrength:     a.metrics.RelativeStrength,
		OrderImbalance:       a.metrics.OrderImbalance,
		TrendStrength:        a.metrics.TrendStrength,
		AvgTrendStrength:     a.metrics.AvgTrendStrength,
		MarketEfficiencyRatio: a.metrics.MarketEfficiencyRatio,
	}
	
	return metricsCopy
}

// calculateMetrics calculates all market metrics
func (a *Analyzer) calculateMetrics() {
	a.mutex.Lock()
	defer a.mutex.Unlock()
	
	// Get price and volume data
	prices := a.market.GetPriceArray()
	if len(prices) < 2 {
		return
	}
	
	// Calculate returns
	returns := make([]float64, len(prices)-1)
	for i := 1; i < len(prices); i++ {
		returns[i-1] = (prices[i] / prices[i-1]) - 1
	}
	
	// Calculate realized volatility
	stdDev, _ := stats.StandardDeviation(returns)
	realizedVolatility := stdDev * math.Sqrt(252*1440) * 100
	
	// Calculate ATR (Average True Range)
	atr := a.calculateATR(prices)
	
	// Calculate relative strength
	relativeStrength := a.calculateRelativeStrength(returns)
	
	// Calculate order imbalance
	orderImbalance := a.calculateOrderImbalance()
	
	// Calculate trend strength
	trendStrength := a.calculateTrendStrength(prices)
	
	// Update trend strength window
	if len(a.trendStrengthWindow) >= 20 {
		a.trendStrengthWindow = a.trendStrengthWindow[1:]
	}
	a.trendStrengthWindow = append(a.trendStrengthWindow, trendStrength)
	
	// Calculate average trend strength
	avgTrendStrength := 0.0
	if len(a.trendStrengthWindow) >= 7 {
		sum := 0.0
		for _, v := range a.trendStrengthWindow {
			sum += v
		}
		avgTrendStrength = sum / float64(len(a.trendStrengthWindow))
	}
	
	// Calculate market efficiency ratio
	mer := a.calculateMarketEfficiencyRatio(prices)
	
	// Update metrics
	a.metrics.RealizedVolatility = realizedVolatility
	a.metrics.ATR = atr
	a.metrics.RelativeStrength = relativeStrength
	a.metrics.OrderImbalance = orderImbalance
	a.metrics.TrendStrength = trendStrength
	a.metrics.AvgTrendStrength = avgTrendStrength
	a.metrics.MarketEfficiencyRatio = mer
}

// calculateATR calculates the Average True Range
func (a *Analyzer) calculateATR(prices []float64) float64 {
	highPrices := a.market.GetHighPricesArray()
	lowPrices := a.market.GetLowPricesArray()
	
	if len(highPrices) < 14 || len(lowPrices) < 14 || len(prices) < 14 {
		// Not enough data, use volatility as a proxy
		if len(prices) > 0 {
			return a.metrics.RealizedVolatility * prices[len(prices)-1] / 100
		}
		return 0
	}
	
	// Use the last 14 periods for ATR calculation
	period := 14
	highPrices = highPrices[len(highPrices)-period:]
	lowPrices = lowPrices[len(lowPrices)-period:]
	closes := prices[len(prices)-period-1 : len(prices)-1]
	
	// Calculate true ranges
	trueRanges := make([]float64, period)
	for i := 0; i < period; i++ {
		// True Range is the greatest of:
		// 1. Current High - Current Low
		// 2. |Current High - Previous Close|
		// 3. |Current Low - Previous Close|
		tr1 := highPrices[i] - lowPrices[i]
		tr2 := math.Abs(highPrices[i] - closes[i])
		tr3 := math.Abs(lowPrices[i] - closes[i])
		
		trueRanges[i] = math.Max(tr1, math.Max(tr2, tr3))
	}
	
	// Calculate average
	sum := 0.0
	for _, tr := range trueRanges {
		sum += tr
	}
	
	return sum / float64(period)
}

// calculateRelativeStrength calculates the Relative Strength
func (a *Analyzer) calculateRelativeStrength(returns []float64) float64 {
	if len(returns) < 2 {
		return 0.5
	}
	
	// Use up to 500 most recent returns
	window := int(math.Min(500, float64(len(returns))))
	windowReturns := returns[len(returns)-window:]
	
	// Calculate gains and losses
	gains := 0.0
	losses := 0.0
	
	for _, ret := range windowReturns {
		if ret > 0 {
			gains += ret
		} else {
			losses -= ret
		}
	}
	
	// Calculate RS
	if gains+losses == 0 {
		return 0.5
	}
	
	return gains / (gains + losses)
}

// calculateOrderImbalance calculates the order imbalance
func (a *Analyzer) calculateOrderImbalance() float64 {
	bidVolume := a.market.GetBidVolumeArray()
	askVolume := a.market.GetAskVolumeArray()
	
	totalBidVol := 0.0
	for _, vol := range bidVolume {
		totalBidVol += vol
	}
	
	totalAskVol := 0.0
	for _, vol := range askVolume {
		totalAskVol += vol
	}
	
	if totalBidVol+totalAskVol == 0 {
		return 0.5
	}
	
	return totalBidVol / (totalBidVol + totalAskVol)
}

// calculateTrendStrength calculates the trend strength using linear regression
func (a *Analyzer) calculateTrendStrength(prices []float64) float64 {
	if len(prices) < 30 {
		return 0.0
	}
	
	// Use last 30 prices for trend calculation
	windowPrices := prices[len(prices)-30:]
	x := make([]float64, len(windowPrices))
	for i := range x {
		x[i] = float64(i)
	}
	
	// Calculate linear regression
	slope, intercept, r := linearRegression(x, windowPrices)
	
	// Scale slope by r-squared and price level
	meanPrice, _ := stats.Mean(windowPrices)
	trendStrength := slope * r * r * (30 / meanPrice) * 100000
	
	return trendStrength
}

// calculateMarketEfficiencyRatio calculates the Market Efficiency Ratio
func (a *Analyzer) calculateMarketEfficiencyRatio(prices []float64) float64 {
	if len(prices) < 30 {
		return 0.5
	}
	
	// Net directional movement
	netMovement := math.Abs(prices[len(prices)-1] - prices[len(prices)-30])
	
	// Total price path length
	pathLength := 0.0
	for i := len(prices) - 29; i < len(prices); i++ {
		pathLength += math.Abs(prices[i] - prices[i-1])
	}
	
	// Calculate MER
	if pathLength == 0 {
		return 0.5
	}
	
	return netMovement / pathLength
}

// linearRegression calculates linear regression parameters
func linearRegression(x, y []float64) (slope, intercept, r float64) {
	n := float64(len(x))
	
	if n != float64(len(y)) || n < 2 {
		return 0, 0, 0
	}
	
	sumX, sumY := 0.0, 0.0
	sumXY, sumXX := 0.0, 0.0
	sumYY := 0.0
	
	for i := 0; i < len(x); i++ {
		sumX += x[i]
		sumY += y[i]
		sumXY += x[i] * y[i]
		sumXX += x[i] * x[i]
		sumYY += y[i] * y[i]
	}
	
	// Calculate slope and intercept
	slope = (n*sumXY - sumX*sumY) / (n*sumXX - sumX*sumX)
	intercept = (sumY - slope*sumX) / n
	
	// Calculate correlation coefficient
	numerator := n*sumXY - sumX*sumY
	denominator := math.Sqrt((n*sumXX - sumX*sumX) * (n*sumYY - sumY*sumY))
	
	if denominator == 0 {
		r = 0
	} else {
		r = numerator / denominator
	}
	
	return slope, intercept, r
}