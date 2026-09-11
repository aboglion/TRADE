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

    def _append_completed_order(self, order_data: dict[str, Any]) -> None:
        """Safely append or update an order in completed_orders to prevent duplicates."""
        cid = order_data.get("client_order_id")
        eid = order_data.get("exchange_order_id")
        for existing in self._state.completed_orders:
            if (cid and existing.get("client_order_id") == cid) or (eid and existing.get("exchange_order_id") == eid):
                existing.update(order_data)
                return
        self._state.completed_orders.append(order_data)

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
                            fee_val = float(res.fees or 0.0) if (res.fees and res.fees > 0) else float(local_order.get("fees", 0.0) or 0.0)
                            curr = str(res.fee_currency or local_order.get("fee_currency") or "USDT").strip().upper() or "USDT"
                            if fee_val > 0:
                                local_order["fees"] = fee_val
                                local_order["fee_currency"] = curr
                                if not local_order.get("fees_recorded"):
                                    self._state.session_fees[curr] = float(self._state.session_fees.get(curr, 0.0) or 0.0) + fee_val
                                    local_order["fees_recorded"] = True
                            if res.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED):
                                self._append_completed_order(local_order)
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
                self._append_completed_order(local_order)
                clean = False
                continue

            lookup_id = exc_id or client_id
            if exc_id and exc_id in exchange_ids:
                # Order exists on exchange — good
                continue

            if not exc_id and client_id:
                matching_open = next((o for o in exchange_open if o.client_order_id == client_id), None)
                if matching_open:
                    local_order["exchange_order_id"] = matching_open.exchange_order_id
                    local_order["status"] = matching_open.status.value
                    local_order["filled_amount"] = matching_open.filled_amount
                    continue

            if lookup_id:
                # Order was submitted but no longer open on exchange (or exc_id was missing)
                # It was either filled, cancelled, or failed
                try:
                    result = self._gateway.fetch_order(symbol, lookup_id)
                    local_order["exchange_order_id"] = result.exchange_order_id or local_order.get("exchange_order_id")
                    local_order["status"] = result.status.value
                    local_order["filled_amount"] = result.filled_amount
                    local_order["average_price"] = result.average_price
                    fee_val = float(result.fees or 0.0) if (result.fees and result.fees > 0) else float(local_order.get("fees", 0.0) or 0.0)
                    curr = str(result.fee_currency or local_order.get("fee_currency") or "USDT").strip().upper() or "USDT"
                    if fee_val > 0:
                        local_order["fees"] = fee_val
                        local_order["fee_currency"] = curr
                        if not local_order.get("fees_recorded"):
                            self._state.session_fees[curr] = float(self._state.session_fees.get(curr, 0.0) or 0.0) + fee_val
                            local_order["fees_recorded"] = True
                    logger.info(
                        "Resolved order %s: %s (filled=%.8f)",
                        lookup_id, result.status.value, result.filled_amount,
                    )
                    if result.status in (
                        OrderStatus.FILLED,
                        OrderStatus.CANCELLED,
                        OrderStatus.EXPIRED,
                        OrderStatus.FAILED,
                    ):
                        self._append_completed_order(local_order)
                        has_fill = (result.status == OrderStatus.FILLED) or bool(result.filled_amount and result.filled_amount > 0)
                        if has_fill and self._telegram_service:
                            try:
                                mode_str = self._run_mode.name if hasattr(self._run_mode, "name") else str(self._run_mode or "LIVE")
                                self._telegram_service.send_trade_notification(local_order, run_mode=mode_str)
                            except Exception as ex:
                                logger.warning("Telegram trade notification failed during reconciliation: %s", ex)
                except Exception as e:
                    err_str = str(e)
                    if "Order does not exist" in err_str or "-2013" in err_str or "OrderNotFound" in type(e).__name__:
                        logger.warning("Order %s does not exist on exchange — marking FAILED", lookup_id)
                        local_order["status"] = OrderStatus.FAILED.value
                        local_order["error_message"] = f"Order not found on exchange: {e}"
                        self._append_completed_order(local_order)
                    else:
                        logger.warning(
                            "Cannot resolve order %s: %s — marking unknown",
                            lookup_id, e,
                        )
                        local_order["status"] = "unknown"
                    clean = False

        # 3. Check for orphaned exchange orders (not in our local state)
        local_exc_ids = {
            o.get("exchange_order_id")
            for o in self._state.pending_orders + self._state.completed_orders
            if o.get("exchange_order_id")
        }
        local_client_ids = {
            o.get("client_order_id")
            for o in self._state.pending_orders + self._state.completed_orders
            if o.get("client_order_id")
        }

        for exc_order in exchange_open:
            if (
                exc_order.exchange_order_id not in local_exc_ids
                and (not exc_order.client_order_id or exc_order.client_order_id not in local_client_ids)
            ):
                logger.warning(
                    "ORPHANED order found on exchange: %s (%s) — "
                    "not in local state! Adding to tracking.",
                    exc_order.exchange_order_id,
                    exc_order.client_order_id,
                )
                amount = 0.0
                side = "buy"
                if isinstance(exc_order.raw_response, dict):
                    raw = exc_order.raw_response
                    raw_info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
                    amount = float(raw.get("amount") or raw_info.get("origQty") or 0.0)
                    side = str(raw.get("side") or raw_info.get("side") or "buy").lower()

                sym = exc_order.symbol or ""
                if sym and "/" not in sym:
                    if sym.endswith("USDT"):
                        sym = f"{sym[:-4]}/USDT"
                    elif sym.endswith("USD"):
                        sym = f"{sym[:-3]}/USD"
                    elif sym.endswith("USDC"):
                        sym = f"{sym[:-4]}/USDC"

                self._state.pending_orders.append({
                    "client_order_id": exc_order.client_order_id,
                    "exchange_order_id": exc_order.exchange_order_id,
                    "symbol": sym,
                    "side": side,
                    "amount": amount,
                    "status": exc_order.status.value if hasattr(exc_order.status, "value") else str(exc_order.status or "open"),
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
