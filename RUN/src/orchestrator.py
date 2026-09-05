"""
Bot orchestrator — the main cycle logic.

Each cycle:
1. Reconcile state vs exchange
2. Check for new closed candles
3. Fetch full history
4. Validate data continuity
5. Get current portfolio
6. Compute strategy signals
7. Compute rebalance plan
8. Risk-check each order
9. Execute approved orders
10. Save state

The orchestrator owns the cycle but delegates all real work
to services — it's a thin coordinator.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.config.config_manager import BotConfig
from src.core.enums import RunMode
from src.core.exceptions import (
    DataGapError,
    InsufficientDataError,
    KillSwitchActiveError,
    SafeStopRequired,
)
from src.core.models import BotState
from src.data.candle_service import CandleService
from src.services.order_manager import OrderManager
from src.services.portfolio_service import PortfolioService
from src.services.reconciliation_service import ReconciliationService
from src.services.risk_manager import RiskManager
from src.services.state_store import JsonStateStore
from src.strategy.regime_adaptive_strategy import RegimeAdaptiveStrategy
from src.utils.time_utils import SystemClock, ms_to_iso

logger = logging.getLogger("bot.orchestrator")


class BotOrchestrator:
    """
    Main trading bot coordinator.

    Ties together all services and runs the trading cycle.
    """

    def __init__(
        self,
        config: BotConfig,
        gateway,
        candle_service: CandleService,
        portfolio_service: PortfolioService,
        strategy: RegimeAdaptiveStrategy,
        risk_manager: RiskManager,
        state_store: JsonStateStore,
        state: BotState,
        clock: Optional[SystemClock] = None,
    ):
        self._config = config
        self._gateway = gateway
        self._candle_service = candle_service
        self._portfolio_service = portfolio_service
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._state_store = state_store
        self._state = state
        self._clock = clock or SystemClock()

        self._order_manager = OrderManager(gateway, state, config.run_mode)
        self._reconciliation = ReconciliationService(gateway, state)

        self._consecutive_errors = 0

    def clear_critical_errors(self) -> None:
        """Clear critical errors both in memory and persist state."""
        self._state.critical_errors.clear()
        self._consecutive_errors = 0
        self._save_state(success=True)

    def run_once(self) -> bool:
        """
        Execute a single trading cycle.

        Returns True if the cycle completed successfully (even if no
        trades were needed), False if there was a recoverable error.

        Raises SafeStopRequired for unrecoverable errors.
        """
        cycle_start = time.time()
        now_ms = self._clock.now_ms()

        logger.info("=" * 60)
        logger.info("CYCLE START | %s | Mode: %s", ms_to_iso(now_ms), self._config.run_mode.name)

        try:
            # 0. Kill switch check
            if self._risk_manager.is_kill_switch_active():
                logger.critical("KILL SWITCH ACTIVE — no trading")
                raise KillSwitchActiveError()

            # 1. Reconcile state vs exchange
            self._reconciliation.reconcile()

            # 2. Check pending orders from previous cycles
            self._order_manager.check_pending_orders()

            # Check for open orders on exchange
            open_orders = self._order_manager.check_open_orders_on_exchange()
            if open_orders:
                logger.warning(
                    "Open orders detected on exchange — attempting stale order cleanup..."
                )
                canceled = self._order_manager.cancel_stale_open_orders()
                if canceled < len(open_orders):
                    logger.warning(
                        "Some open orders could not be canceled (%d/%d remain open). "
                        "Skipping trading cycle until resolved.",
                        len(open_orders) - canceled,
                        len(open_orders),
                    )
                    self._save_state(success=True)
                    return True

            # 3. Check for new closed candles
            assets = self._config.strategy.assets
            pairs = {name: cfg.pair for name, cfg in assets.items()}

            has_new_candles = False
            new_candles_by_pair: Dict[str, list] = {}

            for asset_name, pair in pairs.items():
                last_ts = self._state.last_processed_candle_ts.get(pair)
                new_candles = self._candle_service.get_new_closed_candles(
                    symbol=pair,
                    last_processed_ts=last_ts,
                    now_ms=now_ms,
                )
                if new_candles:
                    has_new_candles = True
                    new_candles_by_pair[pair] = new_candles

            if not has_new_candles:
                logger.info("No new closed candles — cycle idle")
                self._save_state(success=True)
                return True

            # 4. Fetch full history for indicator computation
            candles_by_asset: Dict[str, list] = {}
            for asset_name, pair in pairs.items():
                latest_new = new_candles_by_pair.get(pair, [])
                if not latest_new:
                    # Even if no NEW candles for this asset, we need its history
                    last_ts = self._state.last_processed_candle_ts.get(pair)
                    up_to_ts = last_ts or now_ms
                else:
                    up_to_ts = latest_new[-1].timestamp_ms

                try:
                    full_history = self._candle_service.get_full_history(
                        symbol=pair,
                        up_to_ts=up_to_ts,
                        min_candles=self._config.strategy.warmup_candles,
                    )
                    candles_by_asset[pair] = full_history
                except InsufficientDataError as e:
                    logger.error("Insufficient data for %s: %s", pair, e)
                    self._save_state(success=False)
                    return False

            # 5. Validate data continuity
            for pair, candles in candles_by_asset.items():
                try:
                    self._candle_service.validate_continuity(candles)
                except DataGapError as e:
                    logger.error("Data gap in %s: %s — halting cycle", pair, e)
                    self._state.critical_errors.append(
                        f"Data gap in {pair}: {e}"
                    )
                    self._save_state(success=False)
                    return False

            # 6. Get current prices and portfolio
            prices = {}
            for pair, candles in candles_by_asset.items():
                if candles:
                    prices[pair] = candles[-1].close

            portfolio = self._portfolio_service.get_portfolio(prices=prices)

            # 7. Compute strategy signals
            if hasattr(self._strategy, "import_state"):
                self._strategy.import_state(self._state.strategy_state)

            decision = self._strategy.compute_signals(
                candles_by_asset=candles_by_asset,
                portfolio=portfolio,
            )

            if hasattr(self._strategy, "export_state"):
                self._state.strategy_state.update(self._strategy.export_state())

            self._state.last_regime = decision.regime.value

            # 8. Compute rebalance plan
            plan = self._portfolio_service.compute_rebalance_plan(
                portfolio=portfolio,
                target=decision.target_allocation,
                prices=prices,
            )

            # 9. Risk-check and execute each order
            self._risk_manager.reset_cycle()
            executed_count = 0

            for intent in plan.orders:
                approved, reason = self._risk_manager.approve_order(
                    intent, portfolio
                )
                if not approved:
                    logger.warning("Order rejected: %s", reason)
                    continue

                try:
                    result = self._order_manager.execute(intent)
                    executed_count += 1
                    logger.info(
                        "Order executed: %s %s %.8f — %s",
                        intent.side.value,
                        intent.symbol,
                        intent.amount,
                        result.status.value,
                    )
                except Exception as e:
                    logger.error("Order execution failed: %s", e)
                    # Don't halt the cycle — continue with remaining orders

            # 10. Update last processed candle timestamps
            for pair, candles in new_candles_by_pair.items():
                if candles:
                    self._state.last_processed_candle_ts[pair] = (
                        candles[-1].timestamp_ms
                    )

            # Save state
            self._save_state(success=True)
            self._consecutive_errors = 0

            elapsed = time.time() - cycle_start
            logger.info(
                "CYCLE COMPLETE | %.1fs | Regime: %s | Orders: %d/%d executed",
                elapsed,
                decision.regime.value,
                executed_count,
                len(plan.orders),
            )
            logger.info("=" * 60)

            return True

        except KillSwitchActiveError:
            self._save_state(success=True)
            return True

        except SafeStopRequired:
            self._save_state(success=False)
            raise

        except Exception as e:
            self._consecutive_errors += 1
            logger.error(
                "Cycle error (%d consecutive): %s",
                self._consecutive_errors, e,
                exc_info=True,
            )
            self._state.critical_errors.append(
                f"Cycle error: {type(e).__name__}: {e}"
            )
            self._save_state(success=False)

            if self._consecutive_errors >= self._config.scheduler.max_consecutive_errors:
                logger.critical(
                    "Too many consecutive errors (%d) — stopping",
                    self._consecutive_errors,
                )
                raise SafeStopRequired(
                    f"Too many consecutive errors: {self._consecutive_errors}"
                )

            return False

    def run_loop(self) -> None:
        """
        Main loop — runs cycles at the configured interval.

        Handles graceful shutdown via KeyboardInterrupt.
        """
        interval = self._config.scheduler.poll_interval_seconds

        logger.info(
            "Starting bot loop | Mode: %s | Interval: %ds",
            self._config.run_mode.name,
            interval,
        )

        try:
            while True:
                try:
                    self.run_once()
                except SafeStopRequired:
                    logger.critical("Safe stop required — exiting loop")
                    break

                logger.debug("Sleeping %ds until next cycle...", interval)
                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Shutdown requested (Ctrl+C) — saving state...")
            self._save_state(success=True)
            logger.info("Clean shutdown complete")

    def _save_state(self, success: bool) -> None:
        """Save current state to disk."""
        self._state.last_run_ts = self._clock.now_ms()
        self._state.last_cycle_success = success
        try:
            self._state_store.save_state(self._state)
        except Exception as e:
            logger.error("CRITICAL: Failed to save state: %s", e)
