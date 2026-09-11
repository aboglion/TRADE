"""
Reconciliation service.

Compares local bot state against the exchange to detect and resolve
inconsistencies.  The exchange is ALWAYS the source of truth.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.enums import OrderStatus
from src.core.models import BotState

logger = logging.getLogger("bot.services.reconciliation")


class ReconciliationService:
    """
    Detects and resolves discrepancies between local state and exchange.

    Run on every startup and periodically during operation.
    """

    def __init__(
        self,
        gateway,
        state: BotState,
        telegram_service: Any | None = None,
        run_mode: Any | None = None,
    ):
        self._gateway = gateway
        self._state = state
        self._telegram_service = telegram_service
        self._run_mode = run_mode

    def reconcile(self) -> bool:
        """
        Full reconciliation cycle.

        1. Check open orders on exchange
        2. Match against local pending orders
        3. Resolve orphaned orders
        4. Update local state

        Returns True if reconciliation succeeded cleanly.
        """
        logger.debug("Starting reconciliation...")
        clean = True

        # 1. Check exchange for open orders
        try:
            exchange_open = self._gateway.fetch_open_orders()
        except Exception as e:
            logger.error("Cannot fetch open orders for reconciliation: %s", e)
            return False

        # 2. Check locally pending orders against exchange
        local_pending = [
            o for o in self._state.pending_orders
            if o.get("status") in ("intent", "submitted", "unknown", "open", "partially_filled")
        ]

        # Build set of known exchange order IDs
        exchange_ids = {
            o.exchange_order_id for o in exchange_open
            if o.exchange_order_id
        }

        # Check each local pending order
        for local_order in local_pending:
            exc_id = local_order.get("exchange_order_id")
            client_id = local_order.get("client_order_id", "")
            symbol = local_order.get("symbol", "")
            status = local_order.get("status", "")

            if status == "intent":
                # Check if this intent actually reached the exchange before a process crash/interruption
                matching_open = next((o for o in exchange_open if o.client_order_id == client_id), None) if client_id else None
                if matching_open:
                    local_order["exchange_order_id"] = matching_open.exchange_order_id
                    local_order["status"] = matching_open.status.value
                    local_order["filled_amount"] = matching_open.filled_amount
                    logger.info("Recovered open exchange order for pre-crash intent %s (%s)", client_id, matching_open.exchange_order_id)
                    continue

                # Query exchange by clientOrderId in case it was already executed/filled during crash
                recovered = False
                if client_id and symbol:
                    try:
                        res = self._gateway.fetch_order(symbol, client_id)
                        if res.status not in (OrderStatus.UNKNOWN, None):
                            local_order["exchange_order_id"] = res.exchange_order_id or client_id
                            local_order["status"] = res.status.value
                            local_order["filled_amount"] = res.filled_amount
                            local_order["average_price"] = res.average_price
                            if res.fees and res.fees > 0:
                                local_order["fees"] = res.fees
                                curr = (res.fee_currency or "USDT").upper()
                                local_order["fee_currency"] = curr
                                self._state.session_fees[curr] = self._state.session_fees.get(curr, 0.0) + res.fees
                            if res.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED):
                                self._state.completed_orders.append(local_order)
                                has_fill = (res.status == OrderStatus.FILLED) or bool(res.filled_amount and res.filled_amount > 0)
                                if has_fill and self._telegram_service:
                                    try:
                                        mode_str = self._run_mode.name if hasattr(self._run_mode, "name") else str(self._run_mode or "LIVE")
                                        self._telegram_service.send_trade_notification(local_order, run_mode=mode_str)
                                    except Exception:
                                        pass
                            recovered = True
                            logger.info("Recovered completed pre-crash intent %s from exchange (status=%s, filled=%.8f)", client_id, res.status.value, res.filled_amount)
                    except Exception:
                        recovered = False

                if recovered:
                    continue

                # Order was saved but never submitted or rejected pre-submission
                logger.warning(
                    "Found unsent intent %s — removing (never reached exchange)",
                    client_id,
                )
                local_order["status"] = "cancelled_pre_send"
                self._state.completed_orders.append(local_order)
                clean = False
                continue

            if exc_id and exc_id in exchange_ids:
                # Order exists on exchange — good
                continue

            if exc_id and exc_id not in exchange_ids:
                # Order was submitted but no longer open on exchange
                # It was either filled or cancelled
                try:
                    result = self._gateway.fetch_order(symbol, exc_id)
                    local_order["status"] = result.status.value
                    local_order["filled_amount"] = result.filled_amount
                    local_order["average_price"] = result.average_price
                    if result.fees and result.fees > 0:
                        local_order["fees"] = result.fees
                        curr = (result.fee_currency or "USDT").upper()
                        local_order["fee_currency"] = curr
                        self._state.session_fees[curr] = self._state.session_fees.get(curr, 0.0) + result.fees
                    logger.info(
                        "Resolved order %s: %s (filled=%.8f)",
                        exc_id, result.status.value, result.filled_amount,
                    )
                    if result.status in (
                        OrderStatus.FILLED,
                        OrderStatus.CANCELLED,
                        OrderStatus.EXPIRED,
                        OrderStatus.FAILED,
                    ):
                        self._state.completed_orders.append(local_order)
                        has_fill = (result.status == OrderStatus.FILLED) or bool(result.filled_amount and result.filled_amount > 0)
                        if has_fill and self._telegram_service:
                            try:
                                mode_str = self._run_mode.name if hasattr(self._run_mode, "name") else str(self._run_mode or "LIVE")
                                self._telegram_service.send_trade_notification(local_order, run_mode=mode_str)
                            except Exception as ex:
                                logger.warning("Telegram trade notification failed during reconciliation: %s", ex)
                except Exception as e:
                    logger.warning(
                        "Cannot resolve order %s: %s — marking unknown",
                        exc_id, e,
                    )
                    local_order["status"] = "unknown"
                    clean = False

        # 3. Check for orphaned exchange orders (not in our local state)
        local_exc_ids = {
            o.get("exchange_order_id")
            for o in self._state.pending_orders + self._state.completed_orders
            if o.get("exchange_order_id")
        }

        for exc_order in exchange_open:
            if exc_order.exchange_order_id not in local_exc_ids:
                logger.warning(
                    "ORPHANED order found on exchange: %s (%s) — "
                    "not in local state! Adding to tracking.",
                    exc_order.exchange_order_id,
                    exc_order.client_order_id,
                )
                amount = 0.0
                side = "buy"
                if isinstance(exc_order.raw_response, dict):
                    amount = float(exc_order.raw_response.get("amount", 0.0) or 0.0)
                    side = str(exc_order.raw_response.get("side", "buy") or "buy").lower()

                self._state.pending_orders.append({
                    "client_order_id": exc_order.client_order_id,
                    "exchange_order_id": exc_order.exchange_order_id,
                    "symbol": exc_order.symbol,
                    "side": side,
                    "amount": amount,
                    "status": exc_order.status.value,
                    "filled_amount": exc_order.filled_amount,
                    "source": "orphan_detected",
                })
                clean = False

        # 4. Clean up pending list
        self._state.pending_orders = [
            o for o in self._state.pending_orders
            if o.get("status") in ("submitted", "unknown", "open", "partially_filled")
        ]

        # Cap completed_orders in memory to prevent unbounded growth
        if len(self._state.completed_orders) > 6000:
            self._state.completed_orders = self._state.completed_orders[-5000:]

        if clean:
            logger.debug("Reconciliation complete — state is consistent")
        else:
            logger.warning("Reconciliation found discrepancies (resolved)")

        return clean
