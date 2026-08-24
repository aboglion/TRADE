package market

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"TRADE/pkg/logger"
	"TRADE/pkg/types"
)

// TickCallback is a function that gets called when new market data is received
type TickCallback func(tick *types.TickData)

// MarketData handles market data acquisition and storage
type MarketData struct {
	// Data storage
	priceHistory []float64
	volumeHistory []float64
	bidVolume []float64
	askVolume []float64
	timeStamps []time.Time
	highPrices []float64
	lowPrices []float64
	
	// Configuration
	maxSize int
	roundNum int
	prevPrice float64
	
	// Websocket connection for live data
	wsConn *websocket.Conn
	wsActive bool
	symbols []string
	
	// Callback for new data
	tickCallback TickCallback
	
	// Utilities
	logger *logger.Logger
	mutex sync.RWMutex
}

// NewMarketData creates a new market data handler
func NewMarketData(log *logger.Logger) *MarketData {
	return &MarketData{
		priceHistory: make([]float64, 0, 1000),
		volumeHistory: make([]float64, 0, 1000),
		bidVolume: make([]float64, 0, 1000),
		askVolume: make([]float64, 0, 1000),
		timeStamps: make([]time.Time, 0, 1000),
		highPrices: make([]float64, 0, 1000),
		lowPrices: make([]float64, 0, 1000),
		maxSize: 1000,
		wsActive: false,
		logger: log,
	}
}

// SetTickCallback sets the callback function for new market data
func (md *MarketData) SetTickCallback(callback TickCallback) {
	md.mutex.Lock()
	defer md.mutex.Unlock()
	md.tickCallback = callback
}

// AddTick adds a new tick to the market data
func (md *MarketData) AddTick(tick *types.TickData) {
	md.mutex.Lock()
	defer md.mutex.Unlock()
	
	price := tick.Price
	volume := tick.Volume
	isAsk := tick.IsAsk
	timestamp := tick.Timestamp
	
	// Determine rounding precision if not set
	if md.roundNum == 0 {
		priceStr := fmt.Sprintf("%f", price)
		parts := strings.Split(priceStr, ".")
		if len(parts) > 1 {
			md.roundNum = 6 - len(parts[0])
			md.roundNum = int(math.Max(1, math.Min(8, float64(md.roundNum))))
		} else {
			md.roundNum = 2
		}
		md.prevPrice = md.round(price)
	}
	
	// Round price to appropriate precision
	price = md.round(price)
	
	// Add data to histories with capacity management
	md.addToLimitedSlice(&md.priceHistory, price)
	md.addToLimitedSlice(&md.volumeHistory, volume)
	md.addToLimitedSlice(&md.timeStamps, timestamp)
	
	// Update high and low prices
	if len(md.highPrices) == 0 || price > md.highPrices[len(md.highPrices)-1] {
		md.addToLimitedSlice(&md.highPrices, price)
	} else {
		md.addToLimitedSlice(&md.highPrices, md.highPrices[len(md.highPrices)-1])
	}
	
	if len(md.lowPrices) == 0 || price < md.lowPrices[len(md.lowPrices)-1] {
		md.addToLimitedSlice(&md.lowPrices, price)
	} else {
		md.addToLimitedSlice(&md.lowPrices, md.lowPrices[len(md.lowPrices)-1])
	}
	
	// Update volume data
	if isAsk {
		md.addToLimitedSlice(&md.askVolume, volume)
	} else {
		md.addToLimitedSlice(&md.bidVolume, volume)
	}
	
	// Call the callback if set
	if md.tickCallback != nil {
		md.tickCallback(tick)
	}
}

// Helper method to add to a slice with capacity management
func (md *MarketData) addToLimitedSlice(slice interface{}, value interface{}) {
	switch s := slice.(type) {
	case *[]float64:
		if len(*s) >= md.maxSize {
			*s = append((*s)[1:], value.(float64))
		} else {
			*s = append(*s, value.(float64))
		}
	case *[]time.Time:
		if len(*s) >= md.maxSize {
			*s = append((*s)[1:], value.(time.Time))
		} else {
			*s = append(*s, value.(time.Time))
		}
	}
}

// Helper function to round a float to the current precision
func (md *MarketData) round(num float64) float64 {
	shift := math.Pow(10, float64(md.roundNum))
	return math.Round(num*shift) / shift
}

