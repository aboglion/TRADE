"""
Tax & Trade History Service — Comprehensive historical trades & tax reporting.

Retrieves and merges trade execution history from:
1. Binance REST API (Futures USDT-M & Spot) via CCXT / direct pagination (bypassing the 1-year web UI limitation).
2. Bot state store (completed & executed orders recorded locally).

Supports:
- "מאז ומעולם" (All-Time / Inception): from account opening to present.
- "שנה אחרונה" (Past 1 Year / 365 Days): recent 12 months.
- Calendar tax years (2024, 2025, 2026) and custom date ranges.
- Comprehensive Israeli Tax Authority compliant reporting (capital gain/loss, total consideration, allowable fees, per-coin breakdown).
- Excel/CSV export with UTF-8 BOM encoding for seamless Hebrew display.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("bot.services.tax_history")


# Binance SAPI capital endpoints accept ~90-day windows per request; use 85 days for safety.
_TRANSFER_WINDOW_MS = 85 * 86400 * 1000
# Binance launched 2017-07-01; absolute lower bound for all-time deposit/withdrawal lookback.
_BINANCE_INCEPTION_MS = 1498867200000
# Safety cap: 45 chunks x 85 days ≈ 10.5 years of history per full scan.
_MAX_TRANSFER_CHUNKS = 45
# Binance P2P/C2C service launched ~2020; clamp startTimestamp so all-time scans never
# send pre-service timestamps that Binance rejects with -31002 "Illegal parameter".
_P2P_INCEPTION_MS = 1577836800000  # 2020-01-01T00:00:00Z
# Transfer sources whose failure must block cache-coverage advancement (data-loss risk).
_CRITICAL_TRANSFER_LABELS = ("deposit_history", "withdraw_history", "transfer_master_fetcher")


_cached_ip_val: str = "89.139.94.94"
_cached_ip_ts: float = 0.0
_ip_fetch_in_progress: bool = False


def _refresh_ip_background() -> None:
    global _cached_ip_val, _cached_ip_ts, _ip_fetch_in_progress
    try:
        import urllib.request
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "TradeBot/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            ip = resp.read().decode("utf-8").strip()
            if ip:
                _cached_ip_val = ip
                _cached_ip_ts = time.time()
    except Exception:
        pass
    finally:
        _ip_fetch_in_progress = False


def _get_outbound_ip() -> str:
    """Helper to detect server outbound IP for Binance whitelisting instructions (non-blocking)."""
    global _cached_ip_val, _cached_ip_ts, _ip_fetch_in_progress
    now = time.time()
    if not _ip_fetch_in_progress and (now - _cached_ip_ts > 3600.0):
        _ip_fetch_in_progress = True
        t = threading.Thread(target=_refresh_ip_background, daemon=True)
        t.start()
    return _cached_ip_val


def _sync_exchange_time(ex: Any) -> Optional[int]:
    """
    Explicitly sync the local clock offset with Binance server time (non-fatal).

    ccxt applies options['timeDifference'] to signed request timestamps when
    options['adjustForTimeDifference'] is enabled, but the offset is normally loaded
    lazily on the first signed request. Under concurrent fan-out (the transfer
    fetcher's thread pool) that lazy load can race, causing -1021 "Timestamp outside
    recvWindow" errors. Pre-syncing here eliminates the race and provides a resync
    hook for _sapi_with_retry retries.
    """
    try:
        loader = getattr(ex, "load_time_difference", None)
        if callable(loader):
            diff = loader()
            try:
                diff_int = int(diff or 0)
            except (TypeError, ValueError):
                diff_int = 0
            logger.info("Binance server time synced: local-server offset %d ms", diff_int)
            return diff_int
    except Exception as e:
        logger.debug("Binance time sync skipped: %s", e)
    return None


class TaxHistoryService:
    """
    Centralized service for historical trade extraction and tax calculation.
    Thread-safe with caching to prevent excessive exchange queries.
    """

    def __init__(self, state_store: Any = None, config: Any = None):
        self.state_store = state_store
        self.config = config
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._cache_ttl_seconds: float = 45.0  # Cache for 45s to protect API limits

    def clear_cache(self) -> None:
        """Clear cached tax history data."""
        with self._lock:
            self._cache.clear()
            self._cache_ts = 0.0

    def _get_manual_deposits_file(self) -> Path:
        """Resolve canonical path for manual deposits & cost basis storage."""
        candidates = [
            Path("/home/uns/TRADE/RUN/data/manual_deposits.json"),
            Path("/home/uns/TRADE/data/manual_deposits.json"),
            Path("RUN/data/manual_deposits.json"),
            Path("data/manual_deposits.json"),
        ]
        for p in candidates:
            if p.exists():
                return p
        default_p = Path("/home/uns/TRADE/RUN/data/manual_deposits.json")
        default_p.parent.mkdir(parents=True, exist_ok=True)
        return default_p

    def get_manual_deposits(self) -> List[Dict[str, Any]]:
        """Return all user-recorded manual deposits and opening balances."""
        p = self._get_manual_deposits_file()
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Failed to load manual deposits: %s", e)
            return []

    def add_manual_deposit(self, deposit_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Record a manual deposit / initial cost basis lot.
        Persists to disk, feeds FIFO cost basis queue, and clears cache.
        """
        with self._lock:
            items = self.get_manual_deposits()
            ts = int(deposit_data.get("timestamp_ms") or int(time.time() * 1000))
            coin = str(deposit_data.get("coin", "USDT")).upper().strip()
            amount = float(deposit_data.get("amount", 0.0))
            price = float(deposit_data.get("price", 0.0))
            total_usd = float(deposit_data.get("total_usd") or (amount * price))
            notes = str(deposit_data.get("notes", "") or "הפקדה / יתרת פתיחה ידנית")

            dep_id = f"manual_{int(time.time())}_{coin.lower()}"
            dt_utc = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
            dt_local = datetime.fromtimestamp(ts / 1000.0)

            record = {
                "id": dep_id,
                "timestamp_ms": ts,
                "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": f"{coin}/USDT" if coin != "USDT" else "USDT/USD",
                "coin": coin,
                "side": "DEPOSIT",
                "action_type": "DEPOSIT",
                "market": "MANUAL",
                "amount": amount,
                "price": price,
                "total_usd": round(total_usd, 2),
                "cost_basis_usd": round(total_usd, 2),
                "fee_usd": 0.0,
                "fee_currency": "USD",
                "realized_pnl_usd": 0.0,
                "realized_pnl_pct": 0.0,
                "client_order_id": dep_id,
                "exchange_order_id": dep_id,
                "trade_id": dep_id,
                "status": "CONFIRMED",
                "source": "MANUAL_ENTRY",
                "notes": notes,
            }
            items.append(record)

            target_file = self._get_manual_deposits_file()
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2, ensure_ascii=False)

            self.clear_cache()
            return record

    def delete_manual_deposit(self, deposit_id: str) -> bool:
        """Delete a manual deposit record by ID and invalidate cache."""
        with self._lock:
            items = self.get_manual_deposits()
            initial_len = len(items)
            filtered = [x for x in items if str(x.get("id")) != str(deposit_id)]
            if len(filtered) < initial_len:
                target_file = self._get_manual_deposits_file()
                with open(target_file, "w", encoding="utf-8") as f:
                    json.dump(filtered, f, indent=2, ensure_ascii=False)
                self.clear_cache()
                return True
            return False

    def _get_binance_credentials(self) -> Tuple[str, str]:
        """Load API credentials safely from environment or .env files."""
        try:
            from src.utils.env_manager import load_dotenv
            load_dotenv()
        except Exception:
            try:
                from RUN.src.utils.env_manager import load_dotenv
                load_dotenv()
            except Exception:
                pass

        key = os.environ.get("BINANCE_API_KEY", "").strip().strip("'\"").strip()
        secret = os.environ.get("BINANCE_API_SECRET", "").strip().strip("'\"").strip()

        if not key or not secret or key == "your_api_key_here":
            candidate_envs = [
                Path("/home/uns/TRADE/.env"),
                Path("/home/uns/TRADE/RUN/.env"),
                Path(".env"),
                Path("RUN/.env"),
            ]
            for p in candidate_envs:
                if p.exists():
                    try:
                        for line in p.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if line.startswith("BINANCE_API_KEY="):
                                key = line.split("=", 1)[1].strip().strip("'\"").strip()
                            elif line.startswith("BINANCE_API_SECRET="):
                                secret = line.split("=", 1)[1].strip().strip("'\"").strip()
                    except Exception:
                        pass
                if key and secret and key != "your_api_key_here":
                    break

        return key, secret

    def _get_tracked_symbols(self) -> List[str]:
        """Get all standard symbols configured or tracked by the bot."""
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        if self.config and hasattr(self.config, "strategy") and hasattr(self.config.strategy, "assets"):
            for asset_name, asset_conf in self.config.strategy.assets.items():
                pair = getattr(asset_conf, "pair", f"{asset_name}/USDT")
                if pair not in symbols:
                    symbols.append(pair)
        return symbols

    def fetch_all_history(
        self,
        timeframe: str = "all",
        symbol_filter: str = "ALL",
        side_filter: str = "ALL",
        custom_start_ts: Optional[int] = None,
        custom_end_ts: Optional[int] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch full historical trade records, merge with bot state, and compute tax aggregates.

        Args:
            timeframe: 'all' (מאז ומעולם), '1y' (שנה אחרונה), '2026', '2025', '2024', or 'custom'.
            symbol_filter: 'ALL' or specific symbol (e.g. 'BTC/USDT', 'ETH', etc.)
            side_filter: 'ALL', 'BUY', 'SELL', 'REALIZED_PNL', 'FEES'
            custom_start_ts: Optional start timestamp in ms.
            custom_end_ts: Optional end timestamp in ms.
            force_refresh: If True, bypass internal cache.
        """
        now_ms = int(time.time() * 1000)
        since_ms, until_ms = self._resolve_timeframe(timeframe, now_ms, custom_start_ts, custom_end_ts)

        if timeframe == "custom":
            cache_key = f"custom_{custom_start_ts}_{custom_end_ts}"
        else:
            cache_key = timeframe

        with self._lock:
            if not force_refresh and (time.time() - self._cache_ts < self._cache_ttl_seconds) and cache_key in self._cache:
                raw_trades, binance_status = self._cache[cache_key]
            else:
                raw_trades, binance_status = self._collect_trades(since_ms, until_ms)
                self._cache[cache_key] = (raw_trades, binance_status)
                self._cache_ts = time.time()

        # Apply client-side filters (symbol & side)
        filtered_trades = self._filter_trades(raw_trades, symbol_filter, side_filter)

        # Compute tax aggregates and breakdown
        summary = self.calculate_tax_summary(filtered_trades)

        return {
            "timeframe": timeframe,
            "since_ms": since_ms,
            "until_ms": until_ms,
            "binance_status": binance_status,
            "total_count": len(filtered_trades),
            "summary": summary,
            "trades": filtered_trades,
            "server_ip": _get_outbound_ip(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

    def _resolve_timeframe(
        self,
        timeframe: str,
        now_ms: int,
        custom_start_ts: Optional[int] = None,
        custom_end_ts: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Convert timeframe selector into (since_ms, until_ms)."""
        until_ms = custom_end_ts if custom_end_ts else now_ms

        if timeframe == "all":
            # מאז ומעולם — All time since account inception (epoch 0)
            since_ms = 0
        elif timeframe == "1y":
            # שנה אחרונה — Past 365 days
            since_ms = now_ms - (365 * 24 * 3600 * 1000)
        elif timeframe in ("2026", "2025", "2024", "2023"):
            year = int(timeframe)
            dt_start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            dt_end = datetime(year, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)
            since_ms = int(dt_start.timestamp() * 1000)
            until_ms = min(int(dt_end.timestamp() * 1000), now_ms)
        elif timeframe == "custom" and custom_start_ts is not None:
            since_ms = custom_start_ts
        else:
            # Default to all-time
            since_ms = 0

        return since_ms, until_ms

    def _collect_trades(self, since_ms: int, until_ms: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Collect and deduplicate trades from both Binance Exchange API and Local Bot State."""
        binance_trades: List[Dict[str, Any]] = []
        binance_status: Dict[str, Any] = {
            "connected": False,
            "error": None,
            "futures_trades_count": 0,
            "spot_trades_count": 0,
            "income_records_count": 0,
            "bot_orders_count": 0,
        }

        api_key, api_secret = self._get_binance_credentials()

        # 1. Fetch from Binance API if keys are provided
        if api_key and api_secret and api_key not in ("your_api_key_here", ""):
            try:
                import ccxt
                from concurrent.futures import ThreadPoolExecutor

                # Setup CCXT exchange instance with adjusted time window and strict 3.5s timeout
                exchange = ccxt.binance({
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": False,
                    "timeout": 3500,
                    "options": {
                        "defaultType": "future",
                        "adjustForTimeDifference": True,
                        "recvWindow": 30000,
                    },
                })
                # Explicitly sync the clock offset before concurrent fan-out to avoid
                # -1021 "Timestamp outside recvWindow" races (see _sync_exchange_time).
                _sync_exchange_time(exchange)
                symbols = self._get_tracked_symbols()

                # Execute all 4 Binance query tasks concurrently in parallel to avoid frontend hanging
                with ThreadPoolExecutor(max_workers=4) as pool:
                    fut_f = pool.submit(self._fetch_binance_futures_trades, exchange, symbols, since_ms, until_ms)
                    fut_i = pool.submit(self._fetch_binance_futures_income, exchange, since_ms, until_ms)
                    fut_s = pool.submit(self._fetch_binance_spot_trades, api_key, api_secret, symbols, since_ms, until_ms)
                    fut_d = pool.submit(self._fetch_binance_deposits_and_withdrawals, api_key, api_secret, since_ms, until_ms)

                    f_trades = fut_f.result()
                    income_records = fut_i.result()
                    spot_trades = fut_s.result()
                    dep_withdraw, transfer_meta = fut_d.result()

                binance_trades.extend(f_trades)
                binance_trades.extend(income_records)
                binance_trades.extend(spot_trades)
                binance_trades.extend(dep_withdraw)

                binance_status["futures_trades_count"] = len(f_trades)
                binance_status["income_records_count"] = len(income_records)
                binance_status["spot_trades_count"] = len(spot_trades)
                binance_status["deposits_count"] = sum(1 for x in dep_withdraw if x.get("side") == "DEPOSIT")
                binance_status["withdrawals_count"] = sum(1 for x in dep_withdraw if x.get("side") == "WITHDRAW")
                binance_status["transfer_fetch_errors"] = list(transfer_meta.get("errors", []))[:8]
                binance_status["transfer_lookback_since_ms"] = transfer_meta.get("lookback_since_ms", 0)
                binance_status["transfer_chunks_queried"] = transfer_meta.get("chunks_queried", 0)
                if transfer_meta.get("errors"):
                    logger.warning(
                        "Transfer/deposit history fetch reported %d issue(s): %s",
                        len(transfer_meta["errors"]),
                        "; ".join(str(e) for e in transfer_meta["errors"][:3]),
                    )

                binance_status["connected"] = True
                logger.info(
                    "Binance API trades fetched concurrently: %d futures, %d income, %d spot, %d deposits/withdrawals",
                    len(f_trades),
                    len(income_records),
                    len(spot_trades),
                    len(dep_withdraw),
                )

            except Exception as e:
                err_msg = str(e)
                binance_status["connected"] = False
                binance_status["error"] = err_msg
                is_auth_err = any(x in err_msg for x in ("-2015", "Invalid API-key", "permissions", "IP"))
                binance_status["is_auth_or_ip_error"] = is_auth_err
                logger.warning("Binance historical query encountered: %s", err_msg)
        else:
            binance_status["error"] = "No Binance API keys configured in .env"

        # 2. Fetch local bot-recorded orders (always included as ground truth of bot activity)
        bot_trades = self._fetch_bot_state_orders(since_ms, until_ms)

        # 3. Include user-recorded manual deposits & opening balances
        manual_deposits = self.get_manual_deposits()
        for m in manual_deposits:
            m_ts = int(m.get("timestamp_ms", 0))
            if m_ts >= since_ms and (until_ms <= 0 or m_ts <= until_ms):
                bot_trades.append(m)

        binance_status["bot_orders_count"] = len(bot_trades)
        binance_status["manual_deposits_count"] = len(manual_deposits)

        # 4. Merge & Deduplicate
        merged = self._merge_and_deduplicate(binance_trades, bot_trades)

        # 5. Apply FIFO Cost Basis & Realized Capital Gain/Loss Engine (Israeli Tax Authority Standards)
        merged = self._apply_fifo_cost_basis(merged)

        # Sort chronologically descending (newest first for UI inspection)
        merged.sort(key=lambda t: t.get("timestamp_ms", 0), reverse=True)

        return merged, binance_status

    def _fetch_binance_futures_trades(
        self,
        exchange: Any,
        symbols: List[str],
        since_ms: int,
        until_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch USDT-M Futures trades using fromId pagination to bypass 7-day limits.
        """
        results: List[Dict[str, Any]] = []

        for sym in symbols:
            clean_sym = sym.replace("/", "").upper()
            from_id = 0
            max_loops = 10  # Up to 10,000 trades per symbol to stay within time budget

            for _ in range(max_loops):
                try:
                    params: Dict[str, Any] = {"symbol": clean_sym, "limit": 1000}
                    if from_id > 0:
                        params["fromId"] = from_id

                    # Call Binance fapiPrivateGetUserTrades
                    trades = exchange.fapiPrivateGetUserTrades(params)
                    if not trades:
                        break

                    for t in trades:
                        trade_ts = int(t.get("time", 0))
                        if trade_ts < since_ms:
                            continue
                        if trade_ts > until_ms:
                            continue

                        price = float(t.get("price", 0.0))
                        qty = float(t.get("qty", 0.0))
                        realized_pnl = float(t.get("realizedPnl", 0.0))
                        commission = float(t.get("commission", 0.0))
                        is_buyer = bool(t.get("buyer", False))
                        side = "BUY" if is_buyer else "SELL"

                        dt_utc = datetime.fromtimestamp(trade_ts / 1000.0, tz=timezone.utc)
                        dt_local = datetime.fromtimestamp(trade_ts / 1000.0)

                        coin_base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "")

                        results.append({
                            "id": f"binance_f_{clean_sym}_{t.get('id')}",
                            "timestamp_ms": trade_ts,
                            "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                            "symbol": sym,
                            "coin": coin_base,
                            "side": side,
                            "action_type": side,
                            "market": "FUTURES_USDT_M",
                            "amount": qty,
                            "price": price,
                            "total_usd": round(price * qty, 2),
                            "fee_usd": round(commission, 4),
                            "fee_currency": t.get("commissionAsset", "USDT"),
                            "realized_pnl_usd": round(realized_pnl, 4),
                            "client_order_id": "",
                            "exchange_order_id": str(t.get("orderId", "")),
                            "trade_id": str(t.get("id", "")),
                            "status": "FILLED",
                            "source": "BINANCE_FUTURES",
                            "notes": f"Maker: {t.get('maker', False)}",
                        })

                    if len(trades) < 1000:
                        break
                    from_id = int(trades[-1]["id"]) + 1

                except Exception as ex:
                    logger.debug("Error fetching futures trades for %s: %s", clean_sym, ex)
                    if any(x in str(ex) for x in ("-2015", "Invalid API-key", "permissions", "IP")):
                        return results
                    break

        return results

    def _fetch_binance_futures_income(
        self,
        exchange: Any,
        since_ms: int,
        until_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Futures Income history (Realized PnL, Commission, Funding Fees).
        fapiPrivateGetIncome works across all symbols without mandatory symbol argument.
        """
        results: List[Dict[str, Any]] = []
        try:
            params: Dict[str, Any] = {"limit": 1000}
            if since_ms > 0:
                params["startTime"] = since_ms
            if until_ms > 0:
                params["endTime"] = until_ms

            incomes = exchange.fapiPrivateGetIncome(params)
            for inc in incomes:
                inc_ts = int(inc.get("time", 0))
                if inc_ts < since_ms or inc_ts > until_ms:
                    continue

                income_val = float(inc.get("income", 0.0))
                inc_type = inc.get("incomeType", "OTHER")
                raw_sym = inc.get("symbol", "")
                sym = f"{raw_sym[:3]}/{raw_sym[3:]}" if len(raw_sym) >= 6 else (raw_sym or "GLOBAL")
                coin_base = raw_sym.replace("USDT", "") if raw_sym else "USDT"

                dt_utc = datetime.fromtimestamp(inc_ts / 1000.0, tz=timezone.utc)
                dt_local = datetime.fromtimestamp(inc_ts / 1000.0)

                fee_usd = 0.0
                realized_pnl = 0.0
                action_type = inc_type

                if inc_type == "REALIZED_PNL":
                    realized_pnl = income_val
                    side = "PNL"
                elif inc_type == "COMMISSION":
                    fee_usd = abs(income_val)
                    side = "FEE"
                elif inc_type == "FUNDING_FEE":
                    realized_pnl = income_val
                    side = "FUNDING"
                else:
                    side = inc_type

                results.append({
                    "id": f"income_{inc.get('tranId', '')}",
                    "timestamp_ms": inc_ts,
                    "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": sym,
                    "coin": coin_base,
                    "side": side,
                    "action_type": action_type,
                    "market": "FUTURES_USDT_M",
                    "amount": 0.0,
                    "price": 0.0,
                    "total_usd": round(abs(income_val), 2),
                    "fee_usd": round(fee_usd, 4),
                    "fee_currency": inc.get("asset", "USDT"),
                    "realized_pnl_usd": round(realized_pnl, 4),
                    "client_order_id": "",
                    "exchange_order_id": str(inc.get("tradeId", "")),
                    "trade_id": str(inc.get("tranId", "")),
                    "status": "SETTLED",
                    "source": "BINANCE_INCOME",
                    "notes": f"Income Type: {inc_type}",
                })
        except Exception as ex:
            logger.debug("Error fetching futures income: %s", ex)

        return results

    def _fetch_binance_spot_trades(
        self,
        api_key: str,
        api_secret: str,
        symbols: List[str],
        since_ms: int,
        until_ms: int,
    ) -> List[Dict[str, Any]]:
        """Fetch Spot trades using fromId pagination."""
        results: List[Dict[str, Any]] = []
        try:
            import ccxt
            spot_ex = ccxt.binance({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": False,
                "timeout": 3500,
                "options": {"adjustForTimeDifference": True, "recvWindow": 30000},
            })
            _sync_exchange_time(spot_ex)

            for sym in symbols:
                clean_sym = sym.replace("/", "").upper()
                from_id = 0
                for _ in range(5):
                    try:
                        params: Dict[str, Any] = {"symbol": clean_sym, "limit": 1000}
                        if from_id > 0:
                            params["fromId"] = from_id
                        elif since_ms > 0:
                            params["startTime"] = since_ms

                        trades = spot_ex.privateGetMyTrades(params)
                        if not trades:
                            break

                        for t in trades:
                            trade_ts = int(t.get("time", 0))
                            if trade_ts < since_ms or trade_ts > until_ms:
                                continue

                            price = float(t.get("price", 0.0))
                            qty = float(t.get("qty", 0.0))
                            is_buyer = bool(t.get("isBuyer", False))
                            side = "BUY" if is_buyer else "SELL"
                            commission = float(t.get("commission", 0.0))

                            dt_utc = datetime.fromtimestamp(trade_ts / 1000.0, tz=timezone.utc)
                            dt_local = datetime.fromtimestamp(trade_ts / 1000.0)
                            coin_base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "")

                            results.append({
                                "id": f"spot_{clean_sym}_{t.get('id')}",
                                "timestamp_ms": trade_ts,
                                "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                                "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": sym,
                                "coin": coin_base,
                                "side": side,
                                "action_type": side,
                                "market": "SPOT",
                                "amount": qty,
                                "price": price,
                                "total_usd": round(price * qty, 2),
                                "fee_usd": round(commission, 4),
                                "fee_currency": t.get("commissionAsset", "USDT"),
                                "realized_pnl_usd": 0.0,
                                "client_order_id": "",
                                "exchange_order_id": str(t.get("orderId", "")),
                                "trade_id": str(t.get("id", "")),
                                "status": "FILLED",
                                "source": "BINANCE_SPOT",
                                "notes": f"Maker: {t.get('isMaker', False)}",
                            })

                        if len(trades) < 1000:
                            break
                        from_id = int(trades[-1]["id"]) + 1
                    except Exception as ex_sym:
                        logger.debug("Spot trade fetch error for %s: %s", clean_sym, ex_sym)
                        if any(x in str(ex_sym) for x in ("-2015", "Invalid API-key", "permissions", "IP")):
                            return results
                        break
        except Exception as ex:
            logger.debug("Error initializing spot trade fetcher: %s", ex)

        return results

    def _fetch_binance_deposits_and_withdrawals(
        self,
        api_key: str,
        api_secret: str,
        since_ms: int,
        until_ms: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Fetch Crypto Deposits, Crypto Withdrawals, Fiat Orders and P2P trades from Binance SAPI.

        - All-time support: when since_ms == 0 the lookback paginates back to Binance
          inception (2017-07) instead of the old ~340-day cap, so initial deposits appear.
        - Bypasses 90-day SAPI query limits by chunking time ranges into <= 85-day segments.
        - Persists fetched records locally (exchange_transfers.json) and refreshes
          incrementally, so only the first full-history scan is expensive.
        - Returns (records, meta); meta["errors"] surfaces fetch failures for UI/CSV
          transparency instead of silently showing zero deposits.
        - Logs all movements for Israeli Tax Authority compliance (סעיפים 88-91).
        """
        meta: Dict[str, Any] = {
            "errors": [],
            "lookback_since_ms": 0,
            "chunks_queried": 0,
        }
        results: List[Dict[str, Any]] = []
        if not api_key or not api_secret:
            return results, meta

        errors: List[str] = meta["errors"]
        now_ms = int(time.time() * 1000)
        target_until = until_ms if until_ms > 0 else now_ms

        if since_ms > 0:
            target_since = since_ms
            span_chunks = (target_until - target_since) // _TRANSFER_WINDOW_MS + 2
            max_chunks = int(min(_MAX_TRANSFER_CHUNKS, max(1, span_chunks)))
        else:
            # מאז ומעולם — paginate back to exchange inception (Binance launched 2017-07)
            target_since = _BINANCE_INCEPTION_MS
            max_chunks = _MAX_TRANSFER_CHUNKS

        meta["lookback_since_ms"] = target_since

        # Reuse deep history already fetched; only refresh the recent window incrementally
        cache = self._load_transfer_cache()
        cached_records: List[Dict[str, Any]] = [r for r in cache.get("records", []) if isinstance(r, dict)]
        covered_since = int(cache.get("covered_since_ms", 0) or 0)
        last_fetch = int(cache.get("last_fetch_ms", 0) or 0)
        deep_history_ready = covered_since > 0 and covered_since <= target_since and last_fetch > 0

        overlap_ms = 2 * 86400 * 1000
        fetch_from = max(target_since, min(last_fetch, now_ms) - overlap_ms) if deep_history_ready else target_since

        chunks = self._build_transfer_chunks(fetch_from, target_until, max_chunks)
        meta["chunks_queried"] = len(chunks)

        try:
            import ccxt
            from concurrent.futures import ThreadPoolExecutor, as_completed
            ex = ccxt.binance({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": False,
                "timeout": 8000,
                "options": {"adjustForTimeDifference": True, "recvWindow": 30000},
            })
            # Pre-sync the clock offset BEFORE the thread pool fans out, so no request
            # is signed with an unadjusted timestamp (fixes intermittent -1021).
            _sync_exchange_time(ex)
            resync = (lambda: _sync_exchange_time(ex)) if ex is not None else None

            seen_transfer_ids = set()

            def fetch_dep(cs: int, ce: int):
                return ("dep", self._sapi_with_retry(
                    ex.sapiGetCapitalDepositHisrec,
                    {"startTime": cs, "endTime": ce, "limit": 1000},
                    errors, "deposit_history", resync=resync,
                ))

            def fetch_wd(cs: int, ce: int):
                return ("wd", self._sapi_with_retry(
                    ex.sapiGetCapitalWithdrawHistory,
                    {"startTime": cs, "endTime": ce, "limit": 1000},
                    errors, "withdraw_history", resync=resync,
                ))

            # 1. Fetch crypto deposits & withdrawals via paced chunk execution with instant circuit-breaker
            auth_or_perm_failed = False
            rate_limit_failed = False

            for c_start, c_end in chunks:
                if auth_or_perm_failed or rate_limit_failed:
                    break

                for kind, fn, lbl in (
                    ("dep", ex.sapiGetCapitalDepositHisrec, "deposit_history"),
                    ("wd", ex.sapiGetCapitalWithdrawHistory, "withdraw_history"),
                ):
                    payload = self._sapi_with_retry(
                        fn,
                        {"startTime": c_start, "endTime": c_end, "limit": 1000},
                        errors,
                        lbl,
                        resync=resync,
                    )
                    time.sleep(0.06)

                    if any("-2015" in str(e) or "הוספת כתובת ה-IP" in str(e) for e in errors):
                        auth_or_perm_failed = True
                        break
                    if any("429" in str(e) or "-1003" in str(e) or "מגבלת קצב" in str(e) for e in errors):
                        rate_limit_failed = True
                        break

                    if not isinstance(payload, list):
                        continue

                    if kind == "dep":
                        for d in payload:
                            status_code = d.get("status")
                            if status_code not in (1, 6):
                                continue

                            insert_ts = int(d.get("insertTime", 0))
                            tx_id = str(d.get("txId", "") or insert_ts)
                            dep_id = f"dep_{tx_id}"
                            if dep_id in seen_transfer_ids:
                                continue
                            seen_transfer_ids.add(dep_id)

                            coin = str(d.get("coin", "USDT")).upper()
                            amount = float(d.get("amount", 0.0))
                            dt_utc = datetime.fromtimestamp(insert_ts / 1000.0, tz=timezone.utc)
                            dt_local = datetime.fromtimestamp(insert_ts / 1000.0)

                            results.append({
                                "id": dep_id,
                                "timestamp_ms": insert_ts,
                                "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                                "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": f"{coin}/USDT" if coin != "USDT" else "USDT/USD",
                                "coin": coin,
                                "side": "DEPOSIT",
                                "action_type": "DEPOSIT",
                                "market": "TRANSFER",
                                "amount": amount,
                                "price": 1.0 if coin in ("USDT", "USD", "USDC", "FDUSD") else 0.0,
                                "total_usd": round(amount, 2) if coin in ("USDT", "USD", "USDC", "FDUSD") else 0.0,
                                "cost_basis_usd": round(amount, 2) if coin in ("USDT", "USD", "USDC", "FDUSD") else 0.0,
                                "fee_usd": 0.0,
                                "fee_currency": coin,
                                "realized_pnl_usd": 0.0,
                                "realized_pnl_pct": 0.0,
                                "client_order_id": "",
                                "exchange_order_id": str(d.get("txId", "")),
                                "trade_id": str(d.get("id", "") or tx_id),
                                "status": "SUCCESS",
                                "source": "BINANCE_DEPOSIT",
                                "notes": f"הפקדת קריפטו מוצלחת לרשת {d.get('network', '')} | TxID: {str(tx_id)[:16]}...",
                            })

                    elif kind == "wd":
                        for w in payload:
                            status_code = w.get("status")
                            if status_code != 6:
                                continue

                            apply_time_str = str(w.get("applyTime", ""))
                            try:
                                dt_apply = datetime.strptime(apply_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                                w_ts = int(dt_apply.timestamp() * 1000)
                            except Exception:
                                w_ts = now_ms

                            tx_id = str(w.get("txId", "") or w_ts)
                            wd_id = f"wd_{tx_id}"
                            if wd_id in seen_transfer_ids:
                                continue
                            seen_transfer_ids.add(wd_id)

                            coin = str(w.get("coin", "USDT")).upper()
                            amount = float(w.get("amount", 0.0))
                            tx_fee = float(w.get("transactionFee", 0.0))
                            dt_utc = datetime.fromtimestamp(w_ts / 1000.0, tz=timezone.utc)
                            dt_local = datetime.fromtimestamp(w_ts / 1000.0)

                            results.append({
                                "id": wd_id,
                                "timestamp_ms": w_ts,
                                "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                                "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": f"{coin}/USDT" if coin != "USDT" else "USDT/USD",
                                "coin": coin,
                                "side": "WITHDRAW",
                                "action_type": "WITHDRAW",
                                "market": "TRANSFER",
                                "amount": amount,
                                "price": 1.0 if coin in ("USDT", "USD", "USDC", "FDUSD") else 0.0,
                                "total_usd": round(amount, 2) if coin in ("USDT", "USD", "USDC", "FDUSD") else 0.0,
                                "cost_basis_usd": 0.0,
                                "fee_usd": round(tx_fee, 4),
                                "fee_currency": coin,
                                "realized_pnl_usd": 0.0,
                                "realized_pnl_pct": 0.0,
                                "client_order_id": "",
                                "exchange_order_id": str(w.get("txId", "")),
                                "trade_id": str(w.get("id", "") or tx_id),
                                "status": "SUCCESS",
                                "source": "BINANCE_WITHDRAWAL",
                                "notes": f"משיכת קריפטו מוצלחת לרשת {w.get('network', '')} | עמלה: {tx_fee} {coin} | כתובת: {str(w.get('address', ''))[:12]}...",
                            })

            # 2. Query Fiat deposit & withdrawal orders sequentially
            fiat_fn = getattr(ex, "sapiGetFiatOrders", None)
            if fiat_fn is not None and not rate_limit_failed:
                fiat_chunks = chunks[:4]  # Limit to most recent ~1 year of fiat records
                fiat_throttled = False
                for c_start, c_end in fiat_chunks:
                    if fiat_throttled or rate_limit_failed:
                        break
                    for t in ("0", "1"):
                        resp = self._sapi_with_retry(
                            fiat_fn,
                            {"transactionType": t, "beginTime": c_start, "endTime": c_end, "limit": 500},
                            errors,
                            f"fiat_orders_{t}",
                            resync=resync,
                        )
                        time.sleep(0.08)
                        if any("429" in str(e) or "-1003" in str(e) or "מגבלת קצב" in str(e) for e in errors):
                            rate_limit_failed = True
                            break
                        if any("-2015" in str(e) or "הוספת כתובת ה-IP" in str(e) for e in errors):
                            fiat_throttled = True
                            break
                        if not isinstance(resp, dict):
                            continue
                        data = resp.get("data", [])
                        if isinstance(data, list):
                            for f_ord in data:
                                rec = self._parse_fiat_order(f_ord, f"fiat_{t}")
                                if rec is None or rec["id"] in seen_transfer_ids:
                                    continue
                                seen_transfer_ids.add(rec["id"])
                                results.append(rec)

            # 3. P2P / C2C order history: paginated full-range query
            if not rate_limit_failed:
                for rec in self._fetch_binance_p2p_orders(ex, errors, fetch_from, target_until):
                    if rec["id"] in seen_transfer_ids:
                        continue
                    seen_transfer_ids.add(rec["id"])
                    results.append(rec)

            # 4. Binance Convert trade flow: converts fiat/crypto to trading inventory
            if not rate_limit_failed:
                for rec in self._fetch_binance_convert_orders(ex, errors, fetch_from, target_until):
                    if rec["id"] in seen_transfer_ids:
                        continue
                    seen_transfer_ids.add(rec["id"])
                    results.append(rec)

        except Exception as ex_err:
            err_msg = str(ex_err)
            errors.append(f"transfer_master_fetcher: {err_msg[:180]}")
            logger.warning("Error in deposit/withdrawal master fetcher: %s", err_msg)

        # Merge with persisted cache (dedup by record id) and save for fast incremental refreshes
        merged_by_id: Dict[str, Dict[str, Any]] = {}
        for r in cached_records + results:
            rid = str(r.get("id") or f"{r.get('source', '')}_{r.get('timestamp_ms', 0)}_{r.get('trade_id', '')}")
            merged_by_id[rid] = r
        all_records = list(merged_by_id.values())

        if not self._has_critical_transfer_error(errors):
            # Advance coverage unless a critical source (deposits/withdrawals) failed.
            # Optional sources (fiat/p2p/convert) failing must not force a perpetual
            # full 45-chunk rescan on every report run.
            new_covered = min(covered_since, target_since) if covered_since > 0 else target_since
            self._save_transfer_cache(all_records, new_covered, now_ms)
        else:
            # Persist what we got but do not advance coverage markers, so gaps get retried
            self._save_transfer_cache(all_records, covered_since, last_fetch)

        # Filter to the requested window
        windowed = [
            r for r in all_records
            if int(r.get("timestamp_ms", 0) or 0) >= max(0, since_ms)
            and int(r.get("timestamp_ms", 0) or 0) <= target_until
        ]
        windowed.sort(key=lambda r: int(r.get("timestamp_ms", 0) or 0))
        return windowed, meta

    def _get_transfer_cache_file(self) -> Path:
        """Resolve canonical path for persisted exchange transfer/deposit history."""
        candidates = [
            Path("/home/uns/TRADE/RUN/data/exchange_transfers.json"),
            Path("/home/uns/TRADE/data/exchange_transfers.json"),
            Path("RUN/data/exchange_transfers.json"),
            Path("data/exchange_transfers.json"),
        ]
        for p in candidates:
            if p.exists():
                return p
        default_p = Path("/home/uns/TRADE/RUN/data/exchange_transfers.json")
        default_p.parent.mkdir(parents=True, exist_ok=True)
        return default_p

    def _load_transfer_cache(self) -> Dict[str, Any]:
        """Load persisted transfer history cache (records + coverage markers)."""
        p = self._get_transfer_cache_file()
        if not p.exists():
            return {"records": [], "covered_since_ms": 0, "last_fetch_ms": 0}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                return {
                    "records": data.get("records", []),
                    "covered_since_ms": int(data.get("covered_since_ms", 0) or 0),
                    "last_fetch_ms": int(data.get("last_fetch_ms", 0) or 0),
                }
        except Exception as e:
            logger.warning("Failed to load transfer cache: %s", e)
        return {"records": [], "covered_since_ms": 0, "last_fetch_ms": 0}

    def _save_transfer_cache(self, records: List[Dict[str, Any]], covered_since_ms: int, last_fetch_ms: int) -> None:
        """Persist transfer history so repeated report generation stays fast."""
        try:
            p = self._get_transfer_cache_file()
            payload = {
                "covered_since_ms": int(covered_since_ms),
                "last_fetch_ms": int(last_fetch_ms),
                "records": records,
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to save transfer cache: %s", e)

    @staticmethod
    def _build_transfer_chunks(target_since: int, target_until: int, max_chunks: int) -> List[Tuple[int, int]]:
        """
        Build <=85-day query chunks from newest to oldest covering [target_since, target_until].
        Binance SAPI capital endpoints only accept ~90-day windows per request.
        """
        chunks: List[Tuple[int, int]] = []
        curr_end = target_until
        while curr_end > target_since and len(chunks) < max_chunks:
            curr_start = max(target_since, curr_end - _TRANSFER_WINDOW_MS)
            chunks.append((curr_start, curr_end))
            curr_end = curr_start - 1
        return chunks

    @staticmethod
    def _has_critical_transfer_error(errors: List[str]) -> bool:
        """
        True if any transfer fetch error came from a critical source (deposits/withdrawals).

        Optional sources (fiat/p2p/convert) may legitimately be unavailable for an
        account (region/permissions); their failure must not block transfer-cache
        coverage advancement, otherwise every report run repeats a full 45-chunk scan.
        """
        return any(
            str(e).split(":", 1)[0].strip() in _CRITICAL_TRANSFER_LABELS
            for e in errors
        )

    @staticmethod
    def _sapi_with_retry(
        fn: Any,
        params: Dict[str, Any],
        errors: List[str],
        label: str,
        max_retries: int = 3,
        resync: Any = None,
    ) -> Any:
        """
        Call a Binance SAPI endpoint with exponential backoff on rate limits (429/418)
        and clock-drift errors (-1021, retried after re-syncing server time via `resync`).
        Non-retryable failures are recorded once per label into `errors` for transparency.
        """
        if fn is None:
            return None

        delay = 1.0
        last_err = ""
        for attempt in range(max_retries):
            try:
                return fn(params)
            except Exception as e:
                last_err = str(e)
                is_rate_limit = any(
                    x in last_err for x in ("429", "418", "Too many requests", "rate limit", "WAY_TOO_MANY_REQUESTS")
                )
                is_clock_drift = "-1021" in last_err
                if "-1003" in last_err or "-2015" in last_err:
                    # Binance IP/UID rate limit or auth/permission error: abort immediately to prevent penalty escalation
                    break
                if (is_rate_limit or is_clock_drift) and attempt < max_retries - 1:
                    if is_clock_drift and callable(resync):
                        try:
                            resync()
                        except Exception:
                            pass
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                break

        if last_err:
            clean_err = last_err
            if "-1003" in last_err or "Too many requests" in last_err or "429" in last_err:
                clean_err = "Binance 429: מגבלת קצב SAPI — הקריאות הוגבלו כדי להגן על חשבונך"
            elif "-2015" in last_err:
                clean_err = "Binance -2015: נדרשת הרשאת קריאה/ארנק או הוספת כתובת ה-IP בבינאנס"
            elif "-31002" in last_err:
                clean_err = "Binance -31002: פרמטר לא חוקי — ה-endpoint הוסר (C2C) או חסרה הרשאת P2P"
            elif "-1021" in last_err:
                clean_err = "Binance -1021: שעון מקומי לא מסונכרן עם שרת בינאנס (recvWindow)"
            msg = f"{label}: {clean_err[:150]}"
            if not any(e.startswith(f"{label}:") for e in errors):
                errors.append(msg)
            logger.warning("SAPI %s failed: %s", label, last_err[:180])
        return None

    @staticmethod
    def _parse_fiat_order(f_ord: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
        """
        Parse one /sapi/v1/fiat/orders entry into a unified DEPOSIT/WITHDRAW record.
        Handles Binance quirks: success status is 'Trade Success' and the fiat amount
        field is documented as 'ammout' (API typo) on some response versions.
        """
        raw_status = str(f_ord.get("status", "")).strip().upper()
        if raw_status not in ("SUCCESSFUL", "SUCCESS", "COMPLETED", "TRADE SUCCESS", "TRADE_SUCCESS", "FINISHED"):
            return None

        act_side = "DEPOSIT" if kind == "fiat_0" else "WITHDRAW"
        try:
            f_ts = int(f_ord.get("createTime") or f_ord.get("updateTime") or 0)
        except (TypeError, ValueError):
            f_ts = 0
        if f_ts <= 0:
            return None

        ord_no = str(f_ord.get("orderNo", "") or f_ts)
        f_id = f"fiat_{act_side.lower()}_{ord_no}"

        fiat_curr = str(f_ord.get("fiatCurrency", "USD") or "USD").upper()
        try:
            fiat_amt = float(f_ord.get("amount") or f_ord.get("ammout") or 0.0)
            crypto_qty = float(f_ord.get("money") or 0.0)
            crypto_price = float(f_ord.get("price") or 0.0)
            fee_amt = float(f_ord.get("commission") or f_ord.get("totalFee") or 0.0)
        except (TypeError, ValueError):
            return None

        dt_utc = datetime.fromtimestamp(f_ts / 1000.0, tz=timezone.utc)
        dt_local = datetime.fromtimestamp(f_ts / 1000.0)
        act_title = "הפקדת פיאט" if act_side == "DEPOSIT" else "משיכת פיאט"
        method = f_ord.get("method") or f_ord.get("source") or "Bank/Card"

        notes = f"{act_title} מוצלחת: {fiat_amt} {fiat_curr} באמצעות {method}"
        if crypto_qty > 0:
            notes += f" | כמות קריפטו בעסקה: {crypto_qty} (מחיר: {crypto_price})"

        return {
            "id": f_id,
            "timestamp_ms": f_ts,
            "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": f"{fiat_curr}/USDT",
            "coin": fiat_curr,
            "side": act_side,
            "action_type": f"FIAT_{act_side}",
            "market": "FIAT",
            "amount": fiat_amt,
            "price": 1.0,
            "total_usd": round(fiat_amt, 2),
            "cost_basis_usd": round(fiat_amt, 2) if act_side == "DEPOSIT" else 0.0,
            "fee_usd": round(fee_amt, 4),
            "fee_currency": fiat_curr,
            "realized_pnl_usd": 0.0,
            "realized_pnl_pct": 0.0,
            "client_order_id": "",
            "exchange_order_id": ord_no,
            "trade_id": ord_no,
            "status": "SUCCESS",
            "source": "BINANCE_FIAT",
            "notes": notes,
        }

    @staticmethod
    def _parse_p2p_order(o: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse one P2P / C2C order entry into a unified BUY/SELL record.
        Supports both /sapi/v1/c2c/orderMatch/listUserOrderHistory and /sapi/v1/p2p/orderHistory/user.
        Completed P2P buys create FIFO cost-basis lots; completed sells are disposals.
        """
        status = str(o.get("orderStatus", "")).strip().upper()
        if status not in ("COMPLETED", "FINISHED", "SUCCESS"):
            return None

        trade_type = str(o.get("tradeType", "")).strip().upper()
        if trade_type not in ("BUY", "SELL"):
            return None

        asset = str(o.get("asset", "")).upper().strip()
        if not asset:
            return None

        try:
            crypto_qty = float(o.get("amount") or 0.0)
            fiat_amt = float(o.get("fiatAmount") or o.get("totalPrice") or 0.0)
            f_ts = int(o.get("orderFinishTime") or o.get("orderCreateTime") or o.get("createTime") or 0)
        except (TypeError, ValueError):
            return None

        if crypto_qty <= 0 or fiat_amt <= 0 or f_ts <= 0:
            return None

        fiat_curr = str(o.get("fiatCurrency") or o.get("fiat") or "USD").upper()
        ord_no = str(o.get("orderNumber", "") or o.get("orderId", "") or f_ts)
        rec_id = f"p2p_{trade_type.lower()}_{ord_no}"
        unit_price = float(o.get("unitPrice") or (fiat_amt / crypto_qty if crypto_qty > 0 else 0.0))

        dt_utc = datetime.fromtimestamp(f_ts / 1000.0, tz=timezone.utc)
        dt_local = datetime.fromtimestamp(f_ts / 1000.0)
        title = "רכישת קריפטו ב-P2P" if trade_type == "BUY" else "מכירת קריפטו ב-P2P"

        return {
            "id": rec_id,
            "timestamp_ms": f_ts,
            "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": f"{asset}/USDT",
            "coin": asset,
            "side": trade_type,
            "action_type": trade_type,
            "market": "P2P",
            "amount": crypto_qty,
            "price": round(unit_price, 4),
            "total_usd": round(fiat_amt, 2),
            "cost_basis_usd": round(fiat_amt, 2) if trade_type == "BUY" else 0.0,
            "fee_usd": 0.0,
            "fee_currency": fiat_curr,
            "realized_pnl_usd": 0.0,
            "realized_pnl_pct": 0.0,
            "client_order_id": "",
            "exchange_order_id": ord_no,
            "trade_id": ord_no,
            "status": "FILLED",
            "source": "BINANCE_P2P",
            "notes": f"{title}: {crypto_qty} {asset} תמורת {fiat_amt} {fiat_curr} | אמצעי תשלום: {o.get('payType', 'N/A')}",
        }

    def _fetch_binance_p2p_orders(
        self,
        ex: Any,
        errors: List[str],
        since_ms: int,
        until_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch completed P2P / C2C buy/sell orders via Binance SAPI.

        Prefers the current /sapi/v1/p2p/orderHistory/user endpoint. The legacy
        /sapi/v1/c2c/orderMatch/listUserOrderHistory endpoint was decommissioned by
        Binance and now answers -31002 "Illegal parameter", so it is only used as a
        last-resort fallback. Candidates are probed on the first BUY page and the
        first one returning a valid dict is used for pagination; an error is recorded
        only if every candidate fails.
        """
        results: List[Dict[str, Any]] = []
        resync = (lambda: _sync_exchange_time(ex)) if ex is not None else None

        candidates: List[Any] = []
        seen_fns = set()
        for cand in (
            getattr(ex, "sapiGetP2pOrderHistoryUser", None),
            getattr(ex, "sapi_get_p2p_order_history_user", None),
            getattr(ex, "sapiGetC2cOrderMatchListUserOrderHistory", None),
            getattr(ex, "sapi_get_c2c_ordermatch_listuserorderhistory", None),
        ):
            if cand is not None and cand not in seen_fns:
                seen_fns.add(cand)
                candidates.append(cand)
        try:
            candidates.append(lambda p: ex.request("p2p/orderHistory/user", "sapi", "GET", p))
        except Exception:
            pass
        if not candidates:
            return results

        # Clamp the lookback to the P2P service era (2020-01-01) so all-time scans
        # never send pre-service timestamps that Binance rejects with -31002.
        p2p_since = max(since_ms, _P2P_INCEPTION_MS) if since_ms > 0 else _P2P_INCEPTION_MS

        # Probe candidates on the first BUY page; pick the first that answers.
        probe_params: Dict[str, Any] = {"tradeType": "BUY", "page": 1, "rows": 100}
        if p2p_since > 0:
            probe_params["startTimestamp"] = p2p_since
        if until_ms > 0:
            probe_params["endTimestamp"] = until_ms

        fn = None
        probe_errs: List[str] = []
        for cand in candidates:
            try:
                resp = cand(dict(probe_params))
                if isinstance(resp, dict):
                    fn = cand
                    break
            except Exception as pe:
                probe_errs.append(str(pe))
                continue

        if fn is None:
            all_err_text = " ".join(probe_errs)
            if any(x in all_err_text for x in ("-2015", "Invalid API-key", "permissions", "IP")):
                errors.append("p2p_orders: Binance -2015: נדרשת הרשאת P2P או אישור IP בבינאנס")
            elif any(x in all_err_text for x in ("429", "-1003", "Too many requests")):
                errors.append("p2p_orders: Binance 429: מגבלת קצב SAPI")
            else:
                errors.append("p2p_orders: כל ה-endpoints של P2P/C2C לא זמינים (ה-endpoint הוסר או חסרה הרשאת P2P)")
            logger.warning("P2P order history unavailable: all candidate endpoints failed (%s)", (probe_errs[0] if probe_errs else "")[:120])
            return results

        for trade_type in ("BUY", "SELL"):
            page = 1
            for _ in range(5):  # up to 500 orders per side
                params: Dict[str, Any] = {"tradeType": trade_type, "page": page, "rows": 100}
                if p2p_since > 0:
                    params["startTimestamp"] = p2p_since
                if until_ms > 0:
                    params["endTimestamp"] = until_ms

                resp = self._sapi_with_retry(fn, params, errors, f"p2p_{trade_type.lower()}", resync=resync)
                time.sleep(0.1)
                if not isinstance(resp, dict):
                    break
                data = resp.get("data")
                if not isinstance(data, list) or not data:
                    break

                for o in data:
                    if not isinstance(o, dict):
                        continue
                    rec = self._parse_p2p_order(o)
                    if rec is None:
                        continue
                    ts = int(rec["timestamp_ms"])
                    if since_ms > 0 and ts < since_ms:
                        continue
                    if until_ms > 0 and ts > until_ms:
                        continue
                    results.append(rec)

                if len(data) < 100:
                    break
                page += 1

        return results

    @staticmethod
    def _parse_convert_order(o: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse Binance Convert trade into FIFO inventory acquisition lot."""
        status = str(o.get("orderStatus", "")).strip().upper()
        if status not in ("SUCCESS", "COMPLETED", "FINISHED"):
            return None

        from_asset = str(o.get("fromAsset", "")).upper().strip()
        to_asset = str(o.get("toAsset", "")).upper().strip()
        if not from_asset or not to_asset:
            return None

        try:
            from_amt = float(o.get("fromAmount") or 0.0)
            to_amt = float(o.get("toAmount") or 0.0)
            c_ts = int(o.get("createTime") or 0)
        except (TypeError, ValueError):
            return None

        if from_amt <= 0 or to_amt <= 0 or c_ts <= 0:
            return None

        ord_id = str(o.get("quoteId", "") or o.get("orderId", "") or c_ts)
        rec_id = f"convert_{ord_id}"
        dt_utc = datetime.fromtimestamp(c_ts / 1000.0, tz=timezone.utc)
        dt_local = datetime.fromtimestamp(c_ts / 1000.0)

        unit_price = (from_amt / to_amt) if to_amt > 0 else 0.0
        is_stable_or_fiat = from_asset in ("USDT", "USD", "USDC", "FDUSD", "BUSD", "EUR", "ILS")

        return {
            "id": rec_id,
            "timestamp_ms": c_ts,
            "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": f"{to_asset}/{from_asset}" if from_asset in ("USDT", "USD", "USDC") else f"{to_asset}/USDT",
            "coin": to_asset,
            "side": "BUY",
            "action_type": "CONVERT_BUY",
            "market": "CONVERT",
            "amount": to_amt,
            "price": round(unit_price, 4),
            "total_usd": round(from_amt, 2) if is_stable_or_fiat else 0.0,
            "cost_basis_usd": round(from_amt, 2) if is_stable_or_fiat else 0.0,
            "fee_usd": 0.0,
            "fee_currency": from_asset,
            "realized_pnl_usd": 0.0,
            "realized_pnl_pct": 0.0,
            "client_order_id": "",
            "exchange_order_id": ord_id,
            "trade_id": ord_id,
            "status": "FILLED",
            "source": "BINANCE_CONVERT",
            "notes": f"המרה (Convert): רכישת {to_amt} {to_asset} תמורת {from_amt} {from_asset}",
        }

    def _fetch_binance_convert_orders(
        self,
        ex: Any,
        errors: List[str],
        since_ms: int,
        until_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch completed Binance Convert orders via /sapi/v1/convert/tradeFlow.
        Ensures crypto converted from fiat or stablecoins establishes proper FIFO cost basis.
        """
        results: List[Dict[str, Any]] = []
        resync = (lambda: _sync_exchange_time(ex)) if ex is not None else None
        fn = (
            getattr(ex, "sapiGetConvertTradeFlow", None)
            or getattr(ex, "sapi_get_convert_tradeflow", None)
        )
        if fn is None:
            try:
                fn = lambda p: ex.request("convert/tradeFlow", "sapi", "GET", p)
            except Exception:
                fn = None

        if fn is None:
            return results

        now_ms = int(time.time() * 1000)
        target_until = until_ms if until_ms > 0 else now_ms
        c_window = 28 * 86400 * 1000
        start_t = max(since_ms, target_until - (180 * 86400 * 1000)) if since_ms <= 0 else since_ms

        curr_start = start_t
        for _ in range(6):  # up to 6 months
            if curr_start >= target_until:
                break
            curr_end = min(target_until, curr_start + c_window)
            params = {"startTime": curr_start, "endTime": curr_end, "limit": 100}
            resp = self._sapi_with_retry(fn, params, errors, "convert_history", resync=resync)
            time.sleep(0.08)
            if not isinstance(resp, dict):
                if any(x in str(e) for e in errors for x in ("-2015", "429", "-1003", "מגבלת קצב", "כתובת ה-IP")):
                    break
                curr_start = curr_end + 1
                continue
            data = resp.get("list") or resp.get("data") or []
            if isinstance(data, list):
                for o in data:
                    rec = self._parse_convert_order(o)
                    if rec is not None:
                        results.append(rec)
            curr_start = curr_end + 1

        return results

    def _fetch_bot_state_orders(self, since_ms: int, until_ms: int) -> List[Dict[str, Any]]:
        """
        Load local bot state orders from memory or bot_state.json files.
        """
        results: List[Dict[str, Any]] = []
        raw_orders: List[Dict[str, Any]] = []

        # 1. From active state store if available
        if self.state_store:
            try:
                st = self.state_store.load_state()
                if st and hasattr(st, "completed_orders"):
                    raw_orders.extend(st.completed_orders)
            except Exception as e:
                logger.debug("Could not load from state_store: %s", e)

        # 2. Check canonical state file locations
        candidate_files = [
            Path("/home/uns/TRADE/RUN/data/bot_state.json"),
            Path("/home/uns/TRADE/data/bot_state.json"),
            Path("RUN/data/bot_state.json"),
            Path("data/bot_state.json"),
        ]

        for p in candidate_files:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    completed = data.get("completed_orders", [])
                    raw_orders.extend(completed)
                except Exception as e:
                    logger.debug("Could not load state from %s: %s", p, e)

        # Deduplicate raw orders by client_order_id
        seen_order_ids = set()
        for o in raw_orders:
            cid = o.get("client_order_id") or str(o.get("timestamp", 0))
            if cid in seen_order_ids:
                continue
            seen_order_ids.add(cid)

            ts = int(o.get("timestamp") or o.get("timestamp_ms") or 0)
            if ts < since_ms or ts > until_ms:
                continue

            price = float(o.get("average_price") or o.get("price") or o.get("estimated_price") or 0.0)
            amount = float(o.get("filled_amount") or o.get("amount") or 0.0)
            fees = float(o.get("fees") or 0.0)
            fee_curr = o.get("fee_currency", "USDT")
            side = str(o.get("side", "BUY")).upper()
            sym = o.get("symbol", "UNKNOWN")
            coin = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "")
            status = str(o.get("status", "FILLED")).upper()
            total_usd = round(price * amount, 2)

            dt_utc = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc) if ts > 0 else datetime.now(timezone.utc)
            dt_local = datetime.fromtimestamp(ts / 1000.0) if ts > 0 else datetime.now()

            results.append({
                "id": cid,
                "timestamp_ms": ts,
                "datetime_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "datetime_local": dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": sym,
                "coin": coin,
                "side": side,
                "action_type": side,
                "market": "BOT_EXECUTION",
                "amount": amount,
                "price": price,
                "total_usd": total_usd,
                "fee_usd": round(fees, 4),
                "fee_currency": fee_curr,
                "realized_pnl_usd": 0.0,  # Will be mapped or computed
                "client_order_id": cid,
                "exchange_order_id": str(o.get("exchange_order_id") or ""),
                "trade_id": str(o.get("exchange_order_id") or cid),
                "status": status,
                "source": "BOT_STATE",
                "notes": o.get("reason", "Automated Strategy Rebalance"),
            })

        return results

    def _merge_and_deduplicate(
        self,
        binance_trades: List[Dict[str, Any]],
        bot_trades: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge exchange trades and bot trades while eliminating duplicates.
        """
        merged: List[Dict[str, Any]] = []
        seen_keys = set()

        # Add all Binance trades first (highest execution fidelity)
        for t in binance_trades:
            k = (t.get("symbol"), t.get("timestamp_ms"), round(t.get("amount", 0), 4), t.get("side"))
            seen_keys.add(k)
            if t.get("exchange_order_id"):
                seen_keys.add(t["exchange_order_id"])
            if t.get("trade_id"):
                seen_keys.add(t["trade_id"])
            merged.append(t)

        # Add bot trades that are not already present from Binance
        for b in bot_trades:
            ex_id = b.get("exchange_order_id")
            if ex_id and ex_id in seen_keys:
                continue

            k = (b.get("symbol"), b.get("timestamp_ms"), round(b.get("amount", 0), 4), b.get("side"))
            if k in seen_keys:
                continue

            seen_keys.add(k)
            if ex_id:
                seen_keys.add(ex_id)
            merged.append(b)

        return merged

    def _apply_fifo_cost_basis(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Compute Realized Capital Gain / Loss (רווח או הפסד הון ממומש)
        for every Spot disposal/sell using FIFO (First-In, First-Out) matching,
        conforming to Israeli Tax Authority standards (סעיף 88-91 לפקודת מס הכנסה).
        """
        # Sort chronologically ascending to match lots in FIFO order
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp_ms", 0))

        # inventory per coin: list of lot dicts
        # lot = {"qty": float, "price": float, "fee_unit": float, "ts": int}
        inventory: Dict[str, List[Dict[str, Any]]] = {}

        for t in sorted_trades:
            status = str(t.get("status", "SUCCESS")).upper()
            is_successful = t.get("is_successful", True)
            if not is_successful or status in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED"):
                t["cost_basis_usd"] = 0.0
                t["realized_pnl_usd"] = 0.0
                t["realized_pnl_pct"] = 0.0
                t["pnl_note"] = "פעולה נכשלה/בוטלה (לא נכללת בחישוב מס)"
                continue

            side = str(t.get("side", "")).upper()
            coin = str(t.get("coin", "")).upper()
            market = str(t.get("market", "")).upper()
            qty = float(t.get("amount", 0.0))
            price = float(t.get("price", 0.0))
            fee = float(t.get("fee_usd", 0.0))

            if coin not in inventory:
                inventory[coin] = []

            # Ensure baseline fields exist
            if "cost_basis_usd" not in t:
                t["cost_basis_usd"] = 0.0
            if "realized_pnl_usd" not in t:
                t["realized_pnl_usd"] = 0.0
            if "realized_pnl_pct" not in t:
                t["realized_pnl_pct"] = 0.0

            # 1. BUYS or INCOMING DEPOSITS -> Add to inventory
            if side in ("BUY", "DEPOSIT") and qty > 0:
                fee_unit = (fee / qty) if qty > 0 else 0.0
                effective_price = price if price > 0 else (t.get("total_usd", 0.0) / qty if qty > 0 else 0.0)
                inventory[coin].append({
                    "qty": qty,
                    "price": effective_price,
                    "fee_unit": fee_unit,
                    "ts": t.get("timestamp_ms", 0)
                })
                t["cost_basis_usd"] = round(qty * effective_price, 2)
                t["realized_pnl_usd"] = 0.0
                t["realized_pnl_pct"] = 0.0
                t["pnl_note"] = "רכישה (עלות בסיס נצברה למלאי)" if side == "BUY" else "הפקדה נכנסה למלאי"

            # 2. SELLS on SPOT -> Match against inventory using FIFO
            elif side == "SELL" and market in ("SPOT", "BOT_EXECUTION", "TRANSFER", "P2P", "FIAT", "CONVERT") and qty > 0:
                proceeds = round(qty * price, 2) if price > 0 else float(t.get("total_usd", 0.0))
                t["total_usd"] = proceeds

                matched_cost = 0.0
                matched_buy_fee = 0.0
                needed_qty = qty

                coin_lots = inventory[coin]
                while needed_qty > 1e-8 and coin_lots:
                    first_lot = coin_lots[0]
                    avail_qty = first_lot["qty"]
                    take_qty = min(needed_qty, avail_qty)

                    matched_cost += take_qty * first_lot["price"]
                    matched_buy_fee += take_qty * first_lot["fee_unit"]
                    first_lot["qty"] -= take_qty
                    needed_qty -= take_qty

                    if first_lot["qty"] <= 1e-8:
                        coin_lots.pop(0)

                # Net Capital Gain = Proceeds - Cost Basis - Sell Fee - Matched Buy Fee
                net_pnl = proceeds - matched_cost - fee - matched_buy_fee
                pnl_pct = (net_pnl / matched_cost * 100.0) if matched_cost > 0 else 0.0

                t["cost_basis_usd"] = round(matched_cost, 2)
                t["realized_pnl_usd"] = round(net_pnl, 4)
                t["realized_pnl_pct"] = round(pnl_pct, 2)
                if needed_qty > 1e-8 and matched_cost == 0.0:
                    t["pnl_note"] = f"תמורה: ${proceeds:.2f} | לא אותרה עלות רכישה (0.00$) - הזן הפקדה ידנית לעדכון עלות"
                elif needed_qty > 1e-8:
                    t["pnl_note"] = f"תמורה: ${proceeds:.2f} | עלות חולצה חלקית מ-FIFO: ${matched_cost:.2f} (חסר {needed_qty:.4f} {coin})"
                else:
                    t["pnl_note"] = f"תמורה: ${proceeds:.2f} | עלות רכישה FIFO: ${matched_cost:.2f}"

            # 3. FUTURES with existing realized PnL
            elif t.get("realized_pnl_usd", 0.0) != 0.0:
                pnl_val = float(t["realized_pnl_usd"])
                tot_val = float(t.get("total_usd", 0.0))
                pct = (pnl_val / tot_val * 100.0) if tot_val > 0 else 0.0
                t["realized_pnl_pct"] = round(pct, 2)
                t["pnl_note"] = "רווח/הפסד ממומש בפיוצ'רס"

        # Re-sort descending (newest first for UI table display)
        sorted_trades.sort(key=lambda t: t.get("timestamp_ms", 0), reverse=True)
        return sorted_trades

    def _filter_trades(
        self,
        trades: List[Dict[str, Any]],
        symbol_filter: str,
        side_filter: str,
    ) -> List[Dict[str, Any]]:
        """Apply symbol and side/action filtering."""
        filtered = []
        sym_clean = symbol_filter.upper().strip()
        side_clean = side_filter.upper().strip()

        for t in trades:
            # 1. Symbol Filter
            if sym_clean != "ALL":
                t_sym = t.get("symbol", "").upper()
                t_coin = t.get("coin", "").upper()
                if sym_clean not in (t_sym, t_coin, t_sym.replace("/", "")):
                    continue

            # 2. Side / Action Filter
            if side_clean != "ALL":
                t_side = t.get("side", "").upper()
                t_act = t.get("action_type", "").upper()
                if side_clean == "BUY" and t_side != "BUY":
                    continue
                elif side_clean == "SELL" and t_side != "SELL":
                    continue
                elif side_clean == "DEPOSIT" and t_side != "DEPOSIT" and t_act != "DEPOSIT":
                    continue
                elif side_clean == "WITHDRAW" and t_side != "WITHDRAW" and t_act != "WITHDRAW":
                    continue
                elif side_clean == "REALIZED_PNL" and "PNL" not in (t_side, t_act) and t.get("realized_pnl_usd", 0.0) == 0.0:
                    continue
                elif side_clean == "FEES" and not ("FEE" in t_side or "FUNDING" in t_side or "COMMISSION" in t_act):
                    continue

            filtered.append(t)

        return filtered

    def calculate_tax_summary(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate comprehensive Israeli Tax Authority metrics for capital gains, turnover & operations.
        """
        total_operations = len(trades)
        total_volume_usd = 0.0
        total_buy_volume_usd = 0.0
        total_sell_volume_usd = 0.0
        total_cost_basis_usd = 0.0
        total_fees_usd = 0.0
        total_realized_pnl_usd = 0.0
        total_funding_fees_usd = 0.0
        total_buy_orders = 0
        total_sell_orders = 0
        total_deposits_count = 0
        total_deposits_usd = 0.0
        total_withdrawals_count = 0
        total_withdrawals_usd = 0.0

        per_coin: Dict[str, Dict[str, Any]] = {}

        for t in trades:
            status = str(t.get("status", "SUCCESS")).upper()
            is_successful = t.get("is_successful", True)
            if not is_successful or status in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED"):
                continue

            coin = t.get("coin") or "OTHER"
            if coin not in per_coin:
                per_coin[coin] = {
                    "coin": coin,
                    "operations": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "buy_volume_usd": 0.0,
                    "sell_volume_usd": 0.0,
                    "cost_basis_usd": 0.0,
                    "total_volume_usd": 0.0,
                    "fees_usd": 0.0,
                    "realized_pnl_usd": 0.0,
                    "deposits_usd": 0.0,
                    "withdrawals_usd": 0.0,
                }

            c_data = per_coin[coin]
            c_data["operations"] += 1

            side = t.get("side", "").upper()
            vol = float(t.get("total_usd", 0.0))
            fee = float(t.get("fee_usd", 0.0))
            pnl = float(t.get("realized_pnl_usd", 0.0))
            cost = float(t.get("cost_basis_usd", 0.0))

            total_volume_usd += vol
            total_fees_usd += fee
            total_realized_pnl_usd += pnl
            c_data["total_volume_usd"] += vol
            c_data["fees_usd"] += fee
            c_data["realized_pnl_usd"] += pnl

            if side == "BUY":
                total_buy_volume_usd += vol
                total_buy_orders += 1
                c_data["buy_volume_usd"] += vol
                c_data["buy_count"] += 1
            elif side == "SELL":
                total_sell_volume_usd += vol
                total_sell_orders += 1
                total_cost_basis_usd += cost
                c_data["sell_volume_usd"] += vol
                c_data["cost_basis_usd"] += cost
                c_data["sell_count"] += 1
            elif side == "DEPOSIT":
                total_deposits_count += 1
                total_deposits_usd += vol
                c_data["deposits_usd"] += vol
            elif side == "WITHDRAW":
                total_withdrawals_count += 1
                total_withdrawals_usd += vol
                c_data["withdrawals_usd"] += vol
            elif "FUNDING" in side:
                total_funding_fees_usd += pnl

        # Israeli capital gains tax estimation: 25% on net capital gain, or tax loss carryforward
        estimated_tax_usd = max(0.0, round(total_realized_pnl_usd * 0.25, 2))
        tax_loss_carryforward_usd = abs(min(0.0, round(total_realized_pnl_usd, 2)))

        # Round per-coin aggregates
        for k, v in per_coin.items():
            v["buy_volume_usd"] = round(v["buy_volume_usd"], 2)
            v["sell_volume_usd"] = round(v["sell_volume_usd"], 2)
            v["cost_basis_usd"] = round(v["cost_basis_usd"], 2)
            v["total_volume_usd"] = round(v["total_volume_usd"], 2)
            v["fees_usd"] = round(v["fees_usd"], 4)
            v["realized_pnl_usd"] = round(v["realized_pnl_usd"], 4)
            v["deposits_usd"] = round(v["deposits_usd"], 2)
            v["withdrawals_usd"] = round(v["withdrawals_usd"], 2)

        return {
            "total_operations": total_operations,
            "total_buy_orders": total_buy_orders,
            "total_sell_orders": total_sell_orders,
            "total_volume_usd": round(total_volume_usd, 2),
            "total_buy_volume_usd": round(total_buy_volume_usd, 2),
            "total_sell_volume_usd": round(total_sell_volume_usd, 2),
            "total_cost_basis_usd": round(total_cost_basis_usd, 2),
            "total_fees_usd": round(total_fees_usd, 4),
            "total_funding_fees_usd": round(total_funding_fees_usd, 4),
            "total_realized_pnl_usd": round(total_realized_pnl_usd, 4),
            "estimated_tax_usd": estimated_tax_usd,
            "tax_loss_carryforward_usd": tax_loss_carryforward_usd,
            "total_deposits_count": total_deposits_count,
            "total_deposits_usd": round(total_deposits_usd, 2),
            "total_withdrawals_count": total_withdrawals_count,
            "total_withdrawals_usd": round(total_withdrawals_usd, 2),
            "per_coin_summary": per_coin,
        }

    def generate_tax_csv(
        self,
        timeframe: str,
        trades: List[Dict[str, Any]],
        summary: Dict[str, Any],
        binance_status: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate Israeli Tax Authority compliant CSV with UTF-8 BOM encoding for Excel.
        """
        output = io.StringIO()

        # Excel UTF-8 BOM is added by the HTTP handler when serving bytes
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        # Header Comments / Summary Block
        writer.writerow(["# =========================================================================================="])
        writer.writerow(["# דוח פעולות מסחר ורווחי הון להגשה למס הכנסה — Trading & Capital Gains Tax Report"])
        writer.writerow([f"# תאריך הפקת הדוח (Generated At): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"])
        writer.writerow([f"# טווח תאריכים נבחר (Timeframe): {timeframe}"])
        writer.writerow([f"# סה\"כ פעולות (Total Transactions): {summary.get('total_operations', 0)}"])
        writer.writerow([f"# מחזור מכירות / תמורה כוללת (Total Consideration USD): ${summary.get('total_sell_volume_usd', 0.0):,.2f}"])
        writer.writerow([f"# עלות רכישה מקורית ממומשת (Total Cost Basis USD): ${summary.get('total_cost_basis_usd', 0.0):,.2f}"])
        writer.writerow([f"# רווח/הפסד הון ממומש נטו (Net Realized Capital Gain/Loss USD): ${summary.get('total_realized_pnl_usd', 0.0):,.2f}"])
        writer.writerow([f"# אומדן מס רווחי הון 25% (Estimated 25% Tax USD): ${summary.get('estimated_tax_usd', 0.0):,.2f}"])
        writer.writerow([f"# סה\"כ עמלות מסחר מותרות בניכוי (Allowable Trading Fees USD): ${summary.get('total_fees_usd', 0.0):,.2f}"])
        writer.writerow([f"# סה\"כ הפקדות (Total Deposits USD): ${summary.get('total_deposits_usd', 0.0):,.2f} ({summary.get('total_deposits_count', 0)} הפקדות)"])
        writer.writerow([f"# סה\"כ משיכות (Total Withdrawals USD): ${summary.get('total_withdrawals_usd', 0.0):,.2f} ({summary.get('total_withdrawals_count', 0)} משיכות)"])

        # Transparency notes: explain zero-deposit reports, fetch errors and missing cost basis
        transfer_errors = (binance_status or {}).get("transfer_fetch_errors") or []
        zero_basis_sells = [
            t for t in trades
            if str(t.get("side", "")).upper() == "SELL"
            and float(t.get("total_usd", 0.0) or 0.0) > 0
            and float(t.get("cost_basis_usd", 0.0) or 0.0) <= 0
            and str(t.get("status", "SUCCESS")).upper() not in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED")
        ]
        if summary.get("total_deposits_count", 0) == 0:
            writer.writerow(["# ⚠ לא אותרו הפקדות בטווח הנבחר. ייתכן שההפקדה בוצעה דרך P2P/Convert, שלמפתח ה-API חסרה הרשאת Wallet, או שטרם הוזנה יתרת פתיחה ידנית במסך היסטוריית המסחר."])
        if transfer_errors:
            writer.writerow([f"# ⚠ שגיאות שליפת היסטוריית העברות/הפקדות: {'; '.join(str(e)[:120] for e in transfer_errors[:4])}"])
        if zero_basis_sells:
            writer.writerow([f"# ⚠ אותרו {len(zero_basis_sells)} מכירות ללא עלות רכישה מקורית (יתרת פתיחה חסרה) — הרווח הממומש בדוח זה עשוי להיות מוגזם. הזן הפקדה/יתרת פתיחה ידנית לתיקון."])

        writer.writerow(["# =========================================================================================="])
        writer.writerow([])

        # Table Column Headers (Bilingual Hebrew & English for Accountants)
        headers = [
            "#",
            "תאריך ושעה UTC (Date UTC)",
            "תאריך ושעה מקומי (Local Date)",
            "נכס / מטבע (Asset / Coin)",
            "צמד מסחר (Symbol)",
            "סוג פעולה (Action / Side)",
            "שוק (Market)",
            "כמות (Quantity)",
            "מחיר ביצוע ב-$ (Exec Price USD)",
            "תמורה / שווי כולל ב-$ (Total Value USD)",
            "עלות רכישה מקורית ב-$ (Cost Basis USD)",
            "עמלות מסחר ב-$ (Fee USD)",
            "רווח / הפסד הון ממומש ב-$ (Realized Capital Gain/Loss USD)",
            "תשואה % (Return %)",
            "מזהה פקודה / עסקה / TxID (Order / Trade / TxID)",
            "מקור הנתון (Data Source)",
            "סטטוס (Status)",
            "הערות / פירוט (Notes)",
        ]
        writer.writerow(headers)

        # Write each trade row
        for idx, t in enumerate(trades, 1):
            pnl_pct_str = f"{t.get('realized_pnl_pct', 0.0):+.2f}%" if t.get("realized_pnl_pct") else "-"
            writer.writerow([
                idx,
                t.get("datetime_utc", ""),
                t.get("datetime_local", ""),
                t.get("coin", ""),
                t.get("symbol", ""),
                t.get("side", ""),
                t.get("market", ""),
                f"{t.get('amount', 0.0):.8f}",
                f"{t.get('price', 0.0):.4f}",
                f"{t.get('total_usd', 0.0):.2f}",
                f"{t.get('cost_basis_usd', 0.0):.2f}",
                f"{t.get('fee_usd', 0.0):.4f}",
                f"{t.get('realized_pnl_usd', 0.0):+.4f}" if t.get("realized_pnl_usd") else "0.0000",
                pnl_pct_str,
                t.get("trade_id") or t.get("exchange_order_id") or t.get("id", ""),
                t.get("source", ""),
                t.get("status", ""),
                t.get("notes", "") or t.get("pnl_note", ""),
            ])

        return output.getvalue()


# Module-level singleton instance for server integration
_tax_history_service: Optional[TaxHistoryService] = None


def get_tax_history_service(state_store: Any = None, config: Any = None) -> TaxHistoryService:
    global _tax_history_service
    if _tax_history_service is None:
        _tax_history_service = TaxHistoryService(state_store=state_store, config=config)
    else:
        if state_store and not _tax_history_service.state_store:
            _tax_history_service.state_store = state_store
        if config and not _tax_history_service.config:
            _tax_history_service.config = config
    return _tax_history_service
