"""
Portfolio service.

Reads account balances, computes allocation ratios, diffs against
targets, and generates the minimal set of trades needed to rebalance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.exchanges.exchange_gateway import ExchangeGateway

from src.core.enums import OrderSide, OrderType
from src.core.models import (
    AssetHolding,
    OrderIntent,
    PortfolioSnapshot,
    RebalancePlan,
    TargetAllocation,
)
from src.utils.math_utils import (
    BINANCE_DEFAULT_MIN_NOTIONAL,
    compute_order_amount,
    get_market_constraints,
    is_above_min_order,
    truncate_to_precision,
    truncate_to_step_size,
)

logger = logging.getLogger("bot.services.portfolio")

# Minimum deviation to trigger a rebalance (percentage points)
DEFAULT_DEVIATION_THRESHOLD = 0.06  # 6%


class PortfolioService:
    """
    Manages portfolio state and rebalancing logic.

    Reads balances from the exchange, computes current vs target
    allocations, and generates the minimal set of orders.
    """

    def __init__(
        self,
        gateway: ExchangeGateway,
        deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
        allow_market_orders: bool = False,
        is_futures: bool = False,
    ):
        """
        Args:
            gateway: Exchange client.
            deviation_threshold: Minimum weight difference to trigger trade.
            allow_market_orders: If True, execute rebalance via market orders.
            is_futures: If True, allows short selling and bypasses spot balance limits.
        """
        self._gateway = gateway
        self._deviation_threshold = deviation_threshold
        self._allow_market_orders = allow_market_orders
        self._is_futures = is_futures

    def get_portfolio(
        self,
        prices: dict[str, float] | None = None,
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

        holdings: dict[str, AssetHolding] = {}
        total_value = 0.0

        for currency, bal in balances.items():
            if not isinstance(bal, dict) or "total" not in bal:
                continue
            total = float(bal.get("total", 0))
            free = float(bal.get("free", 0))
            locked = float(bal.get("used", 0) or bal.get("locked", 0) or 0)
            
            # USDT margin balance in futures already includes unrealized PnL of positions
            if currency in ("USDT", "BUSD", "USDC", "USD"):
                value_usd = total
                total_value += value_usd
            else:
                # If Spot, keep asset values
                pair = f"{currency}/USDT"
                price = prices.get(pair, 0.0) or prices.get(f"{pair}:USDT", 0.0) or prices.get(currency, 0.0)
                if price <= 0:
                    try:
                        price = self._gateway.fetch_ticker_price(pair)
                    except Exception:
                        price = 0.0
                value_usd = total * price
                total_value += value_usd

            holdings[currency] = AssetHolding(
                symbol=currency,
                free=free,
                locked=locked,
                total=total,
                value_usd=value_usd,
            )

        # Merge Futures Positions
        try:
            positions = self._gateway.fetch_positions()
            for pos in positions:
                sym = pos.get("symbol", "")
                base = sym.split("/")[0].split(":")[0].split("_")[0] if ("/" in sym or ":" in sym or "_" in sym) else sym.replace("USDT", "")
                contracts = float(pos.get("contracts", 0) or 0)
                if contracts == 0:
                    continue
                info_amt = float(pos.get("info", {}).get("positionAmt", 0) or 0)
                side = str(pos.get("side", "") or "").lower()
                if (side == "short" or info_amt < 0 or contracts < 0):
                    contracts = -abs(contracts)
                else:
                    contracts = abs(contracts)
                
                entry_price = float(pos.get("entryPrice") or pos.get("info", {}).get("entryPrice") or 0.0)
                unrealized_pnl = float(pos.get("unrealizedPnl") or pos.get("info", {}).get("unRealizedProfit") or 0.0)
                leverage = float(pos.get("leverage") or pos.get("info", {}).get("leverage") or 1.0)
                
                clean_sym = sym.split(":")[0] if ":" in sym else sym
                price = (
                    prices.get(sym, 0.0)
                    or prices.get(clean_sym, 0.0)
                    or prices.get(f"{base}/USDT", 0.0)
                    or prices.get(base, 0.0)
                )
                if price <= 0:
                    for sym_candidate in (sym, clean_sym, f"{base}/USDT", f"{clean_sym}:USDT"):
                        try:
                            price = self._gateway.fetch_ticker_price(sym_candidate)
                            if price > 0:
                                break
                        except Exception:
                            pass
                if price <= 0 and entry_price > 0:
                    price = entry_price
                        
                value_usd = abs(contracts) * price

                existing = holdings.get(base)
                spot_free = existing.free if existing else 0.0
                spot_locked = existing.locked if existing else 0.0
                spot_total = existing.total if existing else 0.0
                combined_total = spot_total + contracts

                # Calculate notional USD value handling spot + futures longs/shorts
                if spot_total > 0 and contracts < 0:
                    # Hedged position (Spot Long + Futures Short)
                    net_qty = spot_total + contracts
                    value_usd = abs(net_qty) * price
                elif spot_total > 0 and contracts > 0:
                    # Combined Long (Spot + Futures Long)
                    value_usd = (spot_total + contracts) * price
                else:
                    # Pure futures position (contracts long or short)
                    value_usd = abs(contracts) * price

                holdings[base] = AssetHolding(
                    symbol=base,
                    free=spot_free,
                    locked=spot_locked,
                    total=combined_total,
                    value_usd=value_usd,
                    unrealized_pnl=unrealized_pnl,
                    entry_price=entry_price,
                    leverage=leverage,
                    contracts=contracts,
                )
        except Exception as e:
            logger.warning("Failed to fetch futures positions: %s", e)

        # Recalculate total_value_usd after merging futures positions.
        # The initial total_value from balances only includes USDT wallet balance
        # (which in Binance futures already reflects unrealized PnL as margin balance).
        # For accurate weight calculations, total_value should reflect the full
        # account equity: USDT margin balance is the canonical source of truth in
        # futures mode (it already includes unrealized PnL), so we do NOT double-count
        # position notional values. Instead, we recalculate from the final holdings
        # to ensure consistency after position merging.
        recalc_total = sum(h.value_usd for h in holdings.values())
        # Use the larger of the two to avoid underestimating (e.g., when USDT margin
        # balance already includes position equity, recalc_total double-counts;
        # when it doesn't, recalc_total is more accurate).
        # In Binance futures: USDT total = wallet balance + unrealized PnL, and
        # position value_usd = |contracts| * price. These overlap, so we keep
        # total_value (USDT-based) as the primary equity measure, but ensure it's
        # at least as large as any individual holding to prevent weight > 100%.
        if recalc_total > total_value > 0:
            # Positions exist whose notional exceeds the margin-based total.
            # This happens when leverage > 1x. Use margin-based total_value
            # as it represents actual equity, not notional exposure.
            pass
        elif total_value <= 0 and recalc_total > 0:
            total_value = recalc_total

        snapshot = PortfolioSnapshot(
            timestamp_ms=int(time.time() * 1000),
            holdings=holdings,
            total_value_usd=total_value,
        )

        logger.debug(
            "Portfolio snapshot: total=$%.2f | %s",
            total_value,
            {k: f"${v.value_usd:.2f}" for k, v in holdings.items() if v.value_usd > 1},
        )

        return snapshot

    def compute_rebalance_plan(
        self,
        portfolio: PortfolioSnapshot,
        target: TargetAllocation,
        prices: dict[str, float],
        leverage: float | None = None,
        short_leverage: float | None = None,
    ) -> RebalancePlan:
        """
        Compute the minimal set of orders to reach target allocation.

        Only generates orders where the deviation exceeds the threshold.
        SELLS are generated before BUYS to free up capital.

        Args:
            portfolio: Current portfolio state.
            target: Desired allocation.
            prices: Current prices by pair.
            leverage: Active leverage from strategy decision (e.g. 10.0x).
            short_leverage: Leverage for bear short hedge (e.g. 2.0x).
        """
        total_value = portfolio.total_value_usd
        if total_value <= 0:
            has_positions = any(
                abs(h.total) > 1e-6
                for sym, h in portfolio.holdings.items()
                if sym not in ("USDT", "USD", "BUSD", "USDC", "BNB")
            )
            if not has_positions:
                logger.warning("Portfolio value is zero and no active positions, no rebalance possible")
                return RebalancePlan(
                    orders=[], current_snapshot=portfolio,
                    target_allocation=target, total_deviation_pct=0.0,
                )

        # In Spot mode:
        # 1. Negative target weights (shorts) are invalid for spot, clamp them to 0.0.
        # 2. Spot total long exposure cannot exceed 1.0 (100%). If strategy target weights
        #    sum to > 1.0 (due to leverage engine in bull mode), normalize crypto weights to 1.0
        #    preserving exact relative asset proportions so no asset is starved or dropped.
        effective_target_weights = dict(target.weights)
        if not self._is_futures:
            crypto_sum = 0.0
            for sym, w in list(effective_target_weights.items()):
                if sym in ("USDT", "USD", "BUSD", "USDC", "BNB"):
                    continue
                if w < 0:
                    effective_target_weights[sym] = 0.0
                else:
                    crypto_sum += w
            if crypto_sum > 1.0:
                scale = 1.0 / crypto_sum
                for sym in list(effective_target_weights.keys()):
                    if sym not in ("USDT", "USD", "BUSD", "USDC", "BNB"):
                        effective_target_weights[sym] = effective_target_weights[sym] * scale
                effective_target_weights["USDT"] = 0.0

        # Compute deviations for all target weights AND untracked portfolio holdings
        all_symbols = set(effective_target_weights.keys())
        for base_asset, h_item in portfolio.holdings.items():
            if base_asset in ("USDT", "USD", "BUSD", "USDC", "BNB"):
                continue
            if abs(h_item.total) > 1e-6:
                # Find matching symbol in target weights (e.g. "BTC/USDT" or "BTC")
                matching_sym = None
                for s in effective_target_weights:
                    if s.startswith(base_asset + "/") or s == base_asset:
                        matching_sym = s
                        break
                if not matching_sym:
                    matching_sym = f"{base_asset}/USDT"
                all_symbols.add(matching_sym)

        deviations: dict[str, float] = {}
        for symbol in all_symbols:
            if symbol in ("USDT", "USD", "BUSD", "USDC", "BNB"):
                continue  # USDT is the residual
            target_weight = effective_target_weights.get(symbol, 0.0)
            base = symbol.split("/")[0].split(":")[0]
            if target_weight == 0.0 and base in effective_target_weights:
                target_weight = effective_target_weights.get(base, 0.0)
            current_weight = portfolio.get_weight(base)
            deviation = target_weight - current_weight
            deviations[symbol] = deviation

        total_deviation = sum(abs(d) for d in deviations.values())

        logger.debug(
            "Allocation deviations: %s (total=%.2f%%)",
            {k: f"{v:+.2%}" for k, v in deviations.items()},
            total_deviation * 100,
        )

        # Generate orders for significant deviations
        sell_orders: list[OrderIntent] = []
        buy_orders: list[OrderIntent] = []

        # Track remaining available quote currency across multiple spot buy orders
        spot_quote_budget: dict[str, float] = {}
        if not self._is_futures:
            for qk in ("USDT", "USD", "BUSD", "USDC"):
                qh = portfolio.holdings.get(qk)
                if qh:
                    spot_quote_budget[qk] = max(0.0, qh.free * 0.998)

        # Deterministic sorting: sells (negative deviation) first so they credit
        # spot_quote_budget before buys consume it, then buys (positive deviation), tie-broken by symbol.
        sorted_dev_items = sorted(deviations.items(), key=lambda x: (0 if x[1] < 0 else 1, x[0]))

        for symbol, deviation in sorted_dev_items:
            pair_symbol = symbol if "/" in symbol else f"{symbol}/USDT"
            target_weight = effective_target_weights.get(symbol, 0.0)
            base = symbol.split("/")[0].split(":")[0]
            if target_weight == 0.0 and base in effective_target_weights:
                target_weight = effective_target_weights.get(base, 0.0)
            current_weight = portfolio.get_weight(base)

            # Skip small deviations unless target is 0.0 and we have an active position to liquidate (long or short)
            holding = portfolio.holdings.get(base)
            has_position = (holding is not None and abs(holding.total) > 1e-6) or abs(current_weight) > 1e-6
            is_full_liquidation = (target_weight == 0.0 and has_position)
            if abs(deviation) < self._deviation_threshold and not is_full_liquidation:
                continue

            price = prices.get(symbol, 0.0)
            if price <= 0 and "/" not in symbol:
                price = prices.get(pair_symbol, 0.0)
            if price <= 0 and ":" in symbol:
                price = prices.get(symbol.split(":")[0], 0.0)
            if price <= 0:
                price = prices.get(f"{pair_symbol}:USDT", 0.0) or prices.get(f"{base}/USDT", 0.0) or prices.get(base, 0.0)
            if price <= 0 and self._gateway:
                try:
                    price = self._gateway.fetch_ticker_price(pair_symbol)
                except Exception:
                    price = 0.0

            if price <= 0:
                logger.warning("No price for %s, skipping", pair_symbol)
                continue

            target_value_usd = abs(deviation) * total_value

            # Get market constraints
            try:
                market_info = self._gateway.get_market_info(pair_symbol)
                constraints = get_market_constraints(market_info, symbol=pair_symbol)
            except Exception:
                base_up = base.upper()
                def_notional = BINANCE_DEFAULT_MIN_NOTIONAL.get(base_up, 10.0) if self._is_futures else 10.0
                constraints = {
                    "amount_precision": 8,
                    "price_precision": 2,
                    "min_amount": 0.00001,
                    "max_amount": float("inf"),
                    "min_price": 0.0,
                    "min_notional": def_notional,
                    "step_size": None,
                }
            step_sz = constraints.get("step_size")

            holding = portfolio.holdings.get(base)
            target_meta = getattr(target, "metadata", {}) or {}
            # Check explicit leverage passed to method first, then target metadata, then target.leverage (if > 1.0)
            if leverage is not None:
                eff_lev = float(leverage)
            elif target_meta.get("effective_leverage") is not None:
                eff_lev = float(target_meta["effective_leverage"])
            elif getattr(target, "leverage", None) is not None and target.leverage > 1.0:
                eff_lev = float(target.leverage)
            else:
                eff_lev = None

            if short_leverage is not None:
                s_lev = float(short_leverage)
            elif target_meta.get("short_leverage") is not None:
                s_lev = float(target_meta["short_leverage"])
            elif eff_lev is not None and eff_lev >= 1.0:
                s_lev = float(eff_lev)
            elif holding and holding.leverage > 1.0:
                s_lev = float(holding.leverage)
            else:
                s_lev = 2.0 if self._is_futures else 1.0

            is_target_bear = (getattr(target.regime, "value", target.regime) == "bear")

            if not self._is_futures:
                pos_lev = 1.0
                s_lev = 1.0
            elif (deviation < 0 or target_weight < 0) and is_target_bear:
                pos_lev = s_lev
            elif eff_lev is not None:
                pos_lev = eff_lev
            elif holding and holding.leverage > 1.0:
                pos_lev = float(holding.leverage)
            else:
                pos_lev = 2.0

            is_flip = (
                holding is not None
                and abs(holding.total) > 1e-6
                and ((current_weight > 1e-6 and target_weight < -1e-6) or (current_weight < -1e-6 and target_weight > 1e-6))
            )

            if is_flip and holding:
                order_type = OrderType.MARKET if self._allow_market_orders else OrderType.LIMIT
                order_price = None if order_type == OrderType.MARKET else price

                # 1. Close current position completely (only if an actual position exists on exchange)
                open_contracts = float(getattr(holding, "contracts", 0.0) or 0.0)
                if open_contracts == 0.0 and abs(holding.total) > 1e-8:
                    open_contracts = holding.total
                raw_close_qty = abs(open_contracts) if self._is_futures else abs(holding.total)

                if raw_close_qty > 1e-8:
                    if step_sz is not None and step_sz > 0:
                        close_amount = truncate_to_step_size(raw_close_qty, step_size=step_sz, precision=constraints["amount_precision"])
                    else:
                        close_amount = truncate_to_precision(raw_close_qty, constraints["amount_precision"])
                    close_side = OrderSide.SELL if (open_contracts > 0 if self._is_futures else current_weight > 0) else OrderSide.BUY
                    close_lev = float(holding.leverage if (holding and holding.leverage > 1.0) else pos_lev)

                    # In Binance Futures, reduce-only orders are allowed below min_notional ("unless you choose reduce only")
                    if is_above_min_order(close_amount, price, constraints["min_amount"], constraints["min_notional"], is_reduce_only=self._is_futures):
                        intent_close = OrderIntent(
                            client_order_id=OrderIntent.generate_id(),
                            symbol=pair_symbol,
                            side=close_side,
                            order_type=order_type,
                            amount=close_amount,
                            price=order_price,
                            estimated_price=price,
                            reason=f"Close previous {current_weight:+.2%} position for regime reversal",
                            candle_ts=target.timestamp_ms,
                            reduce_only=self._is_futures,
                            leverage=close_lev,
                        )
                        if close_side == OrderSide.SELL:
                            sell_orders.append(intent_close)
                        else:
                            buy_orders.append(intent_close)

                # 2. Open new target position in opposite direction
                target_open_val = abs(target_weight) * total_value
                open_amount = compute_order_amount(
                    target_value_usd=target_open_val,
                    price=price,
                    amount_precision=constraints["amount_precision"],
                    min_amount=constraints["min_amount"],
                    min_notional=constraints["min_notional"],
                    step_size=step_sz,
                )
                open_side = OrderSide.SELL if target_weight < 0 else OrderSide.BUY
                open_lev = float(s_lev if (target_weight < 0 and is_target_bear) else pos_lev)
                if open_amount is not None:
                    intent_open = OrderIntent(
                        client_order_id=OrderIntent.generate_id(),
                        symbol=pair_symbol,
                        side=open_side,
                        order_type=order_type,
                        amount=open_amount,
                        price=order_price,
                        estimated_price=price,
                        reason=f"Open new {target_weight:+.2%} position in {pair_symbol}",
                        candle_ts=target.timestamp_ms,
                        leverage=open_lev,
                    )
                    if open_side == OrderSide.SELL:
                        sell_orders.append(intent_open)
                    else:
                        buy_orders.append(intent_open)
                continue

            if is_full_liquidation:
                if holding:
                    if self._is_futures:
                        open_contracts = float(getattr(holding, "contracts", 0.0) or 0.0)
                        if open_contracts == 0.0 and abs(holding.total) > 1e-8:
                            open_contracts = holding.total
                        raw_qty = abs(open_contracts)
                    else:
                        raw_qty = abs(holding.free)

                    if raw_qty < 1e-8:
                        continue

                    if step_sz is not None and step_sz > 0:
                        full_amount = truncate_to_step_size(raw_qty, step_size=step_sz, precision=constraints["amount_precision"])
                    else:
                        full_amount = truncate_to_precision(raw_qty, constraints["amount_precision"])

                    if is_above_min_order(full_amount, price, constraints["min_amount"], constraints["min_notional"], is_reduce_only=self._is_futures):
                        amount = full_amount
                    else:
                        amount = None
                else:
                    amount = None
            else:
                amount = compute_order_amount(
                    target_value_usd=target_value_usd,
                    price=price,
                    amount_precision=constraints["amount_precision"],
                    min_amount=constraints["min_amount"],
                    min_notional=constraints["min_notional"],
                    step_size=step_sz,
                )

            if amount is None:
                logger.debug(
                    "Order for %s too small ($%.2f), skipping",
                    symbol, target_value_usd,
                )
                continue

            # Check available balance for sells (skip if Futures, as shorts are allowed)
            base = symbol.split("/")[0].split(":")[0]
            if deviation < 0 and not self._is_futures:  # Need to sell in Spot
                holding = portfolio.holdings.get(base)
                available = holding.free if holding else 0.0
                if amount > available:
                    amount = truncate_to_precision(
                        available, constraints["amount_precision"]
                    )
                    if not is_above_min_order(amount, price, constraints["min_amount"], constraints["min_notional"]):
                        logger.debug("Available balance for %s ($%.2f) below exchange minimums, skipping", symbol, amount * price)
                        continue
            elif deviation > 0 and not self._is_futures:  # Budget buys in Spot
                quote_key = symbol.split("/")[1].split(":")[0] if "/" in symbol else "USDT"
                avail_quote = spot_quote_budget.get(quote_key, spot_quote_budget.get("USDT", 0.0))
                if (amount * price) > avail_quote:
                    capped_amount = truncate_to_precision(avail_quote / price, constraints["amount_precision"])
                    if is_above_min_order(capped_amount, price, constraints["min_amount"], constraints["min_notional"]):
                        amount = capped_amount
                    else:
                        logger.debug("Available quote balance ($%.2f) below minimum order for %s, skipping", avail_quote, symbol)
                        continue
                used_budget = amount * price
                if quote_key in spot_quote_budget:
                    spot_quote_budget[quote_key] = max(0.0, spot_quote_budget[quote_key] - used_budget)
                elif "USDT" in spot_quote_budget:
                    spot_quote_budget["USDT"] = max(0.0, spot_quote_budget["USDT"] - used_budget)

            order_type = OrderType.MARKET if self._allow_market_orders else OrderType.LIMIT
            order_price = None if order_type == OrderType.MARKET else price

            if is_full_liquidation and holding:
                if self._is_futures:
                    open_contracts = float(getattr(holding, "contracts", 0.0) or 0.0)
                    if open_contracts == 0.0 and abs(holding.total) > 1e-8:
                        open_contracts = holding.total
                    order_side = OrderSide.SELL if open_contracts > 0 else OrderSide.BUY
                else:
                    order_side = OrderSide.SELL if holding.total > 0 else OrderSide.BUY
            else:
                order_side = OrderSide.SELL if deviation < 0 else OrderSide.BUY

            # Strictly verify whether this order is reducing an active futures contract position
            is_reducing = False
            if self._is_futures and holding:
                open_contracts = float(getattr(holding, "contracts", 0.0) or 0.0)
                if open_contracts == 0.0 and abs(holding.total) > 1e-8:
                    open_contracts = holding.total

                if order_side == OrderSide.SELL and open_contracts > 1e-8:
                    # Selling reduces an existing long position
                    is_reducing = True
                    if amount > open_contracts:
                        if step_sz is not None and step_sz > 0:
                            amount = truncate_to_step_size(open_contracts, step_size=step_sz, precision=constraints["amount_precision"])
                        else:
                            amount = truncate_to_precision(open_contracts, constraints["amount_precision"])
                elif order_side == OrderSide.BUY and open_contracts < -1e-8:
                    # Buying reduces an existing short position
                    is_reducing = True
                    short_qty = abs(open_contracts)
                    if amount > short_qty:
                        if step_sz is not None and step_sz > 0:
                            amount = truncate_to_step_size(short_qty, step_size=step_sz, precision=constraints["amount_precision"])
                        else:
                            amount = truncate_to_precision(short_qty, constraints["amount_precision"])

                if is_reducing:
                    if not is_above_min_order(amount, price, constraints["min_amount"], constraints["min_notional"], is_reduce_only=True):
                        logger.debug("Reduce quantity for %s ($%.2f) below exchange minimums, skipping", symbol, amount * price)
                        continue

            intent = OrderIntent(
                client_order_id=OrderIntent.generate_id(),
                symbol=pair_symbol,
                side=order_side,
                order_type=order_type,
                amount=amount,
                price=order_price,
                estimated_price=price,
                reason=f"Rebalance: {deviation:+.2%} deviation in {pair_symbol}",
                candle_ts=target.timestamp_ms,
                reduce_only=is_reducing,
                leverage=pos_lev,
            )

            if order_side == OrderSide.SELL:
                sell_orders.append(intent)
                if not self._is_futures:
                    quote_key = pair_symbol.split("/")[1].split(":")[0] if "/" in pair_symbol else "USDT"
                    sell_proceeds = amount * price * 0.998
                    if quote_key in spot_quote_budget:
                        spot_quote_budget[quote_key] += sell_proceeds
                    elif "USDT" in spot_quote_budget:
                        spot_quote_budget["USDT"] += sell_proceeds
                    else:
                        spot_quote_budget[quote_key] = sell_proceeds
            else:
                buy_orders.append(intent)

        # Separate reducing vs expanding orders:
        # Sells that reduce longs come before sells that open shorts
        reducing_sells: list[OrderIntent] = []
        expanding_sells: list[OrderIntent] = []
        for o in sell_orders:
            base = o.symbol.split("/")[0].split(":")[0]
            holding = portfolio.holdings.get(base)
            if "Close previous" in o.reason:
                reducing_sells.append(o)
            elif "Open new" in o.reason:
                expanding_sells.append(o)
            elif holding and holding.total > 1e-6:
                reducing_sells.append(o)
            else:
                expanding_sells.append(o)

        # Buys that cover shorts come before buys that open longs
        reducing_buys: list[OrderIntent] = []
        expanding_buys: list[OrderIntent] = []
        for o in buy_orders:
            base = o.symbol.split("/")[0].split(":")[0]
            holding = portfolio.holdings.get(base)
            if "Close previous" in o.reason:
                reducing_buys.append(o)
            elif "Open new" in o.reason:
                expanding_buys.append(o)
            elif holding and holding.total < -1e-6:
                reducing_buys.append(o)
            else:
                expanding_buys.append(o)

        # Sells first (reducing before expanding) to maximize cash proceeds,
        # then buys (covering shorts before expanding longs).
        sorted_sells = reducing_sells + expanding_sells
        sorted_buys = reducing_buys + expanding_buys
        all_orders = sorted_sells + sorted_buys

        if all_orders:
            logger.info(
                "🎯 REBALANCE DECISION: Executing %d sells + %d buys (total deviation=%.2f%%)",
                len(sell_orders), len(buy_orders), total_deviation * 100,
            )
            for o in all_orders:
                logger.info(
                    "  %s %s %.8f @ %.4f ($%.2f) — %s",
                    o.side.value.upper(), o.symbol, o.amount,
                    (o.price or o.estimated_price or 0), 
                    (o.amount * (o.price or o.estimated_price or 0)),
                    o.reason,
                )
        else:
            logger.debug("Portfolio within threshold (total deviation=%.2f%%), no rebalance needed", total_deviation * 100)

        return RebalancePlan(
            orders=all_orders,
            current_snapshot=portfolio,
            target_allocation=target,
            total_deviation_pct=total_deviation,
        )
