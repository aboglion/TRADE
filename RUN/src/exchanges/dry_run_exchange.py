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
from typing import Any, Dict, List, Optional

from src.core.enums import OrderSide, OrderStatus, OrderType
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
        initial_balances: Optional[Dict[str, float]] = None,
        fee_rate: float = 0.001,  # 0.1% taker fee
    ):
        self._balances: Dict[str, Dict[str, float]] = {}
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._last_prices: Dict[str, float] = {}
        self._fee_rate = fee_rate
        self._markets: Dict[str, Any] = {}

        # Initialize default balances
        defaults = initial_balances or {"USDT": 1000.0}
        for currency, amount in defaults.items():
            self._balances[currency] = {
                "free": amount,
                "used": 0.0,
                "total": amount,
            }

        logger.info(
            "DryRunExchange initialized with balances: %s",
            {k: v["total"] for k, v in self._balances.items()},
        )

    # ── Market Data ──────────────────────────────────────────

    def set_price(self, symbol: str, price: float) -> None:
        """Set the current price for a symbol (used by candle service)."""
        self._last_prices[symbol] = price

    def fetch_ticker_price(self, symbol: str) -> float:
        if symbol in self._last_prices:
            return self._last_prices[symbol]
        # Fallback to fetching live price via ccxt
        try:
            import ccxt
            ex = ccxt.binance({"enableRateLimit": True})
            ticker = ex.fetch_ticker(symbol)
            price = float(ticker.get("last", 0) or ticker.get("close", 0))
            self._last_prices[symbol] = price
            return price
        except Exception:
            return 0.0

    def fetch_ticker_prices(self, symbols: List[str]) -> Dict[str, float]:
        return {s: self.fetch_ticker_price(s) for s in symbols}

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """Fetch real public candles from Binance in dry run mode."""
        try:
            import ccxt
            ex = ccxt.binance({"enableRateLimit": True})
            raw = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
            candles: List[Candle] = []
            now_ms = int(time.time() * 1000)
            tf_ms = 14_400_000 if timeframe == "4h" else 60_000

            for row in raw:
                ts, o, h, l, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
                closed = (now_ms >= ts + tf_ms)
                candles.append(Candle(
                    timestamp_ms=int(ts),
                    open=float(o),
                    high=float(h),
                    low=float(l),
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
        since_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """Paginated fetch for dry run mode."""
        all_candles: List[Candle] = []
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
        unique: List[Candle] = []
        for c in all_candles:
            if c.timestamp_ms not in seen:
                seen.add(c.timestamp_ms)
                unique.append(c)

        return sorted(unique, key=lambda c: c.timestamp_ms)

    # ── Account ──────────────────────────────────────────────

    def fetch_balance(self) -> Dict[str, Dict[str, float]]:
        return dict(self._balances)

    # ── Orders ───────────────────────────────────────────────

    def create_order(self, intent: OrderIntent) -> OrderResult:
        """Simulate order execution."""
        base, quote = self._parse_symbol(intent.symbol)
        price = intent.price or self._last_prices.get(intent.symbol, 0.0)

        if price <= 0:
            return OrderResult(
                client_order_id=intent.client_order_id,
                status=OrderStatus.FAILED,
                error_message=f"No price available for {intent.symbol}",
            )

        cost = intent.amount * price
        fee = cost * self._fee_rate

        # Check balances
        if intent.side == OrderSide.BUY:
            available = self._get_free(quote)
            needed = cost + fee
            if available < needed:
                return OrderResult(
                    client_order_id=intent.client_order_id,
                    status=OrderStatus.FAILED,
                    error_message=f"Insufficient {quote}: need {needed:.4f}, have {available:.4f}",
                )
            # Deduct quote, add base
            self._adjust_balance(quote, -needed)
            self._adjust_balance(base, intent.amount)
        else:  # SELL
            available = self._get_free(base)
            if available < intent.amount:
                return OrderResult(
                    client_order_id=intent.client_order_id,
                    status=OrderStatus.FAILED,
                    error_message=f"Insufficient {base}: need {intent.amount:.8f}, have {available:.8f}",
                )
            # Deduct base, add quote
            self._adjust_balance(base, -intent.amount)
            self._adjust_balance(quote, cost - fee)

        exchange_id = f"dry_{uuid.uuid4().hex[:12]}"
        result = OrderResult(
            client_order_id=intent.client_order_id,
            exchange_order_id=exchange_id,
            status=OrderStatus.FILLED,
            filled_amount=intent.amount,
            average_price=price,
            fees=fee,
            fee_currency=quote if intent.side == OrderSide.BUY else base,
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

        return result

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        return OrderResult(
            client_order_id="",
            exchange_order_id=order_id,
            status=OrderStatus.CANCELLED,
        )

    def fetch_order(self, symbol: str, order_id: str) -> OrderResult:
        entry = self._orders.get(order_id)
        if entry:
            return entry["result"]
        return OrderResult(
            client_order_id="",
            exchange_order_id=order_id,
            status=OrderStatus.UNKNOWN,
            error_message="Order not found in dry-run store",
        )

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        # Dry-run fills instantly, so there are never open orders
        return []

    # ── Precision (passthrough for dry-run) ──────────────────

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        # In dry-run we accept 8 decimal places
        return round(amount, 8)

    def price_to_precision(self, symbol: str, price: float) -> float:
        return round(price, 2)

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        return self._markets.get(symbol, {
            "precision": {"amount": 8, "price": 2},
            "limits": {
                "amount": {"min": 0.00001},
                "cost": {"min": 10.0},
                "price": {"min": 0.01},
            },
        })

    # ── Internal ─────────────────────────────────────────────

    def _get_free(self, currency: str) -> float:
        bal = self._balances.get(currency)
        return bal["free"] if bal else 0.0

    def _adjust_balance(self, currency: str, delta: float) -> None:
        if currency not in self._balances:
            self._balances[currency] = {"free": 0.0, "used": 0.0, "total": 0.0}
        self._balances[currency]["free"] += delta
        self._balances[currency]["total"] += delta

    @staticmethod
    def _parse_symbol(symbol: str):
        """Split 'BTC/USDT' into ('BTC', 'USDT')."""
        parts = symbol.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid symbol format: {symbol}")
        return parts[0], parts[1]
