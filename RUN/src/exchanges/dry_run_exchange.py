"""
Dry-run exchange simulator.

Implements the same interface as ExchangeGateway but never touches
the network.  Maintains virtual balances and simulates fills at
current market prices.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from src.core.enums import OrderSide, OrderStatus
from src.core.models import Candle, OrderIntent, OrderResult

logger = logging.getLogger("bot.dry_run")


class DryRunExchange:
    """
    Simulated exchange for DRY_RUN mode.

    Fills market orders instantly at the last known price.
    Limit orders are filled if price crosses during the current candle.
    """

    def __init__(
        self,
        initial_balances: dict[str, float] | None = None,
        fee_rate: float = 0.001,  # 0.1% taker fee
        on_balance_change: Any | None = None,
    ):
        self._balances: dict[str, dict[str, float]] = {}
        self._positions: dict[str, dict[str, Any]] = {}  # Futures positions
        self._orders: dict[str, dict[str, Any]] = {}
        self._last_prices: dict[str, float] = {}
        self._fee_rate = fee_rate
        self._markets: dict[str, Any] = {}
        self._on_balance_change = on_balance_change
        self._current_leverage: dict[str, float] = {}

        self._public_exchange: Any | None = None

        # Initialize default balances
        defaults = initial_balances or {"USDT": 1000.0}
        for currency, amount in defaults.items():
            self._balances[currency] = {
                "free": float(amount),
                "used": 0.0,
                "total": float(amount),
            }
        if "USDT" not in self._balances:
            self._balances["USDT"] = {"free": 0.0, "used": 0.0, "total": 0.0}

        logger.info(
            "DryRunExchange initialized with balances: %s",
            {k: v["total"] for k, v in self._balances.items()},
        )

    def _get_public_exchange(self) -> Any | None:
        if self._public_exchange is None:
            try:
                import ccxt
                self._public_exchange = ccxt.binance({"enableRateLimit": True, "timeout": 10000})
            except Exception:
                return None
        return self._public_exchange

    def _notify_balance_change(self) -> None:
        if callable(self._on_balance_change):
            try:
                # Report all balances including negative ones (realized losses
                # in futures mode can temporarily make USDT balance negative).
                simplified = {k: round(v["total"], 8) for k, v in self._balances.items() if abs(v["total"]) > 1e-10}
                self._on_balance_change(simplified)
            except Exception as e:
                logger.error("Error in on_balance_change callback: %s", e)

    # ── Market Data ──────────────────────────────────────────

    def set_price(self, symbol: str, price: float) -> None:
        """Set the current price for a symbol (used by candle service)."""
        self._last_prices[symbol] = price

    def fetch_ticker_price(self, symbol: str) -> float:
        if symbol in self._last_prices:
            return self._last_prices[symbol]
        # Fallback to fetching live price via ccxt
        try:
            ex = self._get_public_exchange()
            if ex:
                ticker = ex.fetch_ticker(symbol)
                price = float(ticker.get("last", 0) or ticker.get("close", 0))
                self._last_prices[symbol] = price
                return price
            return 0.0
        except Exception:
            return 0.0

    def fetch_ticker_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.fetch_ticker_price(s) for s in symbols}

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch real public candles from Binance in dry run mode."""
        try:
            ex = self._get_public_exchange()
            if not ex:
                return []
            raw = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
            candles: list[Candle] = []
            now_ms = int(time.time() * 1000)
            tf_ms = self._timeframe_to_ms(timeframe)

            for row in raw:
                ts, o, h, lo, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
                closed = (now_ms >= ts + tf_ms)
                candles.append(Candle(
                    timestamp_ms=int(ts),
                    open=float(o),
                    high=float(h),
                    low=float(lo),
                    close=float(c),
                    volume=float(v) if v else 0.0,
                    is_closed=closed,
                ))
            if candles:
                self._last_prices[symbol] = candles[-1].close
            return candles
        except Exception as e:
            logger.error("Failed to fetch public candles for %s in dry run: %s", symbol, e)
            return []

    def fetch_ohlcv_all(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Paginated fetch for dry run mode."""
        all_candles: list[Candle] = []
        current_since = since_ms
        batch_size = min(limit, 1000)
        remaining = limit

        while remaining > 0:
            fetch_limit = min(batch_size, remaining)
            batch = self.fetch_ohlcv(symbol, timeframe, since_ms=current_since, limit=fetch_limit)
            if not batch:
                break
            all_candles.extend(batch)
            remaining -= len(batch)
            if len(batch) < fetch_limit:
                break
            current_since = batch[-1].timestamp_ms + 1

        seen = set()
        unique: list[Candle] = []
        for c in all_candles:
            if c.timestamp_ms not in seen:
                seen.add(c.timestamp_ms)
                unique.append(c)

        return sorted(unique, key=lambda c: c.timestamp_ms)

    # ── Account ──────────────────────────────────────────────

    def fetch_balance(self) -> dict[str, dict[str, float]]:
        # In futures, update USDT total based on unrealized PnL and compute locked margin
        bal = dict(self._balances)
        if "USDT" in bal:
            active_positions = self.fetch_positions()
            unrealized = sum(p.get("unrealizedPnl", 0.0) for p in active_positions)
            used_margin = sum(
                abs(float(p.get("contracts", 0.0))) * float(p.get("entryPrice", 0.0)) / max(1.0, float(p.get("leverage", 1.0)))
                for p in active_positions
            )
            raw_wallet = bal["USDT"]["free"] + bal["USDT"]["used"]
            margin_balance = raw_wallet + unrealized
            free_margin = max(0.0, margin_balance - used_margin)
            bal["USDT"] = {
                "free": free_margin,
                "used": used_margin,
                "total": margin_balance,
            }
        return bal

    def fetch_positions(self) -> list[dict[str, Any]]:
        # Update unrealized PnL dynamically
        positions_list = []
        for symbol, pos in self._positions.items():
            if pos["contracts"] == 0:
                continue
            
            price = self._last_prices.get(symbol)
            if price is None or price <= 0:
                clean_sym = symbol.split(":")[0] if ":" in symbol else symbol
                price = self._last_prices.get(clean_sym)
            if price is None or price <= 0:
                price = self.fetch_ticker_price(symbol)
            if price <= 0:
                price = float(pos.get("entryPrice", 0.0) or 0.0)

            if pos["side"] == "long":
                pnl = (price - pos["entryPrice"]) * pos["contracts"]
            else:
                pnl = (pos["entryPrice"] - price) * abs(pos["contracts"])
                
            pos["unrealizedPnl"] = pnl
            positions_list.append(dict(pos))
            
        return positions_list

    def set_balances(self, balances: dict[str, float]) -> None:
        """Update or reset simulated holdings in Dry Run mode."""
        self._balances.clear()
        for currency, amount in balances.items():
            curr_upper = currency.upper()
            amt_float = max(0.0, float(amount))
            self._balances[curr_upper] = {
                "free": amt_float,
                "used": 0.0,
                "total": amt_float,
            }
        logger.info(
            "[DRY_RUN] Balances updated: %s",
            {k: v["total"] for k, v in self._balances.items()},
        )
        self._notify_balance_change()

    def set_leverage(self, leverage: float, symbol: str) -> None:
        """Set leverage for a simulated futures market symbol."""
        lev_float = float(leverage)
        if self._current_leverage.get(symbol) == lev_float:
            return
        self._current_leverage[symbol] = lev_float
        logger.debug("[DRY_RUN] Set leverage=%.1fx for %s", leverage, symbol)

    def _get_active_leverage(self, symbol: str) -> float:
        levs = getattr(self, "_current_leverage", {})
        if symbol in levs:
            return float(levs[symbol])
        clean_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if clean_sym in levs:
            return float(levs[clean_sym])
        if f"{clean_sym}:USDT" in levs:
            return float(levs[f"{clean_sym}:USDT"])
        if f"{clean_sym}/USDT" in levs:
            return float(levs[f"{clean_sym}/USDT"])
        if f"{clean_sym}/USDT:USDT" in levs:
            return float(levs[f"{clean_sym}/USDT:USDT"])
        if "/" in clean_sym:
            base = clean_sym.split("/")[0]
            if base in levs:
                return float(levs[base])
        return float(levs.get("default", 3.5))

    # ── Orders ───────────────────────────────────────────────

    def create_order(self, intent: OrderIntent) -> OrderResult:
        """Simulate order execution."""
        base, quote = self._parse_symbol(intent.symbol)
        price = intent.price or intent.estimated_price or self._last_prices.get(intent.symbol, 0.0)

        if price <= 0:
            return OrderResult(
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                status=OrderStatus.FAILED,
                error_message=f"No price available for {intent.symbol}",
            )

        cost = intent.amount * price
        fee = cost * self._fee_rate

        # Basic margin/balance check
        quote = intent.symbol.split("/")[1] if "/" in intent.symbol else "USDT"
        is_futures = True  # Strategy operates in futures mode
        leverage = getattr(intent, "leverage", 1.0)
        if not leverage or leverage <= 1.0:
            leverage = self._get_active_leverage(intent.symbol)
        margin_req = cost / leverage if is_futures else cost
        total_collateral = self._calculate_total_collateral()
        available_margin = max(self._get_free(quote), total_collateral)

        # Determine expanding amount for margin requirement
        existing_pos = self._get_pos(intent.symbol)
        existing_contracts = float(existing_pos.get("contracts", 0.0) or 0.0)
        
        if intent.side == OrderSide.BUY:
            if existing_contracts >= 0:
                expanding_qty = intent.amount
            else:
                expanding_qty = max(0.0, intent.amount - abs(existing_contracts))
        else:  # SELL
            if existing_contracts <= 0:
                expanding_qty = intent.amount
            else:
                expanding_qty = max(0.0, intent.amount - existing_contracts)

        expanding_cost = expanding_qty * price
        margin_req = expanding_cost / leverage if is_futures else expanding_cost

        if expanding_qty > 0 and margin_req > (available_margin + 1e-4):
             return OrderResult(
                 client_order_id=intent.client_order_id,
                 symbol=intent.symbol,
                 status=OrderStatus.FAILED,
                 error_message=f"Insufficient balance: need {margin_req:.2f} {quote} margin (available={available_margin:.2f}, cost={expanding_cost:.2f})",
             )

        # Handle Futures / Spot balance execution
        base, quote = self._parse_symbol(intent.symbol)
        futures_amount = intent.amount

        # Check if spot balance exists for base asset when selling
        if intent.side == OrderSide.SELL:
            spot_bal = self._balances.get(base, {}).get("total", 0.0)
            if spot_bal > 0:
                sell_spot = min(spot_bal, intent.amount)
                self._balances[base]["free"] = max(0.0, self._balances[base]["free"] - sell_spot)
                self._balances[base]["total"] = max(0.0, self._balances[base]["total"] - sell_spot)
                spot_proceeds = (sell_spot * price) * (1.0 - self._fee_rate)
                self._adjust_balance(quote, spot_proceeds)
                futures_amount = intent.amount - sell_spot

        is_futures = True  # We migrated to futures
        
        if is_futures and futures_amount > 0:
            active_lev = getattr(intent, "leverage", 1.0)
            if not active_lev or active_lev <= 1.0:
                active_lev = self._get_active_leverage(intent.symbol)
            pos = dict(self._get_pos(intent.symbol))
            if not pos:
                pos = {
                    "symbol": intent.symbol,
                    "contracts": 0.0,
                    "entryPrice": 0.0,
                    "side": "neutral",
                    "unrealizedPnl": 0.0,
                    "leverage": active_lev,
                }
            pos["leverage"] = active_lev
            
            # Calculate realized PnL if closing/reducing
            realized_pnl = 0.0
            contracts_before = pos["contracts"]
            qty_delta = futures_amount if intent.side == OrderSide.BUY else -futures_amount
            
            # Simple average entry price logic for adding to or opening position
            if contracts_before == 0:
                pos["entryPrice"] = price
            elif (contracts_before > 0 and qty_delta > 0) or (contracts_before < 0 and qty_delta < 0):
                total_cost = (abs(contracts_before) * pos["entryPrice"]) + (futures_amount * price)
                pos["entryPrice"] = total_cost / (abs(contracts_before) + futures_amount)
            elif contracts_before != 0:
                # Reducing position
                reduce_qty = min(abs(contracts_before), futures_amount)
                if contracts_before > 0:
                    realized_pnl = (price - pos["entryPrice"]) * reduce_qty
                else:
                    realized_pnl = (pos["entryPrice"] - price) * reduce_qty
                
                # If flipped side, update entry price for remainder
                if futures_amount > abs(contracts_before):
                    pos["entryPrice"] = price
                    
            pos["contracts"] += qty_delta
            if abs(pos["contracts"]) < 1e-8:
                pos["contracts"] = 0.0
                pos["entryPrice"] = 0.0
                pos["side"] = "neutral"
                pos["unrealizedPnl"] = 0.0
            elif pos["contracts"] > 0:
                pos["side"] = "long"
            else:
                pos["side"] = "short"
                
            self._positions[intent.symbol] = pos
            
            # Apply realized PnL and fees to USDT balance.
            # Fee is computed ONLY on the futures portion to avoid double-charging
            # (spot sell already deducted fee from spot_proceeds above).
            futures_fee = (futures_amount * price) * self._fee_rate
            self._adjust_balance("USDT", realized_pnl - futures_fee)
            quote = "USDT"

        exchange_id = f"dry_{uuid.uuid4().hex[:12]}"
        result = OrderResult(
            client_order_id=intent.client_order_id,
            exchange_order_id=exchange_id,
            symbol=intent.symbol,
            status=OrderStatus.FILLED,
            filled_amount=intent.amount,
            average_price=price,
            fees=fee,
            fee_currency=quote,
            timestamp_ms=int(time.time() * 1000),
        )

        self._orders[exchange_id] = {
            "intent": intent,
            "result": result,
        }

        logger.info(
            "[DRY_RUN] %s %s %.8f @ %.4f | Fee: %.4f %s | Balances: %s",
            intent.side.value.upper(),
            intent.symbol,
            intent.amount,
            price,
            fee,
            result.fee_currency,
            {k: round(v["total"], 4) for k, v in self._balances.items() if v["total"] > 0},
        )
        self._notify_balance_change()

        return result

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        return OrderResult(
            client_order_id="",
            exchange_order_id=order_id,
            symbol=symbol,
            status=OrderStatus.CANCELLED,
        )

    def fetch_order(self, symbol: str, order_id: str) -> OrderResult:
        entry = self._orders.get(order_id)
        if not entry:
            for ord_data in self._orders.values():
                res = ord_data.get("result")
                intent = ord_data.get("intent")
                if (res and res.client_order_id == order_id) or (intent and intent.client_order_id == order_id):
                    entry = ord_data
                    break
        if entry:
            return entry["result"]
        return OrderResult(
            client_order_id=order_id if not str(order_id).startswith("dry_") else "",
            exchange_order_id=order_id,
            symbol=symbol,
            status=OrderStatus.UNKNOWN,
            error_message="Order not found in dry-run store",
        )

    def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        # Dry-run fills instantly, so there are never open orders
        return []

    # ── Precision (passthrough for dry-run) ──────────────────

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        # In dry-run we accept 8 decimal places
        return round(amount, 8)

    def price_to_precision(self, symbol: str, price: float) -> float:
        return round(price, 2)

    def get_market_info(self, symbol: str) -> dict[str, Any]:
        return self._markets.get(symbol, {
            "precision": {"amount": 8, "price": 2},
            "limits": {
                "amount": {"min": 0.00001},
                "cost": {"min": 10.0},
                "price": {"min": 0.01},
            },
        })

    # ── Internal ─────────────────────────────────────────────

    def _get_pos(self, symbol: str) -> dict[str, Any]:
        """Look up position by exact symbol or base symbol without :USDT suffix."""
        if symbol in self._positions:
            return self._positions[symbol]
        clean_sym = symbol.split(":")[0] if ":" in symbol else symbol
        if clean_sym in self._positions:
            return self._positions[clean_sym]
        for k, v in self._positions.items():
            if k.split(":")[0] == clean_sym:
                return v
        return {}

    def _get_free(self, currency: str) -> float:
        bal = self._balances.get(currency)
        return bal["free"] if bal else 0.0

    def _adjust_balance(self, currency: str, delta: float) -> None:
        if currency not in self._balances:
            self._balances[currency] = {"free": 0.0, "used": 0.0, "total": 0.0}
        self._balances[currency]["free"] += delta
        self._balances[currency]["total"] += delta

    def _calculate_total_collateral(self) -> float:
        total = 0.0
        for curr, b in self._balances.items():
            tot = float(b.get("total", b.get("free", 0.0)))
            if curr in ("USDT", "BUSD", "USDC", "USD"):
                total += tot
            else:
                sym = f"{curr}/USDT"
                px = self._last_prices.get(sym, 0.0)
                if px <= 0:
                    px = self.fetch_ticker_price(sym)
                total += tot * px
        unrealized = sum(float(p.get("unrealizedPnl", 0.0)) for p in self.fetch_positions())
        return max(0.0, total + unrealized)

    @staticmethod
    def _parse_symbol(symbol: str):
        """Split 'BTC/USDT' or 'BTC/USDT:USDT' into ('BTC', 'USDT')."""
        clean = symbol.split(":")[0] if ":" in symbol else symbol
        if "/" in clean:
            parts = clean.split("/")
            return parts[0], parts[1]
        for base in ("BTC", "ETH", "SOL", "BNB"):
            if clean.startswith(base):
                return base, clean[len(base):]
        raise ValueError(f"Invalid symbol format: {symbol}")

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string to milliseconds."""
        units = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        for suffix, ms in units.items():
            if timeframe.endswith(suffix):
                return int(timeframe[:-len(suffix)]) * ms
        raise ValueError(f"Cannot parse timeframe: {timeframe}")
