# Trading Condition Optimizer

This tool allows you to select historical data files and optimize trading conditions to find the best parameters for maximum profit. It works with the existing TRADE system without modifying the original code.

## Features

- Select historical data files for optimization
- Test multiple parameter combinations to find optimal trading conditions
- Display detailed performance metrics for the best parameter sets
- Save optimization results for future reference
- Apply optimized parameters to your trading configuration
- Available in both command-line and GUI versions

## Files

- `trade_optimizer.py` - Command-line version of the optimizer
- `trade_optimizer_gui.py` - Graphical user interface version of the optimizer

## Requirements

The optimizer uses the existing TRADE system code and requires the following Python packages:
- numpy
- pandas
- scipy
- tkinter (for the GUI version)

## How to Use

### Command-Line Version

1. Run the optimizer script:
   ```
   python trade_optimizer.py
   ```

2. Select a historical data file from the list by entering its number.

3. Enter the maximum number of parameter combinations to test (default: 100).

4. Wait for the optimization process to complete. This may take some time depending on the size of the dataset and the number of parameter combinations.

5. Review the optimization results, which show the top-performing parameter sets ranked by profit factor, win rate, and number of trades.

6. Choose whether to save the optimization results to a file.

7. Choose whether to apply the best parameters to your trading configuration.

### GUI Version

1. Run the GUI script:
   ```
   python trade_optimizer_gui.py
   ```

2. The application will display a list of available historical data files. Select one from the list.

3. Set the maximum number of parameter combinations to test.

4. Click the "Start Optimization" button to begin the optimization process.

5. The progress bar will show the optimization progress.

6. Once complete, the results will be displayed in the text area, showing the top-performing parameter sets.

7. Use the "Save Results" button to save the optimization results to a JSON file.

8. Use the "Apply Best Parameters" button to apply the best parameters to your trading configuration.

## Optimization Parameters

The optimizer tests various combinations of the following parameters:

### Buy Conditions
- `volatility_threshold` - Minimum volatility required for entry
- `relative_strength_threshold` - Minimum relative strength required for entry
- `trend_strength` - Minimum trend strength required for entry
- `order_imbalance` - Minimum order imbalance required for entry
- `market_efficiency_ratio` - Minimum market efficiency ratio required for entry

### Exit Conditions
- `profit_target_multiplier` - Profit target as a multiple of risk
- `trailing_stop_act_ivation` - Profit percentage required to activate trailing stop
- `trailing_stop_distance` - Distance of trailing stop as a percentage of price

## Performance Metrics

The optimizer evaluates parameter combinations based on the following metrics:

- **Win Rate** - Percentage of winning trades
- **Profit Factor** - Ratio of gross profits to gross losses
- **Total Trades** - Number of trades executed
- **Average Win** - Average profit percentage of winning trades
- **Average Loss** - Average loss percentage of losing trades
- **Maximum Drawdown** - Maximum peak-to-trough decline in equity
- **Sharpe Ratio** - Risk-adjusted return metric

## Output Files

- **Optimization Results** - JSON file containing detailed results of the optimization process
- **Optimized Configuration** - JSON file containing the best trading parameters that can be loaded by the main trading system

## Tips for Effective Optimization

1. **Start with a representative dataset** - Choose historical data that represents the market conditions you want to trade in.

2. **Balance between overfitting and underfitting** - Testing too many parameters may lead to overfitting, while testing too few may not capture the optimal trading conditions.

3. **Consider multiple metrics** - Don't focus solely on profit factor or win rate. Consider the number of trades, drawdown, and Sharpe ratio as well.

4. **Validate results** - After optimization, validate the results on a different dataset to ensure the parameters are robust.

5. **Start with fewer combinations** - Begin with a smaller number of combinations (e.g., 50-100) to get quick results, then increase if needed.