"""
Risk manager.

Pre-trade gate that validates every order against configurable limits
before it reaches the exchange.

If ANY check fails, the order is rejected with a clear reason.
"""

from __future__ import annotations

import logging
import time

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

    def __init__(self, config: RiskConfig, is_futures: bool = False):
        self._config = config
        self._is_futures = is_futures or getattr(config, "is_futures", False)
        self._cycle_order_count = 0
        self._last_order_time: float = 0.0
        self._cycle_total_value: float = 0.0

    def approve_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
    ) -> tuple[bool, str]:
        """
        Evaluate whether the order is safe to execute.

        Returns:
            (approved, reason) — reason is empty if approved.
        """
        if intent.amount <= 0:
            return False, f"Order amount must be positive, got {intent.amount}"
        if intent.order_type == OrderType.LIMIT and (intent.price is None or intent.price <= 0):
            return False, "Limit order requires a valid positive price"

        checks = [
            self._check_kill_switch,
            lambda i, p: self._check_symbol_allowed(i, p),
            lambda i, p: self._check_market_order(i, p),
            lambda i, p: self._check_max_order_value(i, p),
            lambda i, p: self._check_max_orders_per_cycle(i, p),
            lambda i, p: self._check_min_time_between_orders(i, p),
            lambda i, p: self._check_max_portfolio_change(i, p),
            lambda i, p: self._check_min_order_value(i, p),
            lambda i, p: self._check_sufficient_balance(i, p),
        ]

        for check in checks:
            approved, reason = check(intent, portfolio)
            if not approved:
                side_display = intent.side.value if hasattr(intent.side, "value") else str(intent.side)
                logger.warning(
                    "Order REJECTED by risk manager: %s | Order: %s %s %.8f",
                    reason,
                    side_display,
                    intent.symbol,
                    intent.amount,
                )
                return False, reason

        # All checks passed
        self._cycle_order_count += 1
        self._last_order_time = time.time()
        order_value = self._get_order_value(intent)
        if not self._is_position_reducing_order(intent, portfolio):
            self._cycle_total_value += order_value

        side_display = intent.side.value if hasattr(intent.side, "value") else str(intent.side)
        logger.info(
            "Order APPROVED by risk manager: %s %s %.8f ($%.2f)",
            side_display,
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
    ) -> tuple[bool, str]:
        if self._config.kill_switch:
            return False, "Kill switch is ACTIVE — all trading halted"
        return True, ""

    def _check_symbol_allowed(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        clean_sym = intent.symbol.split(":")[0] if ":" in intent.symbol else intent.symbol
        base_sym = clean_sym.split("/")[0]
        pair_sym = f"{base_sym}/USDT"
        banned = self._config.banned_symbols
        if any(s in banned for s in (intent.symbol, clean_sym, base_sym, pair_sym)):
            return False, f"Symbol {intent.symbol} is banned"
        allowed = self._config.allowed_symbols
        if allowed and not any(s in allowed for s in (intent.symbol, clean_sym, base_sym, pair_sym)):
            return False, f"Symbol {intent.symbol} not in allowed list"
        return True, ""

    def _check_market_order(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
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
    ) -> tuple[bool, str]:
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""
        order_value = self._get_order_value(intent)
        if order_value > self._config.max_single_order_usd:
            return (
                False,
                f"Order value ${order_value:.2f} exceeds max ${self._config.max_single_order_usd:.2f}",
            )
        return True, ""

    def _check_max_orders_per_cycle(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""
        if self._cycle_order_count >= self._config.max_orders_per_cycle:
            return (
                False,
                f"Max orders per cycle reached ({self._config.max_orders_per_cycle})",
            )
        return True, ""

    def _check_min_time_between_orders(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        # Position reduction / emergency stop orders must not be delayed by time throttling
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""
        if self._last_order_time > 0:
            elapsed = time.time() - self._last_order_time
            # Allow 0.05s tolerance for OS scheduler sleep jitter
            if elapsed < (self._config.min_seconds_between_orders - 0.05):
                return (
                    False,
                    f"Only {elapsed:.1f}s since last order (min={self._config.min_seconds_between_orders}s)",
                )
        return True, ""

    def _is_position_reducing_order(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> bool:
        """Check if an order only reduces or closes an existing position without flipping/expanding."""
        if getattr(intent, "reduce_only", False):
            return True
        base = intent.symbol.split("/")[0].split(":")[0]
        holding = portfolio.holdings.get(base)
        if holding is None:
            return False
        current_qty = holding.total
        side_str = (intent.side.value if hasattr(intent.side, "value") else str(intent.side)).upper()
        if current_qty > 1e-8 and side_str == "SELL":
            return intent.amount <= (current_qty + 1e-5)
        if current_qty < -1e-8 and side_str == "BUY":
            return intent.amount <= (abs(current_qty) + 1e-5)
        return False

    def _check_max_portfolio_change(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        if portfolio.total_value_usd <= 0:
            return True, ""
        # Position reduction/liquidation orders reduce risk and must not be blocked by portfolio change caps
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""
        order_value = self._get_order_value(intent)
        total_change = (self._cycle_total_value + order_value) / portfolio.total_value_usd
        order_lev = getattr(intent, "leverage", 1.0)
        base = intent.symbol.split("/")[0].split(":")[0]
        holding = portfolio.holdings.get(base)
        h_lev = holding.leverage if holding else 1.0
        lev = max(1.0, float(order_lev if order_lev > 1.0 else max(1.0, h_lev)))
        allowed_max_change = max(self._config.max_portfolio_change_pct, self._config.max_portfolio_change_pct * (lev / 2.0), lev * 1.2)
        if total_change > allowed_max_change:
            return (
                False,
                f"Cumulative portfolio change {total_change:.1%} exceeds max {allowed_max_change:.1%}",
            )
        return True, ""

    def _check_min_order_value(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        # Position reduction/liquidation orders must never be trapped by minimum order value
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""
        order_value = self._get_order_value(intent)
        if order_value < self._config.min_order_value_usd:
            return (
                False,
                f"Order value ${order_value:.2f} below minimum ${self._config.min_order_value_usd:.2f}",
            )
        return True, ""

    def _check_sufficient_balance(
        self, intent: OrderIntent, portfolio: PortfolioSnapshot
    ) -> tuple[bool, str]:
        """
        Check that account has sufficient free balance or margin to execute the order.
        Position-reducing orders always pass since they release capital/margin.
        """
        if self._is_position_reducing_order(intent, portfolio):
            return True, ""

        base = intent.symbol.split("/")[0].split(":")[0]
        holding = portfolio.holdings.get(base)

        parts = intent.symbol.split("/")
        quote = parts[1].split(":")[0] if len(parts) > 1 else "USDT"
        quote_holding = portfolio.holdings.get(quote) or portfolio.holdings.get("USDT") or portfolio.holdings.get("USD")
        free_quote = quote_holding.free if quote_holding else 0.0
        order_value = self._get_order_value(intent)
        intent_lev = float(getattr(intent, "leverage", 1.0) or 1.0)
        is_futures_intent = (
            intent_lev > 1.0
            or "short" in str(getattr(intent, "reason", "")).lower()
            or getattr(intent, "reduce_only", False)
        )
        is_futures_portfolio = (
            self._is_futures
            or is_futures_intent
            or any(h.total < 0 or h.leverage > 1.0 for h in portfolio.holdings.values())
        )

        side_str = (intent.side.value if hasattr(intent.side, "value") else str(intent.side)).upper()

        # Spot sell requires sufficient free tokens (only when strictly in spot mode)
        if side_str == "SELL" and not is_futures_portfolio and (holding is None or holding.total >= 0):
            available_qty = holding.free if holding else 0.0
            if (
                intent.amount > (available_qty + 1e-6)
                and not getattr(intent, "reduce_only", False)
            ):
                return (
                    False,
                    f"Insufficient free balance for {base}: need {intent.amount:.8f}, available {available_qty:.8f}",
                )

        # Expanding BUY in spot requires 100% notional cash
        if side_str == "BUY":
            if free_quote <= 0.0 and order_value > 0.0:
                return False, f"Insufficient balance: free {quote} is ${free_quote:.2f}"

            if not is_futures_portfolio and order_value > (free_quote + 1e-4):
                return (
                    False,
                    f"Insufficient balance: need ${order_value:.2f}, free {quote} is ${free_quote:.2f}",
                )

        # Futures margin check for expanding orders (both BUY and SELL)
        if is_futures_portfolio and order_value > 0.0 and not self._is_position_reducing_order(intent, portfolio) and not getattr(intent, "reduce_only", False):
            if free_quote <= 0.0:
                return False, f"Insufficient balance: free {quote} is ${free_quote:.2f}"
            order_lev = getattr(intent, "leverage", 1.0) or 1.0
            holding_lev = holding.leverage if holding else 1.0
            lev = max(1.0, float(order_lev if order_lev > 1.0 else (holding_lev if holding_lev > 1.0 else 1.0)))
            margin_req = order_value / max(1.0, lev)
            if free_quote < (margin_req - 1e-4):
                return (
                    False,
                    f"Insufficient margin: need ${margin_req:.2f} {quote} margin (leverage={lev:.1f}x), free {quote} is ${free_quote:.2f}",
                )

        return True, ""