// GetCurrentPrice returns the most recent price
func (md *MarketData) GetCurrentPrice() float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	if len(md.priceHistory) == 0 {
		return 0
	}
	return md.priceHistory[len(md.priceHistory)-1]
}

// GetPriceArray returns the price history as a slice
func (md *MarketData) GetPriceArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.priceHistory))
	copy(result, md.priceHistory)
	return result
}

// GetVolumeArray returns the volume history as a slice
func (md *MarketData) GetVolumeArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.volumeHistory))
	copy(result, md.volumeHistory)
	return result
}

// GetBidVolumeArray returns the bid volume history as a slice
func (md *MarketData) GetBidVolumeArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.bidVolume))
	copy(result, md.bidVolume)
	return result
}

// GetAskVolumeArray returns the ask volume history as a slice
func (md *MarketData) GetAskVolumeArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.askVolume))
	copy(result, md.askVolume)
	return result
}

// GetHighPricesArray returns the high prices history as a slice
func (md *MarketData) GetHighPricesArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.highPrices))
	copy(result, md.highPrices)
	return result
}

// GetLowPricesArray returns the low prices history as a slice
func (md *MarketData) GetLowPricesArray() []float64 {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	result := make([]float64, len(md.lowPrices))
	copy(result, md.lowPrices)
	return result
}

// HasMinimumData checks if we have enough data for analysis
func (md *MarketData) HasMinimumData(minTicks int) bool {
	md.mutex.RLock()
	defer md.mutex.RUnlock()
	
	return len(md.priceHistory) >= minTicks
}

// Reset clears all market data
func (md *MarketData) Reset() {
	md.mutex.Lock()
	defer md.mutex.Unlock()
	
	md.priceHistory = md.priceHistory[:0]
	md.volumeHistory = md.volumeHistory[:0]
	md.bidVolume = md.bidVolume[:0]
	md.askVolume = md.askVolume[:0]
	md.timeStamps = md.timeStamps[:0]
	md.highPrices = md.highPrices[:0]
	md.lowPrices = md.lowPrices[:0]
	md.prevPrice = 0
	md.roundNum = 0
}

// ConnectLive connects to live market data via WebSocket
func (md *MarketData) ConnectLive(symbols []string) error {
	md.mutex.Lock()
	defer md.mutex.Unlock()
	
	if md.wsActive {
		return fmt.Errorf("already connected to market data")
	}
	
	md.symbols = symbols
	
	// Start WebSocket connection in a goroutine
	go md.startWebSocketConnection()
	
	return nil
}

// startWebSocketConnection establishes and maintains the WebSocket connection
func (md *MarketData) startWebSocketConnection() {
	if len(md.symbols) == 0 {
		md.logger.Error("No symbols specified for WebSocket connection")
		return
	}
	
	symbol := md.symbols[0]
	url := fmt.Sprintf("wss://stream.binance.com:9443/ws/%s@trade", strings.ToLower(symbol))
	
	md.logger.Info(fmt.Sprintf("Connecting to %s", url))
	
	// Connect to WebSocket
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		md.logger.Error(fmt.Sprintf("WebSocket connection error: %v", err))
		return
	}
	
	md.mutex.Lock()
	md.wsConn = conn
	md.wsActive = true
	md.mutex.Unlock()
	
	md.logger.Info("WebSocket connection established")
	
	// Handle incoming messages
	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			md.logger.Error(fmt.Sprintf("WebSocket read error: %v", err))
			break
		}
		
		// Parse message
		var data map[string]interface{}
		if err := json.Unmarshal(message, &data); err != nil {
			md.logger.Error(fmt.Sprintf("JSON parse error: %v", err))
			continue
		}
		
		// Extract and normalize data
		price, _ := data["p"].(string)
		quantity, _ := data["q"].(string)
		isMaker, _ := data["m"].(bool)
		timestampMs, _ := data["T"].(float64)
		
		// Convert to appropriate types
		priceFloat, err := strconv.ParseFloat(price, 64)
		if err != nil {
			md.logger.Error(fmt.Sprintf("Price parse error: %v", err))
			continue
		}
		
		quantityFloat, err := strconv.ParseFloat(quantity, 64)
		if err != nil {
			md.logger.Error(fmt.Sprintf("Quantity parse error: %v", err))
			continue
		}
		
		timestamp := time.Unix(0, int64(timestampMs)*int64(time.Millisecond))
		
		// Create tick data
		tick := &types.TickData{
			Price:     priceFloat,
			Volume:    quantityFloat,
			IsAsk:     !isMaker,
			Timestamp: timestamp,
		}
		
		// Add tick to market data
		md.AddTick(tick)
	}
	
	// Clean up
	md.mutex.Lock()
	md.wsConn = nil
	md.wsActive = false
	md.mutex.Unlock()
	
	md.logger.Info("WebSocket connection closed")
}

