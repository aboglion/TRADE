import ccxt
import time
from datetime import datetime, timedelta

def main():
    ex = ccxt.binance()
    
    # Times
    now = datetime.utcnow()
    start_time = now - timedelta(days=365)
    start_ms = int(start_time.timestamp() * 1000)
    
    symbols = ['BTC/USDT', 'ETH/USDT']
    start_prices = {}
    end_prices = {}
    
    for sym in symbols:
        # Start price (120 days ago)
        c_start = ex.fetch_ohlcv(sym, "4h", since=start_ms, limit=1)
        if c_start:
            start_prices[sym] = c_start[0][4]  # close
            
        # End price (now)
        c_end = ex.fetch_ohlcv(sym, "4h", limit=1)
        if c_end:
            end_prices[sym] = c_end[0][4]
            
    # Initial Balances
    init_btc = 0.0201094
    init_eth = 0.5506405
    init_usdt = 0.0
    
    # Final Balances from our fast forward
    fin_usdt = 421.14769317
    fin_btc = 0.0148827
    fin_eth = 0.5506405
    fin_sol = 0.0
    
    # Start Portfolio Value
    start_val = init_usdt + (init_btc * start_prices['BTC/USDT']) + (init_eth * start_prices['ETH/USDT'])
    
    # Final Portfolio Value
    fin_val = fin_usdt + (fin_btc * end_prices['BTC/USDT']) + (fin_eth * end_prices['ETH/USDT'])
    
    # HODL Value (Initial balances at final prices)
    hodl_val = init_usdt + (init_btc * end_prices['BTC/USDT']) + (init_eth * end_prices['ETH/USDT'])
    
    # The bot sold (0.0201094 - 0.0148827) = 0.0052267 BTC
    # Let's see how much USDT it should have received vs how much it got, to estimate fees.
    btc_sold = init_btc - fin_btc
    # Assuming it sold at around some price... DryRunExchange charges 0.1% fee.
    
    print(f"Start Prices: {start_prices}")
    print(f"End Prices: {end_prices}")
    print(f"Initial Value: ${start_val:.2f}")
    print(f"Final Value: ${fin_val:.2f}")
    print(f"HODL Value: ${hodl_val:.2f}")
    
    bot_profit = fin_val - start_val
    bot_profit_pct = (bot_profit / start_val) * 100
    
    hodl_profit = hodl_val - start_val
    hodl_profit_pct = (hodl_profit / start_val) * 100
    
    print(f"Bot Net Profit: ${bot_profit:.2f} ({bot_profit_pct:.2f}%)")
    print(f"HODL Profit: ${hodl_profit:.2f} ({hodl_profit_pct:.2f}%)")
    print(f"Bot vs HODL: ${(fin_val - hodl_val):.2f}")

if __name__ == '__main__':
    main()
