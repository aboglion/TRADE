"""
Reconciliation service.

Compares local bot state against the exchange to detect and resolve
inconsistencies.  The exchange is ALWAYS the source of truth.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from src.core.enums import OrderStatus
from src.core.exceptions import ReconciliationError
from src.core.models import BotState

logger = logging.getLogger("bot.services.reconciliation")


class ReconciliationService:
    """
    Detects and resolves discrepancies between local state and exchange.

    Run on every startup and periodically during operation.
    """

    def __init__(self, gateway, state: BotState):
        self._gateway = gateway
        self._state = state

    def reconcile(self) -> bool:
        """
        Full reconciliation cycle.

        1. Check open orders on exchange
        2. Match against local pending orders
        3. Resolve orphaned orders
        4. Update local state

        Returns True if reconciliation succeeded cleanly.
        """
        logger.info("Starting reconciliation...")
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
            if o.get("status") in ("intent", "submitted", "unknown", "open")
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
                # Order was saved but never submitted (crash before send)
                # It's safe to remove it — it was never sent
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
                    logger.info(
                        "Resolved order %s: %s (filled=%.8f)",
                        exc_id, result.status.value, result.filled_amount,
                    )
                    if result.status in (
                        OrderStatus.FILLED,
                        OrderStatus.CANCELLED,
                        OrderStatus.EXPIRED,
                    ):
                        self._state.completed_orders.append(local_order)
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
                self._state.pending_orders.append({
                    "client_order_id": exc_order.client_order_id,
                    "exchange_order_id": exc_order.exchange_order_id,
                    "status": exc_order.status.value,
                    "filled_amount": exc_order.filled_amount,
                    "source": "orphan_detected",
                })
                clean = False

        # 4. Clean up pending list
        self._state.pending_orders = [
            o for o in self._state.pending_orders
            if o.get("status") in ("submitted", "unknown", "open")
        ]

        if clean:
            logger.info("Reconciliation complete — state is consistent")
        else:
            logger.warning("Reconciliation found discrepancies (resolved)")

        return clean
