package types

import (
	"time"
)

// MarketMetrics contains all calculated market metrics
type MarketMetrics struct {
	RealizedVolatility   float64
	ATR                  float64
	RelativeStrength     float64
	OrderImbalance       float64
	TrendStrength        float64
	AvgTrendStrength     float64
	MarketEfficiencyRatio float64
}

// NewMarketMetrics creates a new MarketMetrics with default values
func NewMarketMetrics() *MarketMetrics {
	return &MarketMetrics{
		RealizedVolatility:   0.0,
		ATR:                  0.0,
		RelativeStrength:     0.5,
		OrderImbalance:       0.5,
		TrendStrength:        0.0,
		AvgTrendStrength:     0.0,
		MarketEfficiencyRatio: 0.0,
	}
}

// TickData represents a single market tick
type TickData struct {
	Price     float64
	Volume    float64
	IsAsk     bool
	Timestamp time.Time
}

// TradeData represents an active trade
type TradeData struct {
	Active       bool
	Direction    string
	EntryPrice   float64
	EntryTime    time.Time
	HighestPrice float64
	LowestPrice  float64
	StopLoss     float64
	CurrentPnL   float64
}

// NewTradeData creates a new TradeData with default values
func NewTradeData() *TradeData {
	return &TradeData{
		Active: false,
	}
}

// Signal represents a trading signal
type Signal struct {
	Action          string
	Side            string
	Price           float64
	Time            time.Time
	Reason          string
	ProfitPercent   float64
	UpdatedStopLoss float64
	Metrics         *MarketMetrics
}

// NewBuySignal creates a new buy signal
func NewBuySignal(price float64, timestamp time.Time, metrics *MarketMetrics) *Signal {
	return &Signal{
		Action:  "BUY",
		Side:    "buy",
		Price:   price,
		Time:    timestamp,
		Metrics: metrics,
	}
}

// NewSellSignal creates a new sell signal
func NewSellSignal(price float64, timestamp time.Time, reason string, profitPercent float64, stopLoss float64) *Signal {
	return &Signal{
		Action:          "CLOSE",
		Price:           price,
		Time:            timestamp,
		Reason:          reason,
		ProfitPercent:   profitPercent,
		UpdatedStopLoss: stopLoss,
	}
}

// MarketState represents the current state of the market
type MarketState struct {
	Timestamp    time.Time
	CurrentPrice float64
	Metrics      *MarketMetrics
	ActiveTrade  *TradeData
	Performance  *PerformanceMetrics
}

// PerformanceMetrics represents trading performance statistics
type PerformanceMetrics struct {
	TotalTrades  int
	WinningTrades int
	LosingTrades int
	WinRate      float64
	AveragePnL   float64
	TotalPnL     float64
	MaxDrawdown  float64
}

// NewPerformanceMetrics creates a new PerformanceMetrics with default values
func NewPerformanceMetrics() *PerformanceMetrics {
	return &PerformanceMetrics{
		TotalTrades:  0,
		WinningTrades: 0,
		LosingTrades: 0,
		WinRate:      0.0,
		AveragePnL:   0.0,
		TotalPnL:     0.0,
		MaxDrawdown:  0.0,
	}
}