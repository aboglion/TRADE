"""
Helper script to view current portfolio holdings and valuations.

Usage:
  python scripts/show_balances.py
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import load_dotenv
from src.config.config_manager import ConfigManager
from src.core.enums import RunMode
from src.exchanges.dry_run_exchange import DryRunExchange
from src.exchanges.exchange_gateway import ExchangeGateway
from src.services.portfolio_service import PortfolioService
from src.services.state_store import JsonStateStore


def main():
    load_dotenv()
    cm = ConfigManager("config.yaml" if Path("config.yaml").exists() else None)
    config = cm.load()

    if config.run_mode == RunMode.DRY_RUN:
        gateway = DryRunExchange(initial_balances={"USDT": 1000.0})
    else:
        gateway = ExchangeGateway(config.exchange, config.run_mode)
        gateway.initialize()

    portfolio_service = PortfolioService(gateway)
    snapshot = portfolio_service.get_portfolio()

    print("=" * 60)
    print(f" PORTFOLIO BALANCES ({config.run_mode.name})")
    print("=" * 60)
    print(f"{'Asset':<10} {'Free':<15} {'Locked':<15} {'Total':<15} {'USD Value':<15} {'Weight':<10}")
    print("-" * 80)

    for asset, h in snapshot.holdings.items():
        weight = (h.value_usd / snapshot.total_value_usd * 100) if snapshot.total_value_usd > 0 else 0
        print(f"{asset:<10} {h.free:<15.6f} {h.locked:<15.6f} {h.total:<15.6f} ${h.value_usd:<14.2f} {weight:<9.2f}%")

    print("-" * 80)
    print(f"TOTAL PORTFOLIO VALUE: ${snapshot.total_value_usd:.2f}")
    print("=" * 60)

    state_store = JsonStateStore(config.state.path)
    state = state_store.load_state()
    print(f"Last Regime: {state.last_regime or 'N/A'}")
    print(f"Pending Orders: {len(state.pending_orders)}")
    print(f"Completed Orders: {len(state.completed_orders)}")


if __name__ == "__main__":
    main()
