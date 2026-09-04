"""
Portfolio service.

Reads account balances, computes allocation ratios, diffs against
targets, and generates the minimal set of trades needed to rebalance.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.core.enums import OrderSide, OrderType
from src.core.models import (
    AssetHolding,
    OrderIntent,
    PortfolioSnapshot,
    RebalancePlan,
    TargetAllocation,
)
from src.utils.math_utils import (
    compute_order_amount,
    get_market_constraints,
    truncate_to_precision,
)

logger = logging.getLogger("bot.services.portfolio")

# Minimum deviation to trigger a rebalance (percentage points)
DEFAULT_DEVIATION_THRESHOLD = 0.03  # 3%


class PortfolioService:
    """
    Manages portfolio state and rebalancing logic.

    Reads balances from the exchange, computes current vs target
    allocations, and generates the minimal set of orders.
    """

    def __init__(
        self,
        gateway,
        deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    ):
        """
        Args:
            gateway: ExchangeGateway or DryRunExchange instance.
            deviation_threshold: Minimum weight difference to trigger trade.
        """
        self._gateway = gateway
        self._deviation_threshold = deviation_threshold

    def get_portfolio(
        self,
        prices: Optional[Dict[str, float]] = None,
    ) -> PortfolioSnapshot:
        """
        Build a current portfolio snapshot from exchange balances.

        Args:
            prices: Current prices by pair, e.g. {"BTC/USDT": 60000}.
                    If None, fetches from exchange.
        """
        import time
        balances = self._gateway.fetch_balance()

        if prices is None:
            prices = {}

        holdings: Dict[str, AssetHolding] = {}
        total_value = 0.0

        for currency, bal in balances.items():
            if not isinstance(bal, dict) or "total" not in bal:
                continue

            total = float(bal.get("total", 0))
            free = float(bal.get("free", 0))
            locked = float(bal.get("used", 0) or bal.get("locked", 0) or 0)

            # Determine USD value
            if currency in ("USDT", "BUSD", "USDC", "USD"):
                value_usd = total
            else:
                pair = f"{currency}/USDT"
                price = prices.get(pair, 0.0)
                if price <= 0:
                    # Try to fetch from exchange
                    try:
                        price = self._gateway.fetch_ticker_price(pair)
                    except Exception:
                        price = 0.0
                value_usd = total * price

            holdings[currency] = AssetHolding(
                symbol=currency,
                free=free,
                locked=locked,
                total=total,
                value_usd=value_usd,
            )
            total_value += value_usd

        snapshot = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings=holdings,
            total_value_usd=total_value,
        )

        logger.info(
            "Portfolio snapshot: total=$%.2f | %s",
            total_value,
            {k: f"${v.value_usd:.2f}" for k, v in holdings.items() if v.value_usd > 1},
        )

        return snapshot

    def compute_rebalance_plan(
        self,
        portfolio: PortfolioSnapshot,
        target: TargetAllocation,
        prices: Dict[str, float],
    ) -> RebalancePlan:
        """
        Compute the minimal set of orders to reach target allocation.

        Only generates orders where the deviation exceeds the threshold.
        SELLS are generated before BUYS to free up capital.

        Args:
            portfolio: Current portfolio state.
            target: Desired allocation.
            prices: Current prices by pair.
        """
        total_value = portfolio.total_value_usd
        if total_value <= 0:
            logger.warning("Portfolio value is zero, no rebalance possible")
            return RebalancePlan(
                orders=[], current_snapshot=portfolio,
                target_allocation=target, total_deviation_pct=0.0,
            )

        # Compute deviations
        deviations: Dict[str, float] = {}
        for symbol, target_weight in target.weights.items():
            if symbol == "USDT":
                continue  # USDT is the residual
            base = symbol.split("/")[0] if "/" in symbol else symbol
            current_weight = portfolio.get_weight(base)
            deviation = target_weight - current_weight
            deviations[symbol] = deviation

        total_deviation = sum(abs(d) for d in deviations.values())

        logger.info(
            "Allocation deviations: %s (total=%.2f%%)",
            {k: f"{v:+.2%}" for k, v in deviations.items()},
            total_deviation * 100,
        )

        # Generate orders for significant deviations
        sell_orders: List[OrderIntent] = []
        buy_orders: List[OrderIntent] = []

        for symbol, deviation in deviations.items():
            if abs(deviation) < self._deviation_threshold:
                continue

            price = prices.get(symbol, 0.0)
            if price <= 0:
                logger.warning("No price for %s, skipping", symbol)
                continue

            target_value_usd = abs(deviation) * total_value

            # Get market constraints
            try:
                market_info = self._gateway.get_market_info(symbol)
                constraints = get_market_constraints(market_info)
            except Exception:
                constraints = {
                    "amount_precision": 8,
                    "min_amount": 0.00001,
                    "min_notional": 10.0,
                }

            amount = compute_order_amount(
                target_value_usd=target_value_usd,
                price=price,
                amount_precision=constraints["amount_precision"],
                min_amount=constraints["min_amount"],
                min_notional=constraints["min_notional"],
            )

            if amount is None:
                logger.debug(
                    "Order for %s too small ($%.2f), skipping",
                    symbol, target_value_usd,
                )
                continue

            # Check available balance for sells
            base = symbol.split("/")[0]
            if deviation < 0:  # Need to sell
                holding = portfolio.holdings.get(base)
                available = holding.free if holding else 0.0
                if amount > available:
                    amount = truncate_to_precision(
                        available, constraints["amount_precision"]
                    )
                    if amount <= 0:
                        continue

            intent = OrderIntent(
                client_order_id=OrderIntent.generate_id(),
                symbol=symbol,
                side=OrderSide.SELL if deviation < 0 else OrderSide.BUY,
                order_type=OrderType.LIMIT,
                amount=amount,
                price=price,
                reason=f"Rebalance: {deviation:+.2%} deviation in {symbol}",
                candle_ts=target.timestamp_ms,
            )

            if deviation < 0:
                sell_orders.append(intent)
            else:
                buy_orders.append(intent)

        # Sells first, then buys (to free up USDT)
        all_orders = sell_orders + buy_orders

        if all_orders:
            logger.info(
                "Rebalance plan: %d sells + %d buys",
                len(sell_orders), len(buy_orders),
            )
            for o in all_orders:
                logger.info(
                    "  %s %s %.8f @ %.4f ($%.2f) — %s",
                    o.side.value.upper(), o.symbol, o.amount,
                    o.price or 0, (o.amount * (o.price or 0)),
                    o.reason,
                )
        else:
            logger.info("Portfolio within threshold, no rebalance needed")

        return RebalancePlan(
            orders=all_orders,
            current_snapshot=portfolio,
            target_allocation=target,
            total_deviation_pct=total_deviation,
        )
