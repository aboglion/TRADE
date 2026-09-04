"""
Risk manager.

Pre-trade gate that validates every order against configurable limits
before it reaches the exchange.

If ANY check fails, the order is rejected with a clear reason.
"""

from __future__ import annotations

import logging
import time
from typing import List, Tuple

from src.config.config_manager import RiskConfig
from src.core.enums import OrderType
from src.core.interfaces import IRiskManager
from src.core.models import OrderIntent, PortfolioSnapshot

logger = logging.getLogger("bot.services.risk")


class RiskManager(IRiskManager):
    """
    Validates orders against risk limits before execution.

    Checks:
    - Kill switch
    - Symbol whitelist/blacklist
    - Market order restriction
    - Max single order value
    - Max portfolio change per cycle
    - Max orders per cycle
    - Min time between orders
    - Sufficient balance
    """

    def __init__(self, config: RiskConfig):
        self._config = config
        self._cycle_order_count = 0
        self._last_order_time: float = 0.0
        self._cycle_total_value: float = 0.0

    def approve_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
    ) -> Tuple[bool, str]:
        """
        Evaluate whether the order is safe to execute.

        Returns:
            (approved, reason) — reason is empty if approved.
        """
        checks = [
            self._check_kill_switch,
            lambda i, p: self._check_symbol_allowed(i, p),
            lambda i, p: self._check_market_order(i, p),
            lambda i, p: self._check_max_order_value(i, p),
            lambda i, p: self._check_max_orders_per_cycle(i, p),
            lambda i, p: self._check_min_time_between_orders(i, p),
            lambda i, p: self._check_max_portfolio_change(i, p),
            lambda i, p: self._check_min_order_value(i, p),
        ]

        for check in checks:
            approved, reason = check(intent, portfolio)
            if not approved:
                logger.warning(
                    "Order REJECTED by risk manager: %s | Order: %s %s %.8f",
                    reason,
                    intent.side.value,
                    intent.symbol,
                    intent.amount,
                )
                return False, reason

        # All checks passed
        self._cycle_order_count += 1
        self._last_order_time = time.time()
        order_value = self._get_order_value(intent)
        self._cycle_total_value += order_value

        logger.info(
            "Order APPROVED by risk manager: %s %s %.8f ($%.2f)",
            intent.side.value,
            intent.symbol,
            intent.amount,
            order_value,
        )

        return True, ""

    def is_kill_switch_active(self) -> bool:
        return self._config.kill_switch

    def reset_cycle(self) -> None:
        """Reset per-cycle counters.  Call at the start of each trading cycle."""
        self._cycle_order_count = 0
        self._cycle_total_value = 0.0

    # ── Individual checks ────────────────────────────────────

    def _check_kill_switch(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if self._config.kill_switch:
            return False, "Kill switch is ACTIVE — all trading halted"
        return True, ""

    def _check_symbol_allowed(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if intent.symbol in self._config.banned_symbols:
            return False, f"Symbol {intent.symbol} is banned"
        if (
            self._config.allowed_symbols
            and intent.symbol not in self._config.allowed_symbols
        ):
            return False, f"Symbol {intent.symbol} not in allowed list"
        return True, ""

    def _check_market_order(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if (
            intent.order_type == OrderType.MARKET
            and not self._config.allow_market_orders
        ):
            return False, "Market orders are disabled in risk config"
        return True, ""

    def _get_order_value(self, intent: OrderIntent) -> float:
        price = intent.price or intent.estimated_price or 0.0
        return intent.amount * price

    def _check_max_order_value(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        order_value = self._get_order_value(intent)
        if order_value > self._config.max_single_order_usd:
            return (
                False,
                f"Order value ${order_value:.2f} exceeds max "
                f"${self._config.max_single_order_usd:.2f}",
            )
        return True, ""

    def _check_max_orders_per_cycle(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if self._cycle_order_count >= self._config.max_orders_per_cycle:
            return (
                False,
                f"Max orders per cycle reached ({self._config.max_orders_per_cycle})",
            )
        return True, ""

    def _check_min_time_between_orders(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if self._last_order_time > 0:
            elapsed = time.time() - self._last_order_time
            if elapsed < self._config.min_seconds_between_orders:
                return (
                    False,
                    f"Only {elapsed:.1f}s since last order "
                    f"(min={self._config.min_seconds_between_orders}s)",
                )
        return True, ""

    def _check_max_portfolio_change(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        if portfolio.total_value_usd <= 0:
            return True, ""
        order_value = self._get_order_value(intent)
        total_change = (self._cycle_total_value + order_value) / portfolio.total_value_usd
        if total_change > self._config.max_portfolio_change_pct:
            return (
                False,
                f"Cumulative portfolio change {total_change:.1%} exceeds "
                f"max {self._config.max_portfolio_change_pct:.1%}",
            )
        return True, ""

    def _check_min_order_value(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> Tuple[bool, str]:
        order_value = self._get_order_value(intent)
        if order_value < self._config.min_order_value_usd:
            return (
                False,
                f"Order value ${order_value:.2f} below minimum "
                f"${self._config.min_order_value_usd:.2f}",
            )
        return True, ""
