package logger

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"TRADE/pkg/types"
)

// LogLevel defines the severity of log messages
type LogLevel int

const (
	// Log levels
	DEBUG LogLevel = iota
	INFO
	WARNING
	ERROR
	CRITICAL
)

// Logger provides logging functionality with different severity levels
type Logger struct {
	logFile    *os.File
	logger     *log.Logger
	level      LogLevel
	mutex      sync.Mutex
	statusChan chan string
	statusDone chan struct{}
}

// NewLogger creates a new logger instance
func NewLogger() *Logger {
	// Create logs directory if it doesn't exist
	logsDir := "logs"
	if _, err := os.Stat(logsDir); os.IsNotExist(err) {
		os.Mkdir(logsDir, 0755)
	}

	// Create log file with timestamp in name
	timestamp := time.Now().Format("2006-01-02_15-04-05")
	logPath := filepath.Join(logsDir, fmt.Sprintf("trade_%s.log", timestamp))
	
	file, err := os.Create(logPath)
	if err != nil {
		log.Printf("Failed to create log file: %v", err)
		return &Logger{
			logger:     log.New(os.Stdout, "", log.LstdFlags),
			level:      INFO,
			statusChan: make(chan string, 10),
			statusDone: make(chan struct{}),
		}
	}
	
	// Create logger with file and stdout output
	logger := log.New(file, "", log.LstdFlags)
	
	// Start status reporter
	l := &Logger{
		logFile:    file,
		logger:     logger,
		level:      INFO,
		statusChan: make(chan string, 10),
		statusDone: make(chan struct{}),
	}
	
	go l.statusReporter()
	
	l.Info("Logger initialized")
	return l
}

// statusReporter prints status updates to the console
func (l *Logger) statusReporter() {
	for {
		select {
		case status := <-l.statusChan:
			fmt.Println(status)
		case <-l.statusDone:
			return
		}
	}
}

// SetLevel sets the minimum log level
func (l *Logger) SetLevel(level LogLevel) {
	l.mutex.Lock()
	defer l.mutex.Unlock()
	l.level = level
}

// log writes a log message with the specified level
func (l *Logger) log(level LogLevel, message string) {
	l.mutex.Lock()
	defer l.mutex.Unlock()
	
	if level < l.level {
		return
	}
	
	levelStr := "INFO"
	switch level {
	case DEBUG:
		levelStr = "DEBUG"
	case INFO:
		levelStr = "INFO"
	case WARNING:
		levelStr = "WARNING"
	case ERROR:
		levelStr = "ERROR"
	case CRITICAL:
		levelStr = "CRITICAL"
	}
	
	logMessage := fmt.Sprintf("[%s] %s", levelStr, message)
	l.logger.Println(logMessage)
	
	// Also print to stdout for ERROR and CRITICAL
	if level >= ERROR {
		log.Println(logMessage)
	}
}

// Debug logs a debug message
func (l *Logger) Debug(message string) {
	l.log(DEBUG, message)
}

// Info logs an info message
func (l *Logger) Info(message string) {
	l.log(INFO, message)
}

// Warning logs a warning message
func (l *Logger) Warning(message string) {
	l.log(WARNING, message)
}

// Error logs an error message
func (l *Logger) Error(message string) {
	l.log(ERROR, message)
}

// Critical logs a critical message
func (l *Logger) Critical(message string) {
	l.log(CRITICAL, message)
}

// ReportStatus sends a status update to the console
func (l *Logger) ReportStatus(status string) {
	select {
	case l.statusChan <- status:
		// Status sent
	default:
		// Channel full, log it instead
		l.Info(fmt.Sprintf("Status update: %s", status))
	}
}

// ReportMarketStatus reports the current market status
func (l *Logger) ReportMarketStatus(price float64, metrics *types.MarketMetrics, tradeActive bool, tradePnL float64) {
	// Format market status message
	var statusMsg string
	
	if tradeActive {
		statusMsg = fmt.Sprintf(
			"\n=== MARKET STATUS ===\n"+
			"Price: %.6f | Vol: %.2f%% | RS: %.2f\n"+
			"Trend: %.2f | Order Imb: %.2f | MER: %.2f\n"+
			"Active Trade | Current PnL: %.2f%%\n"+
			"=====================",
			price,
			metrics.RealizedVolatility,
			metrics.RelativeStrength,
			metrics.TrendStrength,
			metrics.OrderImbalance,
			metrics.MarketEfficiencyRatio,
			tradePnL,
		)
	} else {
		statusMsg = fmt.Sprintf(
			"\n=== MARKET STATUS ===\n"+
			"Price: %.6f | Vol: %.2f%% | RS: %.2f\n"+
			"Trend: %.2f | Order Imb: %.2f | MER: %.2f\n"+
			"No Active Trade\n"+
			"=====================",
			price,
			metrics.RealizedVolatility,
			metrics.RelativeStrength,
			metrics.TrendStrength,
			metrics.OrderImbalance,
			metrics.MarketEfficiencyRatio,
		)
	}
	
	l.ReportStatus(statusMsg)
}

// Close closes the logger and its resources
func (l *Logger) Close() {
	// Signal status reporter to stop
	close(l.statusDone)
	
	// Close log file
	if l.logFile != nil {
		l.logFile.Close()
	}
}