// Disconnect closes the WebSocket connection
func (md *MarketData) Disconnect() {
	md.mutex.Lock()
	defer md.mutex.Unlock()
	
	if md.wsConn != nil {
		md.wsConn.Close()
		md.wsConn = nil
	}
	
	md.wsActive = false
}

// GetAvailableDatasets returns a list of available historical datasets
func (md *MarketData) GetAvailableDatasets() ([]string, error) {
	dataDir := "data"
	
	// Check if data directory exists
	if _, err := os.Stat(dataDir); os.IsNotExist(err) {
		return nil, fmt.Errorf("data directory does not exist")
	}
	
	// Find all CSV files in the data directory
	files, err := ioutil.ReadDir(dataDir)
	if err != nil {
		return nil, err
	}
	
	var datasets []string
	for _, file := range files {
		if !file.IsDir() && strings.HasSuffix(file.Name(), ".csv") {
			datasets = append(datasets, filepath.Join(dataDir, file.Name()))
		}
	}
	
	return datasets, nil
}

// LoadHistoricalData loads and processes historical data from a CSV file
func (md *MarketData) LoadHistoricalData(filePath string) error {
	md.logger.Info(fmt.Sprintf("Loading historical data from %s", filePath))
	
	// Reset current data
	md.Reset()
	
	// Open the CSV file
	file, err := os.Open(filePath)
	if err != nil {
		return fmt.Errorf("failed to open file: %v", err)
	}
	defer file.Close()
	
	// Create a CSV reader
	reader := csv.NewReader(file)
	
	// Read the header
	header, err := reader.Read()
	if err != nil {
		return fmt.Errorf("failed to read header: %v", err)
	}
	
	// Find column indices
	timestampIdx, priceIdx, volumeIdx, isAskIdx := -1, -1, -1, -1
	for i, col := range header {
		switch strings.ToLower(col) {
		case "timestamp":
			timestampIdx = i
		case "price":
			priceIdx = i
		case "volume":
			volumeIdx = i
		case "is_ask":
			isAskIdx = i
		}
	}
	
	// Check if all required columns are found
	if timestampIdx == -1 || priceIdx == -1 || volumeIdx == -1 || isAskIdx == -1 {
		return fmt.Errorf("missing required columns in CSV file")
	}
	
	// Read and process each row
	lineCount := 0
	for {
		row, err := reader.Read()
		if err != nil {
			break // End of file or error
		}
		
		// Parse values
		timestamp, err := time.Parse(time.RFC3339, row[timestampIdx])
		if err != nil {
			md.logger.Warning(fmt.Sprintf("Invalid timestamp format: %s", row[timestampIdx]))
			continue
		}
		
		price, err := strconv.ParseFloat(row[priceIdx], 64)
		if err != nil {
			md.logger.Warning(fmt.Sprintf("Invalid price: %s", row[priceIdx]))
			continue
		}
		
		volume, err := strconv.ParseFloat(row[volumeIdx], 64)
		if err != nil {
			md.logger.Warning(fmt.Sprintf("Invalid volume: %s", row[volumeIdx]))
			continue
		}
		
		isAsk, err := strconv.ParseBool(row[isAskIdx])
		if err != nil {
			md.logger.Warning(fmt.Sprintf("Invalid is_ask value: %s", row[isAskIdx]))
			continue
		}
		
		// Create tick data
		tick := &types.TickData{
			Price:     price,
			Volume:    volume,
			IsAsk:     isAsk,
			Timestamp: timestamp,
		}
		
		// Add tick to market data
		md.AddTick(tick)
		lineCount++
	}
	
	md.logger.Info(fmt.Sprintf("Loaded %d historical data points", lineCount))
	return nil
}