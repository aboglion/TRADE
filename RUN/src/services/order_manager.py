"""
Order manager.

Manages the complete lifecycle of orders:
  Intent → Persist → Validate → Submit → Track → Confirm

Key safety features:
- Every order gets a unique clientOrderId before submission
- Intent is saved to state BEFORE submission (crash-safe)
- On restart, checks exchange for any pending/unknown orders
- Never submits the same clientOrderId twice
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from src.core.enums import OrderStatus, RunMode
from src.core.exceptions import (
    DuplicateOrderError,
    InsufficientBalanceError,
    InvalidOrderError,
    UnknownOrderStateError,
)
from src.core.models import BotState, OrderIntent, OrderResult

logger = logging.getLogger("bot.services.order_manager")


class OrderManager:
    """
    Manages order submission and lifecycle tracking.

    All orders flow through this manager — it ensures idempotency,
    tracks state, and handles crash recovery.
    """

    def __init__(self, gateway, state: BotState, run_mode: RunMode):
        """
        Args:
            gateway: ExchangeGateway or DryRunExchange instance.
            state: Current bot state (for persistence).
            run_mode: Current operational mode.
        """
        self._gateway = gateway
        self._state = state
        self._run_mode = run_mode
        # Track submitted order IDs to prevent duplicates
        self._submitted_ids: set = set()
        self._load_submitted_ids()

    def execute(self, intent: OrderIntent) -> OrderResult:
        """
        Execute an order through the full lifecycle.

        1. Check for duplicate
        2. Save intent to state (pre-submission safety)
        3. Submit to exchange
        4. Update state with result
        5. Return result
        """
        # 1. Duplicate check
        if intent.client_order_id in self._submitted_ids:
            logger.warning(
                "Duplicate order rejected: %s", intent.client_order_id
            )
            raise DuplicateOrderError(
                f"Order {intent.client_order_id} already submitted"
            )

        # 2. Save intent (crash-safe: if we crash after this, we know
        #    on restart that we intended to place this order)
        self._save_intent(intent)

        # 3. Submit
        logger.info(
            "Submitting order: %s %s %.8f %s @ %s | ID=%s | reason=%s",
            intent.side.value.upper(),
            intent.symbol,
            intent.amount,
            intent.order_type.value,
            intent.price or "MARKET",
            intent.client_order_id,
            intent.reason,
        )

        try:
            if self._run_mode == RunMode.DRY_RUN:
                result = self._gateway.create_order(intent)
            else:
                result = self._gateway.create_order(intent)

            self._submitted_ids.add(intent.client_order_id)

            # 4. Update state
            self._update_order_result(intent, result)

            logger.info(
                "Order result: ID=%s, status=%s, filled=%.8f @ %.4f, fees=%.6f",
                result.exchange_order_id or "N/A",
                result.status.value,
                result.filled_amount,
                result.average_price,
                result.fees,
            )

            return result

        except (InsufficientBalanceError, InvalidOrderError) as e:
            # Permanent failure — record and don't retry
            result = OrderResult(
                client_order_id=intent.client_order_id,
                status=OrderStatus.FAILED,
                error_message=str(e),
            )
            self._update_order_result(intent, result)
            logger.error("Order failed permanently: %s", e)
            raise

        except Exception as e:
            # Unknown state — order might or might not have been placed
            result = OrderResult(
                client_order_id=intent.client_order_id,
                status=OrderStatus.UNKNOWN,
                error_message=str(e),
            )
            self._update_order_result(intent, result)
            logger.error(
                "Order in UNKNOWN state (network error?): %s — "
                "will check on next cycle",
                e,
            )
            raise UnknownOrderStateError(str(e))

    def check_pending_orders(self) -> List[OrderResult]:
        """
        On startup/recovery: check status of any pending/unknown orders.

        Queries the exchange for orders we think might still be open.
        Updates local state to match exchange reality.
        """
        results: List[OrderResult] = []
        pending = [
            o for o in self._state.pending_orders
            if o.get("status") in ("submitted", "unknown", "open", "intent")
        ]

        if not pending:
            return results

        logger.info(
            "Checking %d pending/unknown orders on exchange...",
            len(pending),
        )

        for order_data in pending:
            order_id = order_data.get("exchange_order_id") or order_data.get("client_order_id")
            symbol = order_data.get("symbol", "")

            if not order_id or not symbol:
                continue

            try:
                result = self._gateway.fetch_order(symbol, order_id)
                results.append(result)

                # Update local state
                order_data["status"] = result.status.value
                order_data["filled_amount"] = result.filled_amount
                order_data["average_price"] = result.average_price

                logger.info(
                    "Pending order %s: now %s (filled=%.8f)",
                    order_id, result.status.value, result.filled_amount,
                )

                # If filled or cancelled, move to completed
                if result.status in (
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.FAILED,
                    OrderStatus.EXPIRED,
                ):
                    self._state.completed_orders.append(order_data)
                    if result.status == OrderStatus.FILLED and result.fees > 0 and result.fee_currency:
                        curr = result.fee_currency
                        self._state.session_fees[curr] = self._state.session_fees.get(curr, 0.0) + result.fees

            except Exception as e:
                logger.warning(
                    "Could not check order %s: %s", order_id, e
                )

        # Remove resolved orders from pending
        self._state.pending_orders = [
            o for o in self._state.pending_orders
            if o.get("status") in ("submitted", "unknown", "open", "intent")
        ]

        return results

    def check_open_orders_on_exchange(self) -> List[OrderResult]:
        """
        Check for open orders on the exchange that we don't know about.

        This catches orphaned orders from crashed sessions.
        """
        try:
            open_orders = self._gateway.fetch_open_orders()
            if open_orders:
                logger.warning(
                    "Found %d open order(s) on exchange",
                    len(open_orders),
                )
                for o in open_orders:
                    logger.warning(
                        "  Exchange order %s: %s",
                        o.exchange_order_id, o.status.value,
                    )
            return open_orders
        except Exception as e:
            logger.error("Failed to check open orders: %s", e)
            return []

    def cancel_stale_open_orders(self) -> int:
        """
        Cancel any open orders resting on the exchange to prevent cycle deadlock.

        Returns number of canceled orders.
        """
        open_orders = self.check_open_orders_on_exchange()
        canceled_count = 0
        for o in open_orders:
            if not o.exchange_order_id:
                continue
            symbol = ""
            if isinstance(o.raw_response, dict):
                symbol = o.raw_response.get("symbol", "")
            try:
                logger.warning("Canceling stale open order %s (%s)...", o.exchange_order_id, symbol)
                self._gateway.cancel_order(symbol=symbol, order_id=o.exchange_order_id)
                canceled_count += 1
            except Exception as e:
                logger.error("Failed to cancel open order %s: %s", o.exchange_order_id, e)
        return canceled_count

    # ── Internal helpers ─────────────────────────────────────

    def _save_intent(self, intent: OrderIntent) -> None:
        """Save order intent to state before submission."""
        order_data = {
            "client_order_id": intent.client_order_id,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "order_type": intent.order_type.value,
            "amount": intent.amount,
            "price": intent.price,
            "reason": intent.reason,
            "status": "intent",
            "fees": 0.0,
            "fee_currency": "",
            "timestamp": int(time.time() * 1000),
        }
        self._state.pending_orders.append(order_data)

    def _update_order_result(
        self, intent: OrderIntent, result: OrderResult
    ) -> None:
        """Update the pending order entry with the submission result."""
        for order_data in self._state.pending_orders:
            if order_data.get("client_order_id") == intent.client_order_id:
                order_data["status"] = result.status.value
                order_data["exchange_order_id"] = result.exchange_order_id
                order_data["filled_amount"] = result.filled_amount
                order_data["average_price"] = result.average_price
                order_data["fees"] = result.fees
                order_data["fee_currency"] = result.fee_currency
                order_data["error_message"] = result.error_message

                # Move completed orders out of pending
                if result.status in (
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.FAILED,
                ):
                    self._state.completed_orders.append(order_data)
                    if result.status == OrderStatus.FILLED and result.fees > 0 and result.fee_currency:
                        curr = result.fee_currency
                        self._state.session_fees[curr] = self._state.session_fees.get(curr, 0.0) + result.fees
                break

        # Clean up pending list
        self._state.pending_orders = [
            o for o in self._state.pending_orders
            if o.get("status") in ("intent", "submitted", "unknown", "open")
        ]

    def _load_submitted_ids(self) -> None:
        """Load previously submitted order IDs from state."""
        for o in self._state.pending_orders + self._state.completed_orders:
            cid = o.get("client_order_id")
            if cid:
                self._submitted_ids.add(cid)
