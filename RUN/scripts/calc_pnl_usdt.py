import ccxt
import time
import json
from datetime import datetime, timedelta

def main():
    ex = ccxt.binance()
    
    # Times
    now = datetime.utcnow()
    start_time = now - timedelta(days=365)
    start_ms = int(start_time.timestamp() * 1000)
    
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    start_prices = {}
    end_prices = {}
    
    for sym in symbols:
        c_start = ex.fetch_ohlcv(sym, "4h", since=start_ms, limit=1)
        if c_start:
            start_prices[sym] = c_start[0][4]
            
        c_end = ex.fetch_ohlcv(sym, "4h", limit=1)
        if c_end:
            end_prices[sym] = c_end[0][4]
            
    # Initial Balances
    init_usdt = 4572.876007
    
    # Final Balances from bot_state.json
    with open("data/fast_forward_state.json") as f:
        state = json.load(f)
        bals = state["strategy_state"].get("dry_run_balances", {})
        fin_usdt = bals.get("USDT", 0.0)
        fin_btc = bals.get("BTC", 0.0)
        fin_eth = bals.get("ETH", 0.0)
        fin_sol = bals.get("SOL", 0.0)
    
    # Start Portfolio Value
    start_val = init_usdt
    
    # Final Portfolio Value
    fin_val = fin_usdt + (fin_btc * end_prices['BTC/USDT']) + (fin_eth * end_prices['ETH/USDT']) + (fin_sol * end_prices.get('SOL/USDT', 0.0))
    
    # HODL Value (if we had bought BTC and ETH with the $4572.87 1 year ago)
    # The user asked what if we started in USDT. If we just HODL'd the USDT, the value would be 4572.87.
    # But usually HODL is compared to holding crypto. Let's compare to HODL of the original crypto portfolio ($4572.87 worth of BTC/ETH)
    hodl_btc = 0.0201094
    hodl_eth = 0.5506405
    hodl_val = (hodl_btc * end_prices['BTC/USDT']) + (hodl_eth * end_prices['ETH/USDT'])
    
    print(f"Final Balances: USDT: {fin_usdt:.2f}, BTC: {fin_btc:.5f}, ETH: {fin_eth:.5f}, SOL: {fin_sol:.5f}")
    print(f"Initial Value: ${start_val:.2f}")
    print(f"Final Value: ${fin_val:.2f}")
    print(f"HODL Value (if held original coins): ${hodl_val:.2f}")
    
    bot_profit = fin_val - start_val
    bot_profit_pct = (bot_profit / start_val) * 100
    
    hodl_profit = hodl_val - start_val
    hodl_profit_pct = (hodl_profit / start_val) * 100
    
    print(f"Bot Net Profit: ${bot_profit:.2f} ({bot_profit_pct:.2f}%)")
    print(f"HODL Profit: ${hodl_profit:.2f} ({hodl_profit_pct:.2f}%)")
    print(f"Bot vs HODL: ${(fin_val - hodl_val):.2f}")

if __name__ == '__main__':
    main()
