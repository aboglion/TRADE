#!/bin/bash

# Script to run the TRADE trading system

# Function to display help
show_help() {
    echo "TRADE Trading System"
    echo "Usage: ./run.sh [options]"
    echo ""
    echo "Options:"
    echo "  --live      Run in live trading mode (default)"
    echo "  --backtest  Run in backtest mode"
    echo "  --help      Show this help message"
    echo ""
}

# Default mode
MODE="live"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --live)
            MODE="live"
            shift
            ;;
        --backtest)
            MODE="backtest"
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Check if Go is installed
if ! command -v go &> /dev/null; then
    echo "Error: Go is not installed or not in PATH"
    echo "Please install Go from https://golang.org/dl/"
    exit 1
fi

# Download dependencies if needed
echo "Checking dependencies..."
go mod download

# Build the application
echo "Building TRADE..."
go build -o TRADE cmd/main.go

# Run the application
echo "Starting TRADE in $MODE mode..."
./TRADE --mode=$MODE