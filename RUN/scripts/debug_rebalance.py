import sys
sys.path.insert(0, '/home/uns/TRADE/RUN')
from src.services.portfolio_service import PortfolioService
from src.core.models import PortfolioSnapshot, AssetHolding, TargetAllocation, OrderType, Regime
import logging

logging.basicConfig(level=logging.DEBUG)

class DummyGateway:
    def get_market_info(self, symbol):
        raise Exception("Mock")
        
port_svc = PortfolioService(gateway=DummyGateway(), deviation_threshold=0.03, allow_market_orders=True)

ah = AssetHolding(symbol="USDT", free=4572.88, locked=0, total=4572.88, value_usd=4572.88)

port = PortfolioSnapshot(
    holdings={"USDT": ah},
    total_value_usd=4572.88,
    timestamp_ms=1000
)

target = TargetAllocation(
    timestamp_ms=1000,
    regime=Regime.BULL,
    weights={"USDT": 0.70, "BTC/USDT": 0.0, "ETH/USDT": 0.0, "SOL/USDT": 0.30}
)

prices = {"BTC/USDT": 114180.0, "ETH/USDT": 4000.0, "SOL/USDT": 226.82}

plan = port_svc.compute_rebalance_plan(portfolio=port, target=target, prices=prices)

print(plan.orders)
