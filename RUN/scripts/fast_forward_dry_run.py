"""
Fast forward DRY RUN for the last 4 months to generate an organic bot_state.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
import logging
import ccxt

from src.config.config_manager import ConfigManager
from src.core.enums import RunMode
from src.core.models import BotState, Candle
from src.core.interfaces import IMarketDataProvider, IClock
from src.exchanges.dry_run_exchange import DryRunExchange
from src.data.candle_service import CandleService
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
from src.strategy.hybrid_strategy import HybridStrategy
from src.orchestrator import BotOrchestrator
from src.utils.logging_utils import setup_logger


class MockClock(IClock):
    def __init__(self, start_ms: int):
        self._now_ms = start_ms

    def now_utc(self) -> datetime:
        return datetime.utcfromtimestamp(self._now_ms / 1000.0)

    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, ms: int):
        self._now_ms += ms

    def current_candle_open_ms(self, timeframe: str) -> int:
        tf_ms = 14_400_000
        return (self._now_ms // tf_ms) * tf_ms

    def is_candle_closed(self, candle_open_ms: int, timeframe: str) -> bool:
        tf_ms = 14_400_000
        return self._now_ms >= (candle_open_ms + tf_ms)


class HistoricalProvider(IMarketDataProvider):
    def __init__(self, data: dict, clock: MockClock):
        self.data = data
        self.clock = clock

    def fetch_candles(self, symbol: str, timeframe: str, since_ms: int = None, limit: int = 500) -> list:
        candles = self.data.get(symbol, [])
        if since_ms:
            candles = [c for c in candles if c.timestamp_ms >= since_ms]
        
        tf_ms = 14_400_000
        valid = []
        for c in candles:
            if c.timestamp_ms + tf_ms <= self.clock.now_ms():
                valid.append(c)
        return valid[-limit:]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", stream=sys.stdout)
    logger = logging.getLogger("fast_forward")
    
    logger.info("Starting 4-month fast forward...")
    
    cm = ConfigManager(str(Path(__file__).parent.parent / "config.yaml"))
    config = cm.load()
    config.run_mode = RunMode.DRY_RUN
    
    state_path = "data/fast_forward_state.json"
    config.state.path = state_path
    state_store = JsonStateStore(state_path)
    state = BotState()
    
    # We need 1000 warmup candles + ~2190 (1 year) candles.
    # 1 year = 365 days. Let's get data from 1000 candles ago (166 days) + 365 days = 531 days ago
    # We will fetch 540 days just to be safe.
    now = datetime.utcnow()
    start_time = now - timedelta(days=540)
    start_ms = int(start_time.timestamp() * 1000)
    
    # We can just instantiate a temporary DryRunExchange to fetch the big history
    temp_gateway = DryRunExchange()
    assets = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    
    historical_data = {}
    for symbol in assets:
        logger.info(f"Fetching data for {symbol}...")
        candles = temp_gateway.fetch_ohlcv_all(symbol, "4h", since_ms=start_ms, limit=4000)
        historical_data[symbol] = candles
        logger.info(f"Fetched {len(candles)} candles for {symbol}")

    if not historical_data["BTC/USDT"]:
        logger.error("No data fetched.")
        return
        
    # Parse args if provided
    duration_days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    start_btc = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20109
    start_eth = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5506
    
    # Start simulation
    sim_start_time = now - timedelta(days=duration_days)
    sim_start_ms = int(sim_start_time.timestamp() * 1000)
    
    # Find the candle closest to sim_start_ms
    start_idx = 0
    for i, c in enumerate(historical_data["BTC/USDT"]):
        if c.timestamp_ms >= sim_start_ms:
            start_idx = i
            break
            
    sim_start_candle = historical_data["BTC/USDT"][start_idx]
    clock = MockClock(sim_start_candle.timestamp_ms + 14_400_000 + 1000)
    
    provider = HistoricalProvider(historical_data, clock)
    
    start_btc_price = historical_data["BTC/USDT"][start_idx].open
    start_eth_price = historical_data["ETH/USDT"][start_idx].open
    start_usdt_value = (start_btc * start_btc_price) + (start_eth * start_eth_price)
    
    logger.info(f"Simulation Start: {duration_days} days ago")
    logger.info(f"Start Prices: BTC=${start_btc_price:.2f}, ETH=${start_eth_price:.2f}")
    logger.info(f"Start Value (Liquid): ${start_usdt_value:.2f}")
    
    # Force original starting balances entirely in USDT
    config.dry_run.initial_balances = {
        "USDT": start_usdt_value,
        "BTC": 0.0,
        "ETH": 0.0,
        "SOL": 0.0
    }
    
    gateway = DryRunExchange(initial_balances=config.dry_run.initial_balances)
    
    def mock_fetch_ticker(symbol: str) -> float:
        candles = provider.fetch_candles(symbol, "4h", limit=1)
        if candles:
            return candles[0].close
        return gateway._last_prices.get(symbol, 0.0)
        
    gateway.fetch_ticker_price = mock_fetch_ticker
    
    # Override risk rules to allow initial rebalance
    config.risk.max_portfolio_change_pct = 1.0
    
    candle_service = CandleService(provider, "4h", 150)
    portfolio_service = PortfolioService(gateway, allow_market_orders=config.risk.allow_market_orders, is_futures=(config.exchange.market_type == "future"))
    risk_manager = RiskManager(config.risk)
    
    asset_weights = {name: cfg.weight for name, cfg in config.strategy.assets.items()}
    macro_strategy = RegimeAdaptiveStrategy(
        asset_weights=asset_weights,
        sma_regime_period=config.strategy.sma_regime_period,
        bull_leverage=config.strategy.bull_leverage,
        bear_short_hedge_weight=config.strategy.bear_short_hedge_weight,
    )
    
    micro_strategy = MicroSatelliteStrategy(
        asset_weights=asset_weights,
    )
    
    strategy = HybridStrategy(
        macro_strategy=macro_strategy,
        micro_strategy=micro_strategy,
        core_ratio=config.strategy.core_ratio,
    )
    
    orchestrator = BotOrchestrator(
        config=config,
        gateway=gateway,
        candle_service=candle_service,
        portfolio_service=portfolio_service,
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        state=state,
        clock=clock
    )
    
    end_ms = historical_data["BTC/USDT"][-1].timestamp_ms + 14_400_000 + 1000
    
    cycle = 1
    total_cycles = ((end_ms - clock.now_ms()) // 14_400_000) + 1
    logger.info(f"Running {total_cycles} cycles...")
    
    while clock.now_ms() <= end_ms:
        for sym in assets:
            c = provider.fetch_candles(sym, "4h", limit=1)
            if c:
                gateway.set_price(sym, c[0].close)
                
        if cycle % 50 == 0:
            logger.info(f"Simulating cycle {cycle}/{total_cycles} at {clock.now_utc()}...")
            
        orchestrator.run_once()
        
        clock.advance(14_400_000)
        cycle += 1

    logger.info("Fast forward complete. State saved to bot_state.json.")
    
    # Save the final dry run balances into state so they persist
    state = state_store.load_state()
    final_balances = gateway.fetch_balance()
    simplified_balances = {k: v["total"] for k, v in final_balances.items() if v["total"] >= 0}
    state.strategy_state["dry_run_balances"] = simplified_balances
    state_store.save_state(state)
    logger.info(f"Final Balances: {simplified_balances}")
    
    # Calculate PnL
    final_usdt = simplified_balances.get("USDT", 0.0)
    
    # Fetch final prices from historical data
    final_btc_price = historical_data["BTC/USDT"][-1].close
    final_eth_price = historical_data["ETH/USDT"][-1].close
    hodl_value = (start_btc * final_btc_price) + (start_eth * final_eth_price)
    
    bot_profit = final_usdt - start_usdt_value
    bot_pct = (bot_profit / start_usdt_value) * 100 if start_usdt_value > 0 else 0
    
    hodl_profit = hodl_value - start_usdt_value
    hodl_pct = (hodl_profit / start_usdt_value) * 100 if start_usdt_value > 0 else 0
    
    print("\n" + "="*50)
    print(f"Simulation: {duration_days} Days")
    print(f"Start Value (Liquid): ${start_usdt_value:.2f}")
    print(f"Final Bot Value: ${final_usdt:.2f}")
    print(f"Bot Net Profit: ${bot_profit:.2f} ({bot_pct:.2f}%)")
    print(f"HODL Value: ${hodl_value:.2f}")
    print(f"HODL Net Profit: ${hodl_profit:.2f} ({hodl_pct:.2f}%)")
    print(f"Bot vs HODL: ${(final_usdt - hodl_value):.2f}")
    print("="*50 + "\n")

if __name__ == '__main__':
    main()
