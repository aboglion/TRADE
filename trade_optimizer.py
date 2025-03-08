#!/usr/bin/env python3
"""
Trading Condition Optimizer

This tool allows you to select a historical data file and optimize trading conditions
to find the best parameters for maximum profit.
"""

import os
import sys
import time
import json
import itertools
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Tuple

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import from the TRADE package
from TRADE.utils.config import TradingConfig
from TRADE.analysis.market_analyzer import MarketAnalyzer
from TRADE.connectivity.simulator import MarketSimulator
from TRADE.storage.data_storage import DataStorage
from TRADE.utils.logger import LoggerSetup

class TradingOptimizer:
    """Optimizer for trading conditions using historical data"""
    
    def __init__(self):
        """Initialize the optimizer"""
        self.logger = LoggerSetup.get_logger()
        self.data_storage = DataStorage()
        self.best_results = []
        self.progress = 0.0  # Progress tracking (0.0 to 1.0)
        self.parameter_ranges = {
            'BUY': {
                'volatility_threshold': np.arange(0.1, 0.6, 0.1),
                'relative_strength_threshold': np.arange(0.1, 0.6, 0.1),
                'trend_strength': np.arange(2, 10, 1),
                'order_imbalance': np.arange(0.1, 0.5, 0.1),
                'market_efficiency_ratio': np.arange(0.5, 1.5, 0.1)
            },
            'EXIT': {
                'profit_target_multiplier': np.arange(1.5, 4.0, 0.5),
                'trailing_stop_act_ivation': np.arange(0.5, 2.0, 0.5),
                'trailing_stop_distance': np.arange(1.0, 3.0, 0.5)
            }
        }
        
    def select_dataset(self) -> str:
        """
        Display available datasets and let the user select one
        
        Returns:
            Selected dataset filename
        """
        print("\nAvailable historical datasets:")
        datasets = self.data_storage.get_available_datasets()
        
        if not datasets:
            print("No datasets found in data/ directory")
            print("Record some live data first using 'live' mode")
            sys.exit(1)
            
        for idx, dataset in enumerate(datasets, 1):
            print(f"{idx}. {dataset}")
            
        while True:
            try:
                selection = int(input("\nSelect dataset to use (number): "))
                if 1 <= selection <= len(datasets):
                    return datasets[selection-1]
                print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a valid number.")
    
    def run_backtest(self, dataset: str, params: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        """
        Run a backtest with specific parameters
        
        Args:
            dataset: Dataset filename
            params: Trading parameters to use
            
        Returns:
            Performance metrics from the backtest
        """
        # Save original parameters
        original_params = {
            'BUY': TradingConfig.MARKET_CONDITIONS['BUY'].copy(),
            'EXIT': TradingConfig.MARKET_CONDITIONS['EXIT'].copy()
        }
        
        # Set new parameters
        TradingConfig.MARKET_CONDITIONS['BUY'].update(params['BUY'])
        TradingConfig.MARKET_CONDITIONS['EXIT'].update(params['EXIT'])
        
        # Initialize market analyzer
        market_analyzer = MarketAnalyzer(
            warmup_ticks=TradingConfig.DEFAULT_WARMUP_TICKS,
            dynamic_window=TradingConfig.DEFAULT_DYNAMIC_WINDOW,
            risk_factor=TradingConfig.DEFAULT_RISK_FACTOR,
            adaptive_sizing=TradingConfig.DEFAULT_ADAPTIVE_ActiveTrade_SIZING
        )
        
        # Initialize simulator
        simulator = MarketSimulator(market_analyzer.process_tick)
        if not simulator.load_dataset(dataset):
            self.logger.error(f"Failed to load dataset: {dataset}")
            return {}
            
        # Run simulation
        simulator.start()
        
        # Wait for simulation to complete
        while simulator.running:
            time.sleep(0.01)
            
        # Get performance metrics
        performance = market_analyzer.trade_manager.get_performance_metrics()
        
        # Restore original parameters
        TradingConfig.MARKET_CONDITIONS['BUY'] = original_params['BUY']
        TradingConfig.MARKET_CONDITIONS['EXIT'] = original_params['EXIT']
        
        return performance
    
    def generate_parameter_combinations(self, max_combinations: int = 100) -> List[Dict[str, Dict[str, float]]]:
        """
        Generate parameter combinations for optimization
        
        Args:
            max_combinations: Maximum number of combinations to generate
            
        Returns:
            List of parameter combinations
        """
        # Create lists of parameter values
        buy_params = list(itertools.product(
            self.parameter_ranges['BUY']['volatility_threshold'],
            self.parameter_ranges['BUY']['relative_strength_threshold'],
            self.parameter_ranges['BUY']['trend_strength'],
            self.parameter_ranges['BUY']['order_imbalance'],
            self.parameter_ranges['BUY']['market_efficiency_ratio']
        ))
        
        exit_params = list(itertools.product(
            self.parameter_ranges['EXIT']['profit_target_multiplier'],
            self.parameter_ranges['EXIT']['trailing_stop_act_ivation'],
            self.parameter_ranges['EXIT']['trailing_stop_distance']
        ))
        
        # Sample if too many combinations
        if len(buy_params) * len(exit_params) > max_combinations:
            buy_indices = np.random.choice(len(buy_params), size=int(np.sqrt(max_combinations)), replace=False)
            exit_indices = np.random.choice(len(exit_params), size=int(np.sqrt(max_combinations)), replace=False)
            buy_params = [buy_params[i] for i in buy_indices]
            exit_params = [exit_params[i] for i in exit_indices]
        
        # Create parameter combinations
        combinations = []
        for buy_param in buy_params:
            for exit_param in exit_params:
                param_dict = {
                    'BUY': {
                        'volatility_threshold': float(buy_param[0]),
                        'relative_strength_threshold': float(buy_param[1]),
                        'trend_strength': float(buy_param[2]),
                        'order_imbalance': float(buy_param[3]),
                        'market_efficiency_ratio': float(buy_param[4])
                    },
                    'EXIT': {
                        'profit_target_multiplier': float(exit_param[0]),
                        'trailing_stop_act_ivation': float(exit_param[1]),
                        'trailing_stop_distance': float(exit_param[2])
                    }
                }
                combinations.append(param_dict)
                
                # Limit to max_combinations
                if len(combinations) >= max_combinations:
                    break
            if len(combinations) >= max_combinations:
                break
                
        return combinations
    
    def optimize(self, dataset: str, max_combinations: int = 100) -> List[Dict[str, Any]]:
        """
        Run optimization to find the best trading parameters
        
        Args:
            dataset: Dataset filename
            max_combinations: Maximum number of parameter combinations to test
            
        Returns:
            List of best parameter combinations and their performance
        """
        print(f"\nOptimizing trading conditions for {dataset}")
        print(f"Testing up to {max_combinations} parameter combinations...")
        
        # Reset progress
        self.progress = 0.0
        
        # Generate parameter combinations
        combinations = self.generate_parameter_combinations(max_combinations)
        total_combinations = len(combinations)
        
        # Run backtests
        results = []
        for i, params in enumerate(combinations):
            # Update progress
            self.progress = i / total_combinations
            
            print(f"\rTesting combination {i+1}/{total_combinations}...", end="", flush=True)
            
            # Run backtest
            performance = self.run_backtest(dataset, params)
            
            # Skip if no trades
            if performance.get('total_trades', 0) < 5:
                continue
                
            # Store results
            results.append({
                'parameters': params,
                'performance': performance
            })
            
        # Set progress to complete
        self.progress = 1.0
        print("\nOptimization complete!")
        
        # Sort by profit factor
        results.sort(key=lambda x: (
            x['performance'].get('profit_factor', 0) * 
            x['performance'].get('win_rate', 0) * 
            x['performance'].get('total_trades', 0) / 100
        ), reverse=True)
        
        # Return top results
        self.best_results = results[:10]
        return self.best_results
    
    def display_results(self) -> None:
        """Display optimization results"""
        if not self.best_results:
            print("No optimization results available.")
            return
            
        print("\n" + "="*80)
        print("OPTIMIZATION RESULTS".center(80))
        print("="*80)
        
        for i, result in enumerate(self.best_results[:5], 1):
            params = result['parameters']
            perf = result['performance']
            
            print(f"\nRank #{i}:")
            print(f"  Win Rate: {perf.get('win_rate', 0)*100:.2f}%")
            print(f"  Profit Factor: {perf.get('profit_factor', 0):.2f}")
            print(f"  Total Trades: {perf.get('total_trades', 0)}")
            print(f"  Avg Win: {perf.get('avg_win', 0):.2f}%")
            print(f"  Avg Loss: {perf.get('avg_loss', 0):.2f}%")
            print(f"  Max Drawdown: {perf.get('max_drawdown', 0):.2f}%")
            print(f"  Sharpe Ratio: {perf.get('sharpe_ratio', 0):.2f}")
            
            print("\n  BUY CONDITIONS:")
            for key, value in params['BUY'].items():
                print(f"    {key}: {value}")
                
            print("\n  EXIT CONDITIONS:")
            for key, value in params['EXIT'].items():
                print(f"    {key}: {value}")
                
            print("-"*80)
    
    def save_results(self, filename: str = None) -> None:
        """
        Save optimization results to a file
        
        Args:
            filename: Output filename (default: optimization_results_TIMESTAMP.json)
        """
        if not self.best_results:
            print("No optimization results available to save.")
            return
            
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"optimization_results_{timestamp}.json"
            
        try:
            with open(filename, 'w', encoding='utf-8') as file:
                json.dump(self.best_results, file, indent=2, default=str)
            print(f"\nResults saved to {filename}")
        except Exception as e:
            print(f"Error saving results: {str(e)}")
    
    def apply_parameters(self, params: Dict[str, Dict[str, float]]) -> None:
        """
        Apply optimized parameters to the trading configuration
        
        Args:
            params: Parameter dictionary to apply
        """
        # Update configuration
        TradingConfig.MARKET_CONDITIONS['BUY'].update(params['BUY'])
        TradingConfig.MARKET_CONDITIONS['EXIT'].update(params['EXIT'])
        
        # Save to file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"optimized_config_{timestamp}.json"
        TradingConfig.save_to_file(filename)
        
        print(f"\nOptimized parameters applied and saved to {filename}")
        print("You can use this configuration file with the main trading system.")

def main():
    """Main entry point for the optimizer"""
    print("\n" + "="*80)
    print("TRADING CONDITION OPTIMIZER".center(80))
    print("="*80)
    print("\nThis tool will help you find the optimal trading conditions for maximum profit.")
    
    # Initialize optimizer
    optimizer = TradingOptimizer()
    
    # Select dataset
    dataset = optimizer.select_dataset()
    
    # Ask for number of combinations to test
    max_combinations = 100
    try:
        user_input = input("\nEnter maximum number of parameter combinations to test [100]: ")
        if user_input.strip():
            max_combinations = int(user_input)
    except ValueError:
        print("Invalid input. Using default value of 100.")
    
    # Run optimization
    results = optimizer.optimize(dataset, max_combinations)
    
    # Display results
    optimizer.display_results()
    
    # Save results
    save_option = input("\nDo you want to save the optimization results? (y/n): ").lower()
    if save_option == 'y':
        optimizer.save_results()
    
    # Apply best parameters
    apply_option = input("\nDo you want to apply the best parameters to your trading configuration? (y/n): ").lower()
    if apply_option == 'y':
        optimizer.apply_parameters(results[0]['parameters'])
    
    print("\nOptimization complete. Thank you for using the Trading Condition Optimizer!")

if __name__ == "__main__":
    main()