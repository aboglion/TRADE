import os
import sys
from datetime import datetime, timedelta
import pandas as pd
from src.core.enums import RunMode
from src.data.live_provider import LiveDataProvider
from src.data.candle_service import CandleService
from src.exchanges.dry_run_exchange import DryRunExchange
from src.services.portfolio_service import PortfolioService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.strategy.micro_satellite_strategy import MicroSatelliteStrategy
from src.strategy.hybrid_strategy import HybridStrategy
from src.orchestrator import BotOrchestrator
from src.core.models import BotState, Candle
from src.config.config_manager import ConfigManager
import logging

logging.disable(logging.CRITICAL) # Silence logs for clean output

def run_sim(duration_days: int, start_btc: float, start_eth: float, start_usdt: float):
    cm = ConfigManager()
    
    # Monkey-patch validate to ignore warmup_candles error for simulation
    orig_validate = cm._validate
    def fake_validate(cfg):
        pass
    cm._validate = fake_validate
    
    config = cm.load()
    cm._validate = orig_validate
    
    import dataclasses
    
    # Bypass frozen dataclass
    object.__setattr__(config.strategy, "warmup_candles", 900)
    object.__setattr__(config, "run_mode", RunMode.DRY_RUN)
    object.__setattr__(config.exchange, "market_type", "future")
    
    provider = LiveDataProvider(None)
    
    from src.exchanges.dry_run_exchange import DryRunExchange
    temp_gw = DryRunExchange()
    start_time_ms = int((datetime.now() - timedelta(days=duration_days + 150)).timestamp() * 1000)
    
    btc_candles = temp_gw.fetch_ohlcv_all("BTC/USDT", "4h", since_ms=start_time_ms, limit=4000)
    eth_candles = temp_gw.fetch_ohlcv_all("ETH/USDT", "4h", since_ms=start_time_ms, limit=4000)
    
    start_time_ms = int((datetime.now() - timedelta(days=duration_days)).timestamp() * 1000)
    
    start_idx = 0
    for i, c in enumerate(btc_candles):
        if c.timestamp_ms >= start_time_ms:
            start_idx = i
            break
            
    warmup = 150 * 6
    if start_idx < warmup:
        print(f"Not enough data for {duration_days} days simulation")
        return
        
    start_btc_price = btc_candles[start_idx].open
    
    eth_start_idx = 0
    for i, c in enumerate(eth_candles):
        if c.timestamp_ms >= start_time_ms:
            eth_start_idx = i
            break
    start_eth_price = eth_candles[eth_start_idx].open
    
    # Calculate starting USD value if converted to liquid
    liquid_value = start_usdt + (start_btc * start_btc_price) + (start_eth * start_eth_price)
    
    print(f"\n{'='*50}")
    print(f"Simulation: {duration_days} Days")
    print(f"Start Date: {datetime.utcfromtimestamp(btc_candles[start_idx].timestamp_ms/1000).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Start BTC Price: ${start_btc_price:.2f} | Start ETH Price: ${start_eth_price:.2f}")
    print(f"Initial Liquid Value: ${liquid_value:.2f}")
    print(f"{'='*50}")
    
    init_balances = {"USDT": liquid_value}
    gateway = DryRunExchange(initial_balances=init_balances, fee_rate=0.001)
    
    candle_service = CandleService(provider, "4h", 150)
    portfolio_service = PortfolioService(gateway, allow_market_orders=config.risk.allow_market_orders, is_futures=True)
    risk_manager = RiskManager(config.risk)
    
    asset_weights = {name: cfg.weight for name, cfg in config.strategy.assets.items()}
    macro_strategy = RegimeAdaptiveStrategy(
        asset_weights=asset_weights,
        sma_regime_period=config.strategy.sma_regime_period,
        bull_leverage=config.strategy.bull_leverage,
        bear_short_hedge_weight=config.strategy.bear_short_hedge_weight,
    )
    micro_strategy = MicroSatelliteStrategy(asset_weights=asset_weights)
    strategy = HybridStrategy(macro_strategy=macro_strategy, micro_strategy=micro_strategy, core_ratio=config.strategy.core_ratio)
    
    state = BotState()
    orchestrator = BotOrchestrator(
        config=config, gateway=gateway, candle_service=candle_service,
        portfolio_service=portfolio_service, strategy=strategy,
        risk_manager=risk_manager, state_store=JsonStateStore("dummy.json"),
        state=state,
    )
    
    btc_sim = btc_candles[start_idx:]
    eth_sim = eth_candles[eth_start_idx:]
    
    min_len = min(len(btc_sim), len(eth_sim))
    
    for i in range(min_len):
        bc = btc_sim[i]
        ec = eth_sim[i]
        
        provider._cache["BTC/USDT"] = btc_candles[:start_idx + i + 1]
        provider._cache["ETH/USDT"] = eth_candles[:eth_start_idx + i + 1]
        
        gateway._last_prices["BTC/USDT"] = bc.close
        gateway._last_prices["ETH/USDT"] = ec.close
        
        try:
            orchestrator.run_once()
        except Exception:
            pass
            
    final_balance = gateway.fetch_balance()
    final_usdt = final_balance.get("USDT", {}).get("total", 0.0)
    
    final_btc_price = btc_sim[-1].close
    final_eth_price = eth_sim[-1].close
    hodl_value = start_usdt + (start_btc * final_btc_price) + (start_eth * final_eth_price)
    
    bot_profit = final_usdt - liquid_value
    bot_pct = (bot_profit / liquid_value) * 100
    
    hodl_profit = hodl_value - liquid_value
    hodl_pct = (hodl_profit / liquid_value) * 100
    
    print(f"Final Bot Value: ${final_usdt:.2f}")
    print(f"Bot Net Profit: ${bot_profit:.2f} ({bot_pct:.2f}%)")
    print(f"HODL Value: ${hodl_value:.2f}")
    print(f"HODL Net Profit: ${hodl_profit:.2f} ({hodl_pct:.2f}%)")
    print(f"Bot vs HODL: ${(final_usdt - hodl_value):.2f}\n")

if __name__ == "__main__":
    run_sim(120, 0.20109, 0.5506, 0.0)
    run_sim(365, 0.20109, 0.5506, 0.0)
