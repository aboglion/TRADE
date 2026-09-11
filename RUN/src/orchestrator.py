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
from typing import Any

from src.config.config_manager import BotConfig
from src.core.enums import OrderSide, OrderStatus, Regime
from src.core.exceptions import (
    DataGapError,
    InsufficientDataError,
    KillSwitchActiveError,
    SafeStopRequired,
)
from src.core.models import AssetHolding, BotState
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
        clock: SystemClock | None = None,
        telegram_service: Any | None = None,
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

        if telegram_service is not None:
            self._telegram_service = telegram_service
        elif hasattr(config, "telegram"):
            from src.services.telegram_service import TelegramService
            tg_cfg = config.telegram
            self._telegram_service = TelegramService(
                bot_token=tg_cfg.bot_token,
                chat_id=tg_cfg.chat_id,
                enabled=tg_cfg.enabled,
                dashboard_url=tg_cfg.dashboard_url,
            )
        else:
            self._telegram_service = None

        self._order_manager = OrderManager(
            gateway, state, config.run_mode, telegram_service=self._telegram_service
        )
        self._reconciliation = ReconciliationService(
            gateway, state, telegram_service=self._telegram_service, run_mode=config.run_mode
        )

        self._consecutive_errors = 0
        self._last_scan_time: float | None = None
        import threading
        self._cycle_lock = threading.Lock()

    def clear_critical_errors(self) -> None:
        """Clear critical errors both in memory and persist state."""
        self._state.critical_errors.clear()
        self._consecutive_errors = 0
        self._save_state(success=True)

    def run_once(self, force: bool = False) -> bool:
        """
        Execute a single trading cycle.

        Args:
            force: If True, bypass closed candle check and evaluate strategy on latest available data.

        Returns True if the cycle completed successfully (even if no
        trades were needed), False if there was a recoverable error.

        Raises SafeStopRequired for unrecoverable errors.
        """
        if not self._cycle_lock.acquire(blocking=False):
            logger.warning("Cycle already in progress — skipping concurrent execution.")
            return False

        try:
            return self._run_once_locked(force=force)
        finally:
            self._cycle_lock.release()

    def _run_once_locked(self, force: bool = False) -> bool:
        cycle_start = time.time()
        now_ms = self._clock.now_ms()

        # Timing check: Alert if cycle was triggered in less than 5 minutes (300s)
        # Note: on bot startup (self._last_scan_time is None) or manual forced trigger, do not alert.
        now = time.time()
        if self._last_scan_time is not None and not force:
            elapsed_seconds = now - self._last_scan_time
            expected_interval = getattr(self._config.scheduler, "poll_interval_seconds", 300)
            min_threshold = max(60, expected_interval - 15)  # e.g. 285s
            if elapsed_seconds < min_threshold:
                logger.warning(
                    "⚠️ SCAN TIMING ALERT: Cycle ran after only %.1fs (< 5m)! Expected interval >= %ds. Possible rapid cycle loop or duplicate runner.",
                    elapsed_seconds, expected_interval,
                )
        self._last_scan_time = now

        logger.debug("Routine cycle check | %s | Mode: %s", ms_to_iso(now_ms), self._config.run_mode.name)

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
            new_candles_by_pair: dict[str, list] = {}

            for _asset_name, pair in pairs.items():
                last_ts = self._state.last_processed_candle_ts.get(pair)
                new_candles = self._candle_service.get_new_closed_candles(
                    symbol=pair,
                    last_processed_ts=last_ts,
                    now_ms=now_ms,
                )
                if new_candles:
                    has_new_candles = True
                    new_candles_by_pair[pair] = new_candles

            if not has_new_candles and not force:
                logger.debug("No new closed candles — cycle idle")
                self._save_state(success=True)
                return True

            # Only log cycle banner at INFO when forced; routine cycles log at DEBUG
            if force:
                logger.info("=" * 60)
                logger.info("CYCLE START | %s | Mode: %s | FORCED", ms_to_iso(now_ms), self._config.run_mode.name)
            else:
                logger.debug("CYCLE START | %s | Mode: %s", ms_to_iso(now_ms), self._config.run_mode.name)

            # 4. Fetch full history for indicator computation (synchronizing up_to_ts across all pairs)
            all_latest_ts = []
            for pair in pairs.values():
                latest_new = new_candles_by_pair.get(pair, [])
                if latest_new:
                    all_latest_ts.append(latest_new[-1].timestamp_ms)
                else:
                    last_ts = self._state.last_processed_candle_ts.get(pair)
                    if last_ts:
                        all_latest_ts.append(last_ts)

            target_up_to_ts = max(all_latest_ts) if all_latest_ts else now_ms

            candles_by_asset: dict[str, list] = {}
            for _asset_name, pair in pairs.items():
                try:
                    full_history = self._candle_service.get_full_history(
                        symbol=pair,
                        up_to_ts=target_up_to_ts,
                        min_candles=self._config.strategy.warmup_candles,
                    )
                    candles_by_asset[pair] = full_history
                except InsufficientDataError as e:
                    logger.error("Insufficient data for %s: %s — halting cycle", pair, e)
                    self._state.critical_errors.append(f"Insufficient data for {pair}: {e}")
                    self._save_state(success=False)
                    return False
            # Synchronize candle timelines to common latest closed timestamp across all assets
            if candles_by_asset:
                latest_ts_list = [candles[-1].timestamp_ms for candles in candles_by_asset.values() if candles]
                if latest_ts_list:
                    common_latest_ts = min(latest_ts_list)
                    for pair in list(candles_by_asset.keys()):
                        candles_by_asset[pair] = [c for c in candles_by_asset[pair] if c.timestamp_ms <= common_latest_ts]

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

            # If rebalance plan requires orders and not forced, emit cycle banner at INFO
            if plan.orders and not force:
                logger.info("=" * 60)
                logger.info("CYCLE START | %s | Mode: %s — Action Plan: %d order(s) to execute", ms_to_iso(now_ms), self._config.run_mode.name, len(plan.orders))

            # 9. Dynamic Leverage Sync & Risk-check execute each order
            self._risk_manager.reset_cycle()
            executed_count = 0

            target_lev = decision.metadata.get("effective_leverage", 1.0) if decision.metadata else 1.0
            clamped_lev = max(1.0, float(target_lev))

            # In Bear Regime, the effective_leverage is 0.0 but short hedge needs short_leverage
            is_bear_regime = (decision.regime == Regime.BEAR or getattr(decision.regime, "value", decision.regime) == "bear")
            short_lev = decision.metadata.get("short_leverage", 2.0) if decision.metadata else 2.0

            if hasattr(self._gateway, "set_leverage"):
                for p in pairs.values():
                    try:
                        if is_bear_regime:
                            # In Bear: set short_leverage for the hedge symbol, 1x for others
                            btc_pair = next((v for v in pairs.values() if "BTC" in v), None)
                            lev_to_set = max(1.0, float(short_lev)) if p == btc_pair else 1.0
                        else:
                            lev_to_set = clamped_lev
                        self._gateway.set_leverage(lev_to_set, p)
                    except Exception as ex:
                        logger.warning("Could not sync leverage=%.1fx for %s: %s", lev_to_set, p, ex)

            failed_symbols: set[str] = set()
            for idx, intent in enumerate(plan.orders):
                base_sym = intent.symbol.split("/")[0].split(":")[0]
                if intent.symbol in failed_symbols or base_sym in failed_symbols:
                    logger.warning(
                        "Skipping order for %s (%s) because previous order for this symbol failed in this cycle",
                        intent.symbol, intent.reason,
                    )
                    continue

                if idx > 0:
                    wait_sec = max(0.1, float(getattr(self._config.risk, "min_seconds_between_orders", 1))) + 0.1
                    time.sleep(wait_sec)
                approved, reason = self._risk_manager.approve_order(
                    intent, portfolio
                )
                if not approved:
                    logger.warning("Order rejected: %s", reason)
                    failed_symbols.add(intent.symbol)
                    failed_symbols.add(base_sym)
                    continue

                logger.info(
                    "🎯 ACTION DECISION: Executing %s %s %.8f (reason: %s)",
                    intent.side.value.upper(),
                    intent.symbol,
                    intent.amount,
                    intent.reason,
                )

                try:
                    result = self._order_manager.execute(intent)
                    if result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                        executed_count += 1
                        # Dynamically update in-memory portfolio holdings so subsequent
                        # orders in this cycle have accurate free balance for risk checks
                        filled_qty = result.filled_amount if (result.filled_amount and result.filled_amount > 0) else intent.amount
                        fill_price = result.average_price or intent.price or intent.estimated_price or prices.get(intent.symbol, 0.0)
                        fill_val = filled_qty * fill_price
                        fees_usd = (result.fees or 0.0) * (fill_price if (result.fee_currency and result.fee_currency.upper() == base_sym.upper()) else 1.0)

                        old_base = portfolio.holdings.get(base_sym)
                        if old_base:
                            delta_qty = -filled_qty if intent.side == OrderSide.SELL else filled_qty
                            new_total = old_base.total + delta_qty
                            new_free = max(0.0, old_base.free + delta_qty)
                            new_val = abs(new_total) * fill_price
                            portfolio.holdings[base_sym] = AssetHolding(
                                symbol=base_sym,
                                free=new_free,
                                locked=old_base.locked,
                                total=new_total,
                                value_usd=new_val,
                                unrealized_pnl=old_base.unrealized_pnl,
                                entry_price=old_base.entry_price,
                                leverage=old_base.leverage,
                            )
                        elif intent.side == OrderSide.BUY:
                            portfolio.holdings[base_sym] = AssetHolding(
                                symbol=base_sym,
                                free=filled_qty,
                                locked=0.0,
                                total=filled_qty,
                                value_usd=fill_val,
                            )
                        elif intent.side == OrderSide.SELL:
                            # New short position opened
                            portfolio.holdings[base_sym] = AssetHolding(
                                symbol=base_sym,
                                free=0.0,
                                locked=0.0,
                                total=-filled_qty,
                                value_usd=fill_val,
                            )

                        old_usdt = portfolio.holdings.get("USDT")
                        is_fut = (
                            getattr(self._config.exchange, "market_type", "") == "future"
                            or (old_base and (old_base.leverage > 1.0 or old_base.total < 0))
                            or intent.symbol.endswith(":USDT")
                        )
                        if is_fut:
                            lev = max(1.0, float(old_base.leverage if (old_base and old_base.leverage > 1.0) else 2.0))
                            is_reducing = getattr(intent, "reduce_only", False) or (old_base and (
                                (old_base.total > 0 and intent.side == OrderSide.SELL) or
                                (old_base.total < 0 and intent.side == OrderSide.BUY)
                            ))
                            margin_delta = (fill_val / lev) if is_reducing else -(fill_val / lev)
                            delta_usdt = margin_delta - fees_usd
                        else:
                            delta_usdt = (fill_val - fees_usd) if intent.side == OrderSide.SELL else (-fill_val - fees_usd)

                        if old_usdt:
                            new_usdt_free = max(0.0, old_usdt.free + delta_usdt)
                            new_usdt_total = max(0.0, old_usdt.total + (-fees_usd if is_fut else delta_usdt))
                            portfolio.holdings["USDT"] = AssetHolding(
                                symbol="USDT",
                                free=new_usdt_free,
                                locked=old_usdt.locked,
                                total=new_usdt_total,
                                value_usd=new_usdt_total,
                            )
                        elif intent.side == OrderSide.SELL and not is_fut:
                            portfolio.holdings["USDT"] = AssetHolding(
                                symbol="USDT",
                                free=max(0.0, delta_usdt),
                                locked=0.0,
                                total=max(0.0, delta_usdt),
                                value_usd=max(0.0, delta_usdt),
                            )
                    elif result.status == OrderStatus.OPEN:
                        executed_count += 1
                    elif result.status in (OrderStatus.FAILED, OrderStatus.CANCELLED):
                        failed_symbols.add(intent.symbol)
                        failed_symbols.add(base_sym)
                    logger.info(
                        "✅ Order executed: %s %s %.8f — %s (Price: %s, Fee: %s)",
                        intent.side.value.upper(),
                        intent.symbol,
                        result.filled_amount or intent.amount,
                        result.status.value,
                        f"{result.average_price:.4f}" if result.average_price else "MARKET",
                        f"{result.fees:.6f} {result.fee_currency}" if result.fees else "0",
                    )
                except Exception as e:
                    logger.error("❌ Order execution failed for %s %s: %s", intent.side.value, intent.symbol, e)
                    failed_symbols.add(intent.symbol)
                    failed_symbols.add(base_sym)
                    # Don't halt the cycle — continue with remaining orders for other symbols

            # 10. Update last processed candle timestamps
            for pair in pairs.values():
                ts_candidates = []
                if pair in self._state.last_processed_candle_ts:
                    ts_candidates.append(self._state.last_processed_candle_ts[pair])
                new_c = new_candles_by_pair.get(pair, [])
                if new_c:
                    ts_candidates.append(new_c[-1].timestamp_ms)
                full_c = candles_by_asset.get(pair, [])
                if full_c:
                    ts_candidates.append(full_c[-1].timestamp_ms)
                if ts_candidates:
                    self._state.last_processed_candle_ts[pair] = max(ts_candidates)

            # Save state
            self._save_state(success=True)
            self._consecutive_errors = 0

            elapsed = time.time() - cycle_start
            lev_str = f" | Lev: {decision.metadata.get('effective_leverage', 1.0):.1f}x" if decision.metadata else ""
            shield_str = " | 🛡️ SAFE_HAVEN" if decision.metadata and decision.metadata.get("safe_haven_active") else (" | 🚀 IN_MOMENTUM" if decision.metadata and decision.metadata.get("in_momentum") else "")
            if len(plan.orders) > 0 or force:
                logger.info(
                    "CYCLE COMPLETE | %.1fs | Regime: %s%s%s | Orders: %d/%d executed",
                    elapsed,
                    decision.regime.value,
                    lev_str,
                    shield_str,
                    executed_count,
                    len(plan.orders),
                )
                logger.info("=" * 60)
            else:
                logger.debug(
                    "CYCLE COMPLETE | %.1fs | Regime: %s%s%s | Orders: 0/0 executed (idle)",
                    elapsed,
                    decision.regime.value,
                    lev_str,
                    shield_str,
                )

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
            if len(self._state.critical_errors) > 100:
                self._state.critical_errors = self._state.critical_errors[-100:]
            self._save_state(success=False)

            if self._consecutive_errors >= self._config.scheduler.max_consecutive_errors:
                logger.critical(
                    "Too many consecutive errors (%d) — stopping",
                    self._consecutive_errors,
                )
                raise SafeStopRequired(
                    f"Too many consecutive errors: {self._consecutive_errors}"
                ) from e

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
