import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.enums import Regime
from src.core.models import PortfolioSnapshot, TargetAllocation
from src.services.portfolio_service import PortfolioService
from src.exchanges.dry_run_exchange import DryRunExchange
import time

prices = {'BTC/USDT': 60000.0, 'ETH/USDT': 2500.0, 'SOL/USDT': 140.0}
gateway = DryRunExchange(initial_balances={"USDT": 4572.87, "BTC": 0.0, "ETH": 0.0, "SOL": 0.0})
ps = PortfolioService(gateway, allow_market_orders=True)
portfolio = ps.get_portfolio(prices)

target = TargetAllocation(weights={"BTC/USDT": 0.4, "ETH/USDT": 0.3, "SOL/USDT": 0.3}, regime=Regime.BULL, timestamp_ms=int(time.time()*1000))
plan = ps.compute_rebalance_plan(portfolio, target, prices)

print("Orders:")
for o in plan.orders:
    print(f"  {o.side.value} {o.amount} {o.symbol} @ {o.price}")
    res = gateway.create_order(o)
    print(f"    -> {res.status.value}: {res.error_message}")
    
print("Balances after:")
print(gateway.fetch_balance())
