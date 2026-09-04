"""
Exchange Gateway — single point of contact with Binance via CCXT.

ALL exchange communication flows through this class.  No other module
may import or use ccxt directly.

Features:
- Automatic retry with exponential backoff for transient errors
- Error classification (transient vs permanent)
- Rate limit compliance
- TESTNET endpoint switching
- Thread-safe (single-threaded bot, but safe for future use)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import ccxt

from src.config.config_manager import ExchangeConfig
from src.core.enums import OrderSide, OrderStatus, OrderType, RunMode
from src.core.exceptions import (
    ExchangeAuthError,
    ExchangeConnectionError,
    ExchangeNotAvailableError,
    ExchangeRateLimitError,
    InsufficientBalanceError,
    InvalidOrderError,
)
from src.core.models import Candle, OrderIntent, OrderResult

logger = logging.getLogger("bot.exchange")

# Binance testnet URL
_TESTNET_URLS = {
    "binance": {
        "api": "https://testnet.binance.vision/api",
    }
}

# Transient errors worth retrying
_TRANSIENT_ERRORS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.ExchangeNotAvailable,
    ccxt.DDoSProtection,
)


class ExchangeGateway:
    """
    Thin, resilient wrapper around CCXT for all Binance interactions.

    Usage:
        gw = ExchangeGateway(exchange_config, run_mode)
        gw.initialize()
        candles = gw.fetch_ohlcv("BTC/USDT", "4h", limit=100)
    """

    def __init__(self, config: ExchangeConfig, run_mode: RunMode):
        self._config = config
        self._run_mode = run_mode
        self._exchange: Optional[ccxt.Exchange] = None
        self._markets: Dict[str, Any] = {}
        self._initialized = False

    def initialize(self) -> None:
        """Create the CCXT exchange instance and load markets."""
        options: Dict[str, Any] = {
            "enableRateLimit": self._config.rate_limit,
            "timeout": self._config.timeout_ms,
            "defaultType": self._config.market_type,
        }

        # API credentials (not needed for DRY_RUN with no real calls)
        api_key = self._config.api_key if self._config.api_key else None
        api_secret = self._config.api_secret if self._config.api_secret else None

        self._exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": options,
            "enableRateLimit": self._config.rate_limit,
            "timeout": self._config.timeout_ms,
        })

        # Switch to testnet if needed
        if self._run_mode == RunMode.TESTNET:
            testnet = _TESTNET_URLS.get("binance", {})
            if testnet:
                self._exchange.urls["api"] = testnet.get("api", self._exchange.urls["api"])
                self._exchange.set_sandbox_mode(True)
                logger.info("Exchange configured for TESTNET mode")

        # Load markets
        self._retry(lambda: self._exchange.load_markets())
        self._markets = self._exchange.markets
        self._initialized = True
        logger.info(
            "Exchange initialized: %s | Markets loaded: %d",
            self._config.name,
            len(self._markets),
        )

    @property
    def exchange(self) -> ccxt.Exchange:
        if not self._initialized or self._exchange is None:
            raise ExchangeConnectionError("Exchange not initialized. Call initialize() first.")
        return self._exchange

    @property
    def markets(self) -> Dict[str, Any]:
        return self._markets

    # ── Market Data ──────────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """
        Fetch OHLCV candles.

        Returns a list of Candle objects sorted chronologically.
        The last candle may be incomplete (is_closed=False).
        """
        raw = self._retry(
            lambda: self.exchange.fetch_ohlcv(
                symbol, timeframe, since=since_ms, limit=limit
            )
        )

        candles: List[Candle] = []
        now_ms = int(time.time() * 1000)
        tf_ms = self._timeframe_to_ms(timeframe)

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

        return candles

    def fetch_ohlcv_all(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        Paginated fetch to get more than the default limit of candles.

        Fetches in batches and concatenates results.
        """
        all_candles: List[Candle] = []
        current_since = since_ms
        batch_size = min(limit, 1000)  # Binance max per request
        remaining = limit

        while remaining > 0:
            fetch_limit = min(batch_size, remaining)
            batch = self.fetch_ohlcv(symbol, timeframe, since_ms=current_since, limit=fetch_limit)
            if not batch:
                break

            all_candles.extend(batch)
            remaining -= len(batch)

            if len(batch) < fetch_limit:
                break  # No more data available

            # Move to next page
            current_since = batch[-1].timestamp_ms + 1

        # Deduplicate by timestamp
        seen = set()
        unique: List[Candle] = []
        for c in all_candles:
            if c.timestamp_ms not in seen:
                seen.add(c.timestamp_ms)
                unique.append(c)

        return sorted(unique, key=lambda c: c.timestamp_ms)

    # ── Account ──────────────────────────────────────────────

    def fetch_balance(self) -> Dict[str, Dict[str, float]]:
        """
        Fetch account balances.

        Returns dict of {currency: {"free": x, "used": y, "total": z}}.
        """
        raw = self._retry(lambda: self.exchange.fetch_balance())
        result: Dict[str, Dict[str, float]] = {}
        for currency, balance in raw.items():
            if isinstance(balance, dict) and "free" in balance:
                total = float(balance.get("total", 0) or 0)
                if total > 0:
                    result[currency] = {
                        "free": float(balance.get("free", 0) or 0),
                        "used": float(balance.get("used", 0) or 0),
                        "total": total,
                    }
        return result

    def fetch_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch active futures positions.
        """
        if self._config.market_type != "future":
            return []
            
        try:
            positions = self._retry(lambda: self.exchange.fetch_positions())
            return [p for p in positions if abs(float(p.get("contracts", 0) or 0)) > 0]
        except AttributeError:
            # exchange doesn't support fetch_positions
            return []

    def fetch_ticker_price(self, symbol: str) -> float:
        """Fetch the last traded price for a symbol."""
        ticker = self._retry(lambda: self.exchange.fetch_ticker(symbol))
        return float(ticker.get("last", 0) or ticker.get("close", 0))

    def fetch_ticker_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Fetch prices for multiple symbols."""
        prices: Dict[str, float] = {}
        for symbol in symbols:
            try:
                prices[symbol] = self.fetch_ticker_price(symbol)
            except Exception as e:
                logger.warning("Failed to fetch price for %s: %s", symbol, e)
        return prices

    # ── Orders ───────────────────────────────────────────────

    def create_order(self, intent: OrderIntent) -> OrderResult:
        """
        Submit an order to the exchange.

        Uses clientOrderId (newClientOrderId) for idempotency.
        """
        params: Dict[str, Any] = {
            "newClientOrderId": intent.client_order_id,
        }

        try:
            raw = self._retry(lambda: self.exchange.create_order(
                symbol=intent.symbol,
                type=intent.order_type.value,
                side=intent.side.value,
                amount=intent.amount,
                price=intent.price,
                params=params,
            ))

            return OrderResult(
                client_order_id=intent.client_order_id,
                exchange_order_id=str(raw.get("id", "")),
                status=self._map_order_status(raw.get("status", "")),
                filled_amount=float(raw.get("filled", 0) or 0),
                average_price=float(raw.get("average", 0) or raw.get("price", 0) or 0),
                fees=float(raw.get("fee", {}).get("cost", 0) or 0) if raw.get("fee") else 0.0,
                fee_currency=str(raw.get("fee", {}).get("currency", "")) if raw.get("fee") else "",
                timestamp_ms=int(raw.get("timestamp", 0) or 0),
                raw_response=raw,
            )
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(str(e))
        except (ccxt.InvalidOrder, ccxt.BadRequest) as e:
            raise InvalidOrderError(str(e))

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Cancel an open order."""
        try:
            raw = self._retry(lambda: self.exchange.cancel_order(order_id, symbol))
            return OrderResult(
                client_order_id=raw.get("clientOrderId", ""),
                exchange_order_id=str(raw.get("id", order_id)),
                status=OrderStatus.CANCELLED,
                raw_response=raw,
            )
        except ccxt.OrderNotFound:
            return OrderResult(
                client_order_id="",
                exchange_order_id=order_id,
                status=OrderStatus.CANCELLED,
                error_message="Order not found (already cancelled or filled)",
            )

    def fetch_order(self, symbol: str, order_id: str) -> OrderResult:
        """Get current status of an order."""
        raw = self._retry(lambda: self.exchange.fetch_order(order_id, symbol))
        return OrderResult(
            client_order_id=raw.get("clientOrderId", ""),
            exchange_order_id=str(raw.get("id", "")),
            status=self._map_order_status(raw.get("status", "")),
            filled_amount=float(raw.get("filled", 0) or 0),
            average_price=float(raw.get("average", 0) or raw.get("price", 0) or 0),
            fees=float(raw.get("fee", {}).get("cost", 0) or 0) if raw.get("fee") else 0.0,
            fee_currency=str(raw.get("fee", {}).get("currency", "")) if raw.get("fee") else "",
            timestamp_ms=int(raw.get("timestamp", 0) or 0),
            raw_response=raw,
        )

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """List open orders on the exchange."""
        raw_list = self._retry(lambda: self.exchange.fetch_open_orders(symbol))
        results: List[OrderResult] = []
        for raw in raw_list:
            results.append(OrderResult(
                client_order_id=raw.get("clientOrderId", ""),
                exchange_order_id=str(raw.get("id", "")),
                status=self._map_order_status(raw.get("status", "")),
                filled_amount=float(raw.get("filled", 0) or 0),
                average_price=float(raw.get("average", 0) or raw.get("price", 0) or 0),
                timestamp_ms=int(raw.get("timestamp", 0) or 0),
                raw_response=raw,
            ))
        return results

    # ── Precision helpers ────────────────────────────────────

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        """Apply exchange-specific amount precision."""
        return float(self.exchange.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        """Apply exchange-specific price precision."""
        return float(self.exchange.price_to_precision(symbol, price))

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """Get market info for a symbol."""
        if symbol in self._markets:
            return self._markets[symbol]
        raise InvalidOrderError(f"Symbol {symbol} not found in loaded markets.")

    # ── Internal helpers ─────────────────────────────────────

    def _retry(self, func, max_retries: Optional[int] = None):
        """Execute *func* with exponential backoff on transient errors."""
        retries = max_retries or self._config.max_retries
        delay_ms = self._config.retry_delay_base_ms

        for attempt in range(retries + 1):
            try:
                return func()
            except ccxt.AuthenticationError as e:
                raise ExchangeAuthError(f"Authentication failed: {e}")
            except ccxt.RateLimitExceeded as e:
                if attempt < retries:
                    wait = (delay_ms * (2 ** attempt)) / 1000
                    logger.warning(
                        "Rate limited, waiting %.1fs (attempt %d/%d)",
                        wait, attempt + 1, retries,
                    )
                    time.sleep(wait)
                else:
                    raise ExchangeRateLimitError(str(e))
            except _TRANSIENT_ERRORS as e:
                if attempt < retries:
                    wait = (delay_ms * (2 ** attempt)) / 1000
                    logger.warning(
                        "Transient error: %s, retrying in %.1fs (attempt %d/%d)",
                        type(e).__name__, wait, attempt + 1, retries,
                    )
                    time.sleep(wait)
                else:
                    raise ExchangeConnectionError(
                        f"Exchange unreachable after {retries} retries: {e}"
                    )
            except (ccxt.BadSymbol, ccxt.SymbolNotFound) as e:
                raise InvalidOrderError(f"Symbol not supported or invalid: {e}")
            except (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadRequest):
                raise  # Permanent errors — don't retry
            except ccxt.BaseError as e:
                logger.error("Unexpected CCXT error: %s", e)
                raise ExchangeConnectionError(str(e))

    @staticmethod
    def _map_order_status(status_str: str) -> OrderStatus:
        """Map CCXT order status string to our enum."""
        mapping = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.FAILED,
        }
        return mapping.get(status_str.lower(), OrderStatus.UNKNOWN)

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string to milliseconds."""
        units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        for suffix, ms in units.items():
            if timeframe.endswith(suffix):
                return int(timeframe[:-len(suffix)]) * ms
        raise ValueError(f"Cannot parse timeframe: {timeframe}")
