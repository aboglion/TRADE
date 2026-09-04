"""
Helper script to test Binance connection and API credentials.

Usage:
  python scripts/check_connection.py
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import load_dotenv
from src.config.config_manager import ConfigManager
from src.core.enums import RunMode
from src.exchanges.exchange_gateway import ExchangeGateway
from src.utils.logging_utils import setup_logger

logger = setup_logger("scripts.check_connection")


def main():
    load_dotenv()
    cm = ConfigManager("config.yaml" if Path("config.yaml").exists() else None)
    config = cm.load()

    logger.info("Testing connection to Binance (%s mode)...", config.run_mode.name)

    if config.run_mode == RunMode.DRY_RUN:
        logger.info("DRY_RUN mode active. Skipping live API check.")
        return

    try:
        gateway = ExchangeGateway(config.exchange, config.run_mode)
        gateway.initialize()

        ticker = gateway.fetch_ticker_price("BTC/USDT")
        logger.info("SUCCESS! BTC/USDT price from Binance: $%.2f", ticker)

        balance = gateway.fetch_balance()
        logger.info("Account balance snapshot:")
        for asset, data in balance.items():
            if data["total"] > 0:
                logger.info("  %s: free=%.8f, locked=%.8f, total=%.8f", asset, data["free"], data["used"], data["total"])

    except Exception as e:
        logger.error("Connection failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
