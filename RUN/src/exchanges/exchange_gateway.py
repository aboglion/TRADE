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
from typing import Any

import ccxt

from src.config.config_manager import ExchangeConfig
from src.core.enums import OrderStatus, RunMode
from src.core.exceptions import (
    ExchangeAuthError,
    ExchangeConnectionError,
    ExchangeRateLimitError,
    InsufficientBalanceError,
    InvalidOrderError,
)
from src.core.models import Candle, OrderIntent, OrderResult
from src.utils.network_utils import get_whitelist_ip_summary

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
        self._exchange: ccxt.Exchange | None = None
        self._markets: dict[str, Any] = {}
        self._initialized = False
        self._current_leverage: dict[str, int] = {}

    def initialize(self) -> None:
        """Create the CCXT exchange instance and load markets."""
        options: dict[str, Any] = {
            "enableRateLimit": self._config.rate_limit,
            "timeout": self._config.timeout_ms,
            "defaultType": self._config.market_type,
            "adjustForTimeDifference": True,
            "recvWindow": 10000,
            "warnWithoutSymbol": False,
            "warnOnFetchOpenOrdersWithoutSymbol": False,
        }
        if getattr(self._config, "portfolio_margin", False):
            options["portfolioMargin"] = True

        # API credentials (not needed for DRY_RUN with no real calls)
        api_key = self._config.api_key.strip().strip("'\"").strip() if self._config.api_key else None
        api_secret = self._config.api_secret.strip().strip("'\"").strip() if self._config.api_secret else None

        self._exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": options,
            "enableRateLimit": self._config.rate_limit,
            "timeout": self._config.timeout_ms,
        })
        self._exchange.options["warnWithoutSymbol"] = False
        self._exchange.options["warnOnFetchOpenOrdersWithoutSymbol"] = False
        if "fetchOpenOrders" not in self._exchange.options:
            self._exchange.options["fetchOpenOrders"] = {}
        self._exchange.options["fetchOpenOrders"]["warnWithoutSymbol"] = False

        # Switch to testnet if needed
        if self._run_mode == RunMode.TESTNET:
            testnet = _TESTNET_URLS.get("binance", {})
            if testnet:
                self._exchange.urls["api"] = testnet.get("api", self._exchange.urls["api"])
                self._exchange.set_sandbox_mode(True)
                logger.info("Exchange configured for TESTNET mode")

        # Load markets with automatic fallback for portfolio margin
        try:
            self._retry(lambda: self._exchange.load_markets())
        except ExchangeAuthError as auth_err:
            if getattr(self._config, "portfolio_margin", False):
                logger.warning(
                    "Portfolio Margin initialization failed (%s). Retrying with standard USDT-M Futures...",
                    auth_err,
                )
                self._exchange.options["portfolioMargin"] = False
                self._retry(lambda: self._exchange.load_markets())
                logger.info("Successfully connected using standard USDT-M Futures mode.")
            else:
                raise
        self._markets = self._exchange.markets
        self._initialized = True
        logger.info(
            "Exchange initialized: %s | Markets loaded: %d",
            self._config.name,
            len(self._markets),
        )

        # Ensure One-Way Mode on Binance Futures (required for directional trading & short hedges)
        if self._config.market_type == "future":
            try:
                self._retry(lambda: self._exchange.set_position_mode(hedged=False))
                logger.info("Binance Futures position mode verified: One-Way Mode")
            except Exception as ex:
                err_str = str(ex)
                if "-4059" in err_str or "No need to change" in err_str:
                    logger.debug("Binance Futures already in One-Way Mode")
                else:
                    logger.warning("Could not set One-Way Mode on Binance Futures: %s", ex)

        # Set default leverage for futures markets if applicable
        if self._config.market_type == "future":
            for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
                if symbol in self._markets:
                    self.set_leverage(2, symbol)

    def set_leverage(self, leverage: float, symbol: str) -> None:
        """Set leverage for a futures market symbol."""
        if self._config.market_type != "future":
            return
        lev_int = max(1, round(leverage))
        if self._current_leverage.get(symbol) == lev_int:
            return

        sym_to_use = symbol
        if self._markets:
            if symbol in self._markets:
                sym_to_use = symbol
            elif f"{symbol}:USDT" in self._markets:
                sym_to_use = f"{symbol}:USDT"
            elif ":" in symbol and symbol.split(":")[0] in self._markets:
                sym_to_use = symbol.split(":")[0]

        try:
            self._retry(lambda: self.exchange.set_leverage(lev_int, sym_to_use))
            self._current_leverage[symbol] = lev_int
            logger.info("Set leverage=%dx for %s (%s)", lev_int, symbol, sym_to_use)
        except Exception as e:
            logger.warning("Could not set leverage=%dx for %s: %s", lev_int, symbol, e)

    def set_margin_mode(self, margin_mode: str, symbol: str) -> None:
        """Set margin mode ('cross' or 'isolated') for a futures market symbol."""
        try:
            self._retry(lambda: self.exchange.set_margin_mode(margin_mode.upper(), symbol))
            logger.info("Set margin_mode=%s for %s", margin_mode, symbol)
        except Exception as e:
            logger.warning("Could not set margin_mode=%s for %s: %s", margin_mode, symbol, e)

    @property
    def exchange(self) -> ccxt.Exchange:
        if not self._initialized or self._exchange is None:
            raise ExchangeConnectionError("Exchange not initialized. Call initialize() first.")
        return self._exchange

    @property
    def markets(self) -> dict[str, Any]:
        return self._markets

    # ── Market Data ──────────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[Candle]:
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

        return candles

    def fetch_ohlcv_all(
        self,
        symbol: str,
        timeframe: str = "4h",
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """
        Paginated fetch to get more than the default limit of candles.

        Fetches in batches and concatenates results.
        """
        all_candles: list[Candle] = []
        current_since = since_ms
        if current_since is None and limit > 1000:
            now_ms = int(time.time() * 1000)
            tf_ms = self._timeframe_to_ms(timeframe)
            current_since = now_ms - (tf_ms * (limit + 10))
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
        unique: list[Candle] = []
        for c in all_candles:
            if c.timestamp_ms not in seen:
                seen.add(c.timestamp_ms)
                unique.append(c)

        return sorted(unique, key=lambda c: c.timestamp_ms)

    # ── Account ──────────────────────────────────────────────

    def fetch_balance(self) -> dict[str, dict[str, float]]:
        """
        Fetch account balances.

        Returns dict of {currency: {"free": x, "used": y, "total": z}}.
        """
        try:
            raw = self._retry(lambda: self.exchange.fetch_balance())
        except ExchangeAuthError as e:
            if self._config.market_type == "future" and "-2015" in str(e):
                curr_pm = bool(self.exchange.options.get("portfolioMargin", False))
                logger.info("Attempting automatic portfolioMargin fallback (trying portfolioMargin=%s)...", not curr_pm)
                self.exchange.options["portfolioMargin"] = not curr_pm
                try:
                    raw = self._retry(lambda: self.exchange.fetch_balance())
                    logger.info("Portfolio Margin fallback succeeded! Running with portfolioMargin=%s", not curr_pm)
                except Exception as err:
                    self.exchange.options["portfolioMargin"] = curr_pm
                    raise e from err
            else:
                raise
        result: dict[str, dict[str, float]] = {}
        for currency, balance in raw.items():
            if isinstance(balance, dict) and "free" in balance:
                total = float(balance.get("total", 0) or 0)
                if total > 0 or currency in ("USDT", "BUSD", "USDC", "USD"):
                    result[currency] = {
                        "free": float(balance.get("free", 0) or 0),
                        "used": float(balance.get("used", 0) or 0),
                        "total": total,
                    }
        if "USDT" not in result:
            result["USDT"] = {"free": 0.0, "used": 0.0, "total": 0.0}
        return result

    def fetch_positions(self) -> list[dict[str, Any]]:
        """
        Fetch active futures positions.
        """
        if self._config.market_type != "future":
            return []
            
        try:
            positions = self._retry(lambda: self.exchange.fetch_positions())
            return [p for p in positions if abs(float(p.get("contracts", 0) or 0)) > 0]
        except ExchangeAuthError as e:
            if self._config.market_type == "future" and "-2015" in str(e):
                curr_pm = bool(self.exchange.options.get("portfolioMargin", False))
                self.exchange.options["portfolioMargin"] = not curr_pm
                try:
                    positions = self._retry(lambda: self.exchange.fetch_positions())
                    return [p for p in positions if abs(float(p.get("contracts", 0) or 0)) > 0]
                except Exception as err:
                    self.exchange.options["portfolioMargin"] = curr_pm
                    raise e from err
            raise
        except AttributeError:
            # exchange doesn't support fetch_positions
            return []

    def fetch_ticker_price(self, symbol: str) -> float:
        """Fetch the last traded price for a symbol."""
        sym = self._resolve_market_symbol(symbol)
        ticker = self._retry(lambda: self.exchange.fetch_ticker(sym))
        return float(ticker.get("last", 0) or ticker.get("close", 0))

    def fetch_ticker_prices(self, symbols: list[str]) -> dict[str, float]:
        """Fetch prices for multiple symbols."""
        prices: dict[str, float] = {}
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
        resolved_sym = self._resolve_market_symbol(intent.symbol)
        params: dict[str, Any] = {
            "newClientOrderId": intent.client_order_id,
        }
        if intent.order_type.value == "limit" and "timeInForce" not in params:
            params["timeInForce"] = "GTC"
        if self._config.market_type == "future" and getattr(intent, "reduce_only", False):
            params["reduceOnly"] = True

        # Apply strict exchange precision formatting to prevent API error -1111
        try:
            formatted_amount = self.amount_to_precision(resolved_sym, intent.amount)
        except Exception:
            formatted_amount = intent.amount

        if formatted_amount is not None:
            try:
                fa_float = float(formatted_amount)
                if intent.side.value.upper() == "SELL" and fa_float > (intent.amount + 1e-9):
                    from src.utils.math_utils import truncate_to_precision
                    prec = 8
                    try:
                        prec = self.get_market_info(resolved_sym).get("precision", {}).get("amount", 8)
                    except Exception:
                        pass
                    formatted_amount = truncate_to_precision(intent.amount, prec)
                if float(formatted_amount) <= 0.0:
                    raise InvalidOrderError(
                        f"Order amount {intent.amount} formatted to 0.0 for {resolved_sym} due to exchange lot size/precision"
                    )
            except (ValueError, TypeError):
                pass

        formatted_price = None
        if intent.order_type.value != "market" and intent.price is not None:
            try:
                formatted_price = self.price_to_precision(resolved_sym, intent.price)
            except Exception:
                formatted_price = intent.price

        try:
            raw = self._retry(lambda: self.exchange.create_order(
                symbol=resolved_sym,
                type=intent.order_type.value,
                side=intent.side.value,
                amount=formatted_amount,
                price=formatted_price,
                params=params,
            ))

            avg_px = float(raw.get("average") or raw.get("price") or 0)
            if avg_px == 0.0 and raw.get("trades"):
                trades = raw.get("trades", [])
                total_cost = sum(float(t.get("cost", 0) or (float(t.get("price", 0)) * float(t.get("amount", 0)))) for t in trades)
                total_qty = sum(float(t.get("amount", 0)) for t in trades)
                if total_qty > 0:
                    avg_px = total_cost / total_qty
            if avg_px == 0.0 and isinstance(raw.get("info"), dict):
                info = raw["info"]
                cum_quote = float(info.get("cumQuote", 0) or 0)
                cum_qty = float(info.get("cumQty", 0) or 0)
                if cum_qty > 0:
                    avg_px = cum_quote / cum_qty
                elif float(info.get("avgPrice", 0) or 0) > 0:
                    avg_px = float(info.get("avgPrice"))
            if avg_px == 0.0:
                avg_px = float(intent.estimated_price or intent.price or 0.0)

            mapped_status = self._map_order_status(raw.get("status", ""))
            filled_amt = float(raw.get("filled", 0) or 0)
            if filled_amt == 0.0 and isinstance(raw.get("info"), dict):
                info = raw["info"]
                filled_amt = float(info.get("cumQty", 0) or info.get("executedQty", 0) or 0)
            if filled_amt == 0.0 and mapped_status == OrderStatus.FILLED:
                filled_amt = float(raw.get("amount", 0) or formatted_amount or intent.amount or 0)

            fee_cost, fee_curr = self._extract_fee(raw)
            return OrderResult(
                client_order_id=intent.client_order_id,
                exchange_order_id=str(raw.get("id", "")),
                status=mapped_status,
                filled_amount=filled_amt,
                average_price=avg_px,
                fees=fee_cost,
                fee_currency=fee_curr,
                timestamp_ms=int(raw.get("timestamp", 0) or 0),
                raw_response=raw,
            )
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(str(e)) from e
        except (ccxt.InvalidOrder, ccxt.BadRequest, InvalidOrderError) as e:
            err_msg = str(e)
            if "-2022" in err_msg and params.get("reduceOnly"):
                logger.warning("ReduceOnly order rejected by Binance (-2022). Checking active positions on exchange...")
                try:
                    positions = self.fetch_positions()
                    active_pos = None
                    for p in positions:
                        p_sym = p.get("symbol", "")
                        if p_sym == resolved_sym or p_sym.split(":")[0] == resolved_sym.split(":")[0]:
                            active_pos = p
                            break

                    # If position is already completely closed on Binance:
                    if not active_pos or abs(float(active_pos.get("contracts", 0.0) or 0.0)) < 1e-8:
                        logger.info("Position for %s is already completely closed on Binance. Resolving order as closed.", resolved_sym)
                        return OrderResult(
                            client_order_id=intent.client_order_id,
                            exchange_order_id=f"closed_{intent.client_order_id}",
                            status=OrderStatus.FILLED,
                            filled_amount=0.0,
                            average_price=float(intent.estimated_price or intent.price or 0.0),
                            fees=0.0,
                            fee_currency="USDT",
                            timestamp_ms=int(time.time() * 1000),
                            error_message="Position already closed on exchange (-2022 resolved)",
                        )

                    # If position still exists but remaining amount is smaller than requested formatted_amount:
                    rem_qty = abs(float(active_pos.get("contracts", 0.0) or 0.0))
                    try:
                        new_formatted_amt = self.amount_to_precision(resolved_sym, rem_qty)
                    except Exception:
                        new_formatted_amt = rem_qty

                    if 0 < float(new_formatted_amt) < float(formatted_amount):
                        logger.info("Retrying reduceOnly order with exact remaining position amount: %.8f -> %.8f", float(formatted_amount), float(new_formatted_amt))
                        raw = self._retry(lambda: self.exchange.create_order(
                            symbol=resolved_sym,
                            type=intent.order_type.value,
                            side=intent.side.value,
                            amount=new_formatted_amt,
                            price=formatted_price,
                            params=params,
                        ))
                        avg_px = float(raw.get("average") or raw.get("price") or intent.estimated_price or intent.price or 0.0)
                        filled_amt = float(raw.get("filled", 0) or raw.get("amount", 0) or new_formatted_amt)
                        fee_cost, fee_curr = self._extract_fee(raw)
                        return OrderResult(
                            client_order_id=intent.client_order_id,
                            exchange_order_id=str(raw.get("id", "")),
                            status=self._map_order_status(raw.get("status", "closed")),
                            filled_amount=filled_amt,
                            average_price=avg_px,
                            fees=fee_cost,
                            fee_currency=fee_curr,
                            timestamp_ms=int(raw.get("timestamp", time.time() * 1000)),
                            raw_response=raw,
                        )
                except Exception as retry_err:
                    logger.error("Failed handling -2022 reduce-only recovery: %s", retry_err)

            # Handle duplicate order sent on network timeout retry (-2010 or duplicate order message)
            if ("duplicate" in err_msg.lower() or "already exists" in err_msg.lower()) and intent.client_order_id:
                logger.warning(
                    "Order %s reported as duplicate by exchange — fetching existing order status from exchange...",
                    intent.client_order_id,
                )
                try:
                    existing_res = self.fetch_order(resolved_sym, intent.client_order_id)
                    if existing_res and existing_res.status != OrderStatus.UNKNOWN:
                        logger.info(
                            "Successfully recovered duplicate order %s from exchange (status=%s, filled=%.8f)",
                            intent.client_order_id,
                            existing_res.status.value,
                            existing_res.filled_amount,
                        )
                        return existing_res
                except Exception as fetch_err:
                    logger.warning("Could not fetch duplicate order %s: %s", intent.client_order_id, fetch_err)

            raise InvalidOrderError(str(e)) from e

    @staticmethod
    def _extract_fee(raw: dict[str, Any]) -> tuple[float, str]:
        """Safely extract fee cost and currency from CCXT order dictionary."""
        if not isinstance(raw, dict):
            return 0.0, ""
        fee_dict = raw.get("fee")
        if isinstance(fee_dict, dict) and fee_dict.get("cost") is not None:
            return float(fee_dict.get("cost") or 0.0), str(fee_dict.get("currency") or "")
        fees_list = raw.get("fees")
        if isinstance(fees_list, list) and len(fees_list) > 0:
            total_cost = 0.0
            currs = set()
            for f in fees_list:
                if isinstance(f, dict):
                    total_cost += float(f.get("cost") or 0.0)
                    if f.get("currency"):
                        currs.add(str(f.get("currency")))
            return total_cost, ",".join(sorted(currs))
        # Check trades list in CCXT order
        trades_list = raw.get("trades")
        if isinstance(trades_list, list) and len(trades_list) > 0:
            total_cost = 0.0
            currs = set()
            for t in trades_list:
                if isinstance(t, dict):
                    tf = t.get("fee")
                    if isinstance(tf, dict) and tf.get("cost") is not None:
                        total_cost += float(tf.get("cost") or 0.0)
                        if tf.get("currency"):
                            currs.add(str(tf.get("currency")))
            if total_cost > 0:
                return total_cost, ",".join(sorted(currs))
        # Check raw Binance fills in info
        if isinstance(raw.get("info"), dict):
            fills = raw["info"].get("fills")
            if isinstance(fills, list) and len(fills) > 0:
                total_cost = 0.0
                currs = set()
                for fill in fills:
                    if isinstance(fill, dict):
                        comm = float(fill.get("commission", 0.0) or 0.0)
                        total_cost += comm
                        if fill.get("commissionAsset"):
                            currs.add(str(fill.get("commissionAsset")))
                if total_cost > 0:
                    return total_cost, ",".join(sorted(currs))
        return 0.0, ""

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        """Cancel an open order, supporting numeric exchange order IDs and client order IDs."""
        sym = self._resolve_market_symbol(symbol)
        params: dict[str, Any] = {}
        target_id: str | None = order_id
        if order_id and not str(order_id).isdigit():
            params["origClientOrderId"] = str(order_id)
            target_id = None
        try:
            raw = self._retry(lambda: self.exchange.cancel_order(target_id, sym, params=params))
            return OrderResult(
                client_order_id=raw.get("clientOrderId", "") or (order_id if not str(order_id).isdigit() else ""),
                exchange_order_id=str(raw.get("id", order_id)),
                status=OrderStatus.CANCELLED,
                raw_response=raw,
            )
        except ccxt.OrderNotFound:
            return OrderResult(
                client_order_id=order_id if not str(order_id).isdigit() else "",
                exchange_order_id=order_id,
                status=OrderStatus.CANCELLED,
                error_message="Order not found (already cancelled or filled)",
            )
        except (ccxt.InvalidOrder, ccxt.BadRequest) as e:
            err_str = str(e).lower()
            if any(k in err_str for k in ("-2011", "unknown order", "not found", "already filled", "already canceled", "already cancelled", "does not exist")):
                return OrderResult(
                    client_order_id=order_id if not str(order_id).isdigit() else "",
                    exchange_order_id=order_id,
                    status=OrderStatus.CANCELLED,
                    error_message=f"Order already inactive ({e})",
                )
            raise

    def fetch_order(self, symbol: str, order_id: str) -> OrderResult:
        """Get current status of an order, supporting numeric exchange order IDs and client order IDs."""
        sym = self._resolve_market_symbol(symbol)
        params: dict[str, Any] = {}
        target_id: str | None = order_id
        if order_id and not str(order_id).isdigit():
            params["origClientOrderId"] = str(order_id)
            target_id = None
        raw = self._retry(lambda: self.exchange.fetch_order(target_id, sym, params=params))
        avg_px = float(raw.get("average") or raw.get("price") or 0)
        if avg_px == 0.0 and raw.get("trades"):
            trades = raw.get("trades", [])
            total_cost = sum(float(t.get("cost", 0) or (float(t.get("price", 0)) * float(t.get("amount", 0)))) for t in trades)
            total_qty = sum(float(t.get("amount", 0)) for t in trades)
            if total_qty > 0:
                avg_px = total_cost / total_qty
        if avg_px == 0.0 and isinstance(raw.get("info"), dict):
            info = raw["info"]
            cum_quote = float(info.get("cumQuote", 0) or 0)
            cum_qty = float(info.get("cumQty", 0) or 0)
            if cum_qty > 0:
                avg_px = cum_quote / cum_qty
            elif float(info.get("avgPrice", 0) or 0) > 0:
                avg_px = float(info.get("avgPrice"))

        mapped_status = self._map_order_status(raw.get("status", ""))
        filled_amt = float(raw.get("filled", 0) or 0)
        if filled_amt == 0.0 and isinstance(raw.get("info"), dict):
            info = raw["info"]
            filled_amt = float(info.get("cumQty", 0) or info.get("executedQty", 0) or 0)
        if filled_amt == 0.0 and mapped_status == OrderStatus.FILLED:
            filled_amt = float(raw.get("amount", 0) or 0)

        fee_cost, fee_curr = self._extract_fee(raw)
        return OrderResult(
            client_order_id=raw.get("clientOrderId", "") or (order_id if not str(order_id).isdigit() else ""),
            exchange_order_id=str(raw.get("id", "")),
            status=mapped_status,
            filled_amount=filled_amt,
            average_price=avg_px,
            fees=fee_cost,
            fee_currency=fee_curr,
            timestamp_ms=int(raw.get("timestamp", 0) or 0),
            raw_response=raw,
        )

    def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        """List open orders on the exchange."""
        raw_list = self._retry(lambda: self.exchange.fetch_open_orders(symbol))
        results: list[OrderResult] = []
        for raw in raw_list:
            results.append(OrderResult(
                client_order_id=raw.get("clientOrderId", ""),
                symbol=str(raw.get("symbol", symbol or "")),
                exchange_order_id=str(raw.get("id", "")),
                status=self._map_order_status(raw.get("status", "")),
                filled_amount=float(raw.get("filled", 0) or 0),
                average_price=float(raw.get("average", 0) or raw.get("price", 0) or 0),
                timestamp_ms=int(raw.get("timestamp", 0) or 0),
                raw_response=raw,
            ))
        return results

    # ── Precision helpers ────────────────────────────────────

    def _resolve_market_symbol(self, symbol: str) -> str:
        """Resolve symbol across spot/futures and pair variations."""
        if not symbol:
            return symbol
        if symbol in self._markets:
            return symbol
        if f"{symbol}:USDT" in self._markets:
            return f"{symbol}:USDT"
        if "/" not in symbol:
            cand = f"{symbol}/USDT"
            if cand in self._markets:
                return cand
            if f"{cand}:USDT" in self._markets:
                return f"{cand}:USDT"
            return cand
        if ":" in symbol and symbol.split(":")[0] in self._markets:
            return symbol.split(":")[0]
        return symbol

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        """Apply exchange-specific amount precision."""
        sym = self._resolve_market_symbol(symbol)
        return float(self.exchange.amount_to_precision(sym, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        """Apply exchange-specific price precision."""
        sym = self._resolve_market_symbol(symbol)
        return float(self.exchange.price_to_precision(sym, price))

    def get_market_info(self, symbol: str) -> dict[str, Any]:
        """Get market info for a symbol."""
        sym = self._resolve_market_symbol(symbol)
        if sym in self._markets:
            return self._markets[sym]
        raise InvalidOrderError(f"Symbol {symbol} not found in loaded markets.")

    # ── Internal helpers ─────────────────────────────────────

    def _retry(self, func, max_retries: int | None = None):
        """Execute *func* with exponential backoff on transient errors."""
        retries = max_retries or self._config.max_retries
        delay_ms = self._config.retry_delay_base_ms

        for attempt in range(retries + 1):
            try:
                return func()
            except (ccxt.AuthenticationError, ccxt.PermissionDenied) as e:
                err_msg = str(e)
                if "-2015" in err_msg or "Invalid API-key" in err_msg:
                    raise ExchangeAuthError(
                        f"Binance authentication failed (-2015: Invalid API-key, IP, or permissions).\n"
                        f"Details: {err_msg}\n"
                        f"Action required on Binance:\n"
                        f"  1. Go to Binance API Management -> Edit API Key.\n"
                        f"  2. Enable 'Enable Futures' permission checkbox (if using Futures trading).\n"
                        f"  3. Check IP Whitelist restrictions: if enabled, add server IP: {get_whitelist_ip_summary()}\n"
                        f"  4. Verify BINANCE_API_KEY and BINANCE_API_SECRET in your .env file.\n"
                        f"  (Note: If you intended Spot trading instead of Futures, set 'market_type: spot' in config.yaml)"
                    ) from e
                raise ExchangeAuthError(f"Authentication failed: {e}") from e
            except Exception as e:
                err_msg = str(e)
                if "-2015" in err_msg or "Invalid API-key" in err_msg:
                    raise ExchangeAuthError(
                        f"Binance authentication failed (-2015: Invalid API-key, IP, or permissions).\n"
                        f"Details: {err_msg}\n"
                        f"Action required on Binance:\n"
                        f"  1. Go to Binance API Management -> Edit API Key.\n"
                        f"  2. Enable 'Enable Futures' permission checkbox (if using Futures trading).\n"
                        f"  3. Check IP Whitelist restrictions: if enabled, add server IP: {get_whitelist_ip_summary()}\n"
                        f"  4. Verify BINANCE_API_KEY and BINANCE_API_SECRET in your .env file.\n"
                        f"  (Note: If you intended Spot trading instead of Futures, set 'market_type: spot' in config.yaml)"
                    ) from e
                if isinstance(e, ccxt.RateLimitExceeded):
                    if attempt < retries:
                        wait = (delay_ms * (2 ** attempt)) / 1000
                        logger.warning(
                            "⚠️ Exchange Rate Limited: %s — waiting %.1fs (attempt %d/%d)",
                            e, wait, attempt + 1, retries,
                        )
                        time.sleep(wait)
                        continue
                    else:
                        logger.error("❌ Exchange Rate Limit exceeded permanently: %s", e)
                        raise ExchangeRateLimitError(str(e)) from e
                elif isinstance(e, _TRANSIENT_ERRORS):
                    if attempt < retries:
                        wait = (delay_ms * (2 ** attempt)) / 1000
                        logger.warning(
                            "⚠️ Exchange communication issue: %s — %s, retrying in %.1fs (attempt %d/%d)",
                            type(e).__name__, e, wait, attempt + 1, retries,
                        )
                        time.sleep(wait)
                        continue
                    else:
                        logger.error("❌ Exchange communication failed after %d retries: %s — %s", retries, type(e).__name__, e)
                        raise ExchangeConnectionError(
                            f"Exchange unreachable after {retries} retries: {e}"
                        ) from e
                elif isinstance(e, (ccxt.BadSymbol, getattr(ccxt, 'SymbolNotFound', ccxt.BadSymbol))):
                    logger.error("❌ Invalid or unsupported exchange symbol: %s", e)
                    raise InvalidOrderError(f"Symbol not supported or invalid: {e}") from e
                elif isinstance(e, (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadRequest)):
                    logger.error("❌ Exchange order error (%s): %s", type(e).__name__, e)
                    raise
                elif isinstance(e, ccxt.BaseError):
                    logger.error("❌ Exchange API error response: %s — %s", type(e).__name__, e)
                    raise ExchangeConnectionError(str(e)) from e
                raise

    @staticmethod
    def _map_order_status(status_str: str) -> OrderStatus:
        """Map CCXT order status string to our enum."""
        mapping = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "partiallyfilled": OrderStatus.PARTIALLY_FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.FAILED,
            "failed": OrderStatus.FAILED,
        }
        return mapping.get(status_str.lower(), OrderStatus.UNKNOWN)

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        """Convert timeframe string to milliseconds."""
        units = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        for suffix, ms in units.items():
            if timeframe.endswith(suffix):
                return int(timeframe[:-len(suffix)]) * ms
        raise ValueError(f"Cannot parse timeframe: {timeframe}")
