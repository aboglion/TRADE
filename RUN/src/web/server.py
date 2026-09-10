"""
Live Monitoring Dashboard Server.

Provides a lightweight, zero-dependency REST API and static asset server for
monitoring the live trading bot status, portfolio holdings, regime state,
and logs in real-time.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import Lock, Thread
from typing import Any, Dict, Optional

logger = logging.getLogger("bot.web.server")

STATIC_DIR = (Path(__file__).parent / "static").resolve()

# Global market metrics cache for BTC, ETH, SOL multi-timeframe performance
_market_metrics_cache: Dict[str, Any] = {"timestamp": 0.0, "data": {}}
_market_metrics_lock = Lock()
_market_metrics_updating = False

_cached_ccxt_exchange: Optional[Any] = None
_cached_ccxt_lock = Lock()


def _get_shared_exchange(gateway: Optional[Any] = None) -> Optional[Any]:
    """Reuse existing exchange client or module-level cached ccxt client."""
    if gateway:
        if hasattr(gateway, "exchange"):
            try:
                ex = gateway.exchange
                if ex is not None:
                    return ex
            except Exception:
                pass
        if hasattr(gateway, "_exchange") and gateway._exchange is not None:
            return gateway._exchange

    global _cached_ccxt_exchange
    with _cached_ccxt_lock:
        if _cached_ccxt_exchange is None:
            try:
                import ccxt
                _cached_ccxt_exchange = ccxt.binance({"timeout": 10000, "enableRateLimit": False})
            except Exception as e:
                logger.debug("Failed initializing shared ccxt client: %s", e)
                return None
        return _cached_ccxt_exchange


def _fetch_market_metrics_worker() -> None:
    global _market_metrics_cache, _market_metrics_updating
    try:
        import numpy as np
        exchange = _get_shared_exchange()
        if exchange is None:
            return
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        
        tickers = {}
        try:
            tickers = exchange.fetch_tickers(symbols)
        except Exception as te:
            logger.debug("Failed batch fetch_tickers: %s", te)

        results = {}
        for sym in symbols:
            coin = sym.split("/")[0]
            try:
                ticker = tickers.get(sym, {})
                pct_24h = float(ticker.get("percentage", 0.0) or 0.0)

                c4h = exchange.fetch_ohlcv(sym, timeframe="4h", limit=3)
                curr_price = float(c4h[-1][4]) if c4h else float(ticker.get("last", 0.0) or 0.0)
                prev_4h = float(c4h[-2][4]) if len(c4h) >= 2 else (float(c4h[-1][1]) if c4h else curr_price)
                pct_4h = ((curr_price - prev_4h) / prev_4h * 100.0) if prev_4h > 0 else 0.0

                c1d = exchange.fetch_ohlcv(sym, timeframe="1d", limit=160)
                closes = [float(c[4]) for c in c1d]
                if len(closes) >= 150:
                    sma150 = float(np.mean(closes[-150:]))
                    pct_sma150 = ((curr_price - sma150) / sma150 * 100.0) if sma150 > 0 else 0.0
                else:
                    pct_sma150 = 0.0

                results[coin] = {
                    "price": curr_price,
                    "change_4h": round(pct_4h, 2),
                    "change_24h": round(pct_24h, 2),
                    "change_sma150": round(pct_sma150, 2),
                }
            except Exception as ex:
                logger.debug("Failed to fetch market metrics for %s: %s", sym, ex)

        if results:
            with _market_metrics_lock:
                _market_metrics_cache = {"timestamp": time.time(), "data": results}
    except Exception as e:
        logger.debug("Error updating market metrics cache: %s", e)
    finally:
        _market_metrics_updating = False


def get_market_metrics_cached() -> Dict[str, Any]:
    global _market_metrics_updating
    now = time.time()
    with _market_metrics_lock:
        data = _market_metrics_cache.get("data", {})
        ts = _market_metrics_cache.get("timestamp", 0.0)

    if (now - ts > 30.0) and not _market_metrics_updating:
        _market_metrics_updating = True
        t = Thread(target=_fetch_market_metrics_worker, daemon=True)
        t.start()

    return data

# Warm-up initial fetch on module load
try:
    _market_metrics_updating = True
    Thread(target=_fetch_market_metrics_worker, daemon=True).start()
except Exception:
    _market_metrics_updating = False


class LoginRateLimiter:
    """In-memory rate limiter and brute-force lockout manager."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 300, delay_seconds: float = 1.0):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.delay_seconds = delay_seconds
        self.attempts: Dict[str, list[float]] = {}
        self.lockouts: Dict[str, float] = {}
        self._lock = Lock()

    def get_client_ip(self, handler: Any) -> str:
        try:
            forwarded = handler.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
            real_ip = handler.headers.get("X-Real-IP")
            if real_ip:
                return real_ip.strip()
            if handler.client_address and len(handler.client_address) > 0:
                return str(handler.client_address[0])
        except Exception:
            pass
        return "127.0.0.1"

    def is_locked_out(self, ip: str) -> tuple[bool, int]:
        """Returns (is_locked, remaining_seconds)."""
        with self._lock:
            now = time.time()
            if ip in self.lockouts:
                lockout_until = self.lockouts[ip]
                if now < lockout_until:
                    return True, max(1, int(lockout_until - now))
                else:
                    del self.lockouts[ip]
                    self.attempts[ip] = []
            return False, 0

    def record_failed_attempt(self, ip: str) -> tuple[bool, int]:
        """Record failed attempt and return (is_now_locked, remaining_seconds)."""
        with self._lock:
            now = time.time()
            timestamps = [t for t in self.attempts.get(ip, []) if now - t < 600]
            timestamps.append(now)
            self.attempts[ip] = timestamps

            if len(timestamps) >= self.max_attempts:
                lockout_until = now + self.lockout_seconds
                self.lockouts[ip] = lockout_until
                logger.warning(
                    "🚨 BRUTE-FORCE PROTECTION: IP %s locked out for %d seconds (%d failed login attempts)",
                    ip,
                    self.lockout_seconds,
                    len(timestamps),
                )
                return True, self.lockout_seconds

            return False, 0

    def record_successful_login(self, ip: str) -> None:
        with self._lock:
            self.attempts.pop(ip, None)
            self.lockouts.pop(ip, None)


# Global rate-limiter instance for the server
rate_limiter = LoginRateLimiter(max_attempts=5, lockout_seconds=300, delay_seconds=1.0)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass
        super().server_bind()


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    """Custom request handler serving REST API endpoints and static dashboard files."""

    # Reference to gateway, state_store, config set by runner
    gateway: Any = None
    state_store: Any = None
    config: Any = None
    log_file_path: Optional[str] = None
    orchestrator: Any = None
    telegram_service: Any = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        clean_path = path.split("?")[0].split("#")[0]
        if clean_path in ("/", "", "/index", "/index.html"):
            clean_path = "/index.html"
        
        rel_path = clean_path.lstrip("/")
        target_file = (STATIC_DIR / rel_path).resolve()
        
        if not str(target_file).startswith(str(STATIC_DIR)):
            target_file = STATIC_DIR / "index.html"
            
        return str(target_file)

    def _is_authenticated(self) -> bool:
        expected_pass = os.environ.get("DASHBOARD_PASSWORD", "").strip()
        if not expected_pass:
            return True
        client_pass = self.headers.get("X-Dashboard-Password", "").strip()
        if client_pass == expected_pass:
            return True
        cookie_header = self.headers.get("Cookie", "")
        if f"dash_auth={expected_pass}" in cookie_header:
            return True
        if f"token={expected_pass}" in self.path or f"password={expected_pass}" in self.path:
            return True
        return False

    def _require_auth(self) -> bool:
        ip = rate_limiter.get_client_ip(self)
        is_locked, remaining = rate_limiter.is_locked_out(ip)
        if is_locked:
            self._send_json(
                {
                    "error": f"Too many failed login attempts. IP {ip} temporarily blocked for {remaining}s.",
                    "auth_required": True,
                    "locked_out": True,
                    "retry_after_seconds": remaining,
                },
                status=429,
            )
            return False

        if self._is_authenticated():
            return True
        self._send_json({"error": "Unauthorized. Password required.", "auth_required": True}, status=401)
        return False

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/api/auth_check":
            self._handle_auth_check()
            return

        if clean_path.startswith("/api/"):
            if not self._require_auth():
                return

        clean_api_path = clean_path.rstrip("/")
        if clean_api_path == "/api/status":
            self._handle_status()
        elif clean_api_path == "/api/portfolio":
            self._handle_portfolio()
        elif clean_api_path == "/api/orders":
            self._handle_orders()
        elif clean_api_path == "/api/logs":
            self._handle_logs()
        elif clean_api_path == "/api/logs/download":
            self._handle_download_logs()
        elif clean_api_path == "/api/dry_run/balances":
            self._handle_get_dry_run_balances()
        elif clean_api_path == "/api/updater":
            self._handle_get_updater_status()
        elif clean_api_path == "/api/telegram":
            self._handle_get_telegram()
        elif clean_api_path == "/api/strategy/conditions":
            self._handle_strategy_conditions()
        elif clean_path.startswith("/api/"):
            self._send_json({"error": f"API endpoint '{clean_path}' not found"}, status=404)
        else:
            # Fallback to serving static files (index.html, style.css, app.js)
            if clean_path in ("/", "", "/index", "/index.html"):
                self.path = "/index.html"
            else:
                self.path = clean_path
            try:
                super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_error(self, format, *args):
        if args and any("Broken pipe" in str(a) or "Connection reset" in str(a) for a in args):
            return
        super().log_error(format, *args)

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/api/login":
            self._handle_login()
            return

        if clean_path.startswith("/api/"):
            if not self._require_auth():
                return

        if clean_path == "/api/trigger":
            self._handle_trigger_cycle()
        elif clean_path == "/api/killswitch":
            self._handle_toggle_killswitch()
        elif clean_path == "/api/dry_run/balances":
            self._handle_update_dry_run_balances()
        elif clean_path == "/api/errors/clear":
            self._handle_clear_errors()
        elif clean_path in ("/api/logs/clear", "/api/clear_logs"):
            self._handle_clear_logs()
        elif clean_path in ("/api/orders/clear", "/api/clear_orders"):
            self._handle_clear_orders()
        elif clean_path == "/api/reset_stats":
            self._handle_reset_stats()
        elif clean_path == "/api/updater/toggle":
            self._handle_toggle_updater()
        elif clean_path == "/api/updater/pull":
            self._handle_manual_pull()
        elif clean_path == "/api/telegram":
            self._handle_update_telegram()
        elif clean_path == "/api/telegram/test":
            self._handle_test_telegram()
        elif clean_path == "/api/mode":
            self._handle_switch_mode()
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    def _handle_switch_mode(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            requested_mode = str(data.get("mode", "")).upper()

            if requested_mode not in ("DRY_RUN", "LIVE", "TESTNET"):
                self._send_json({"error": f"Invalid mode '{requested_mode}'. Allowed: DRY_RUN, LIVE, TESTNET"}, status=400)
                return

            if requested_mode == "LIVE":
                # Ensure .env is read if keys aren't in os.environ yet
                for env_candidate in (".env", "RUN/.env", str(Path(__file__).resolve().parent.parent.parent / ".env")):
                    if Path(env_candidate).exists():
                        try:
                            with open(env_candidate, "r", encoding="utf-8") as ef:
                                for eline in ef:
                                    eline = eline.strip()
                                    if eline and not eline.startswith("#") and "=" in eline:
                                        ek, _, ev = eline.partition("=")
                                        ek, ev = ek.strip(), ev.strip().strip("'\"")
                                        if ek and (ek not in os.environ or not os.environ[ek]):
                                            os.environ[ek] = ev
                        except Exception:
                            pass

                api_key = os.environ.get("BINANCE_API_KEY", "").strip()
                api_secret = os.environ.get("BINANCE_API_SECRET", "").strip()
                if not api_key or not api_secret or api_key == "your_api_key_here":
                    self._send_json({
                        "error": "Cannot switch to LIVE mode: BINANCE_API_KEY and BINANCE_API_SECRET are missing in environment or .env file!",
                        "help": "Set BINANCE_API_KEY and BINANCE_API_SECRET in .env file before activating LIVE mode."
                    }, status=400)
                    return
                os.environ["CONFIRM_LIVE"] = "YES_I_UNDERSTAND"

            from src.config.config_manager import ConfigManager
            cfg_path = "config.yaml" if Path("config.yaml").exists() else "RUN/config.yaml"
            cm = ConfigManager(str(cfg_path))
            cm.save_run_mode(requested_mode)

            self._send_json({
                "success": True,
                "mode": requested_mode,
                "message": f"Successfully updated run mode to {requested_mode}. Rebooting bot...",
            })

            import threading
            import subprocess
            def _reboot():
                time.sleep(1.0)
                try:
                    proj_dir = Path(__file__).resolve().parent.parent.parent.parent
                    subprocess.run(["make", "restart"], cwd=str(proj_dir), timeout=30)
                except Exception as ex:
                    logger.error("Error during mode restart: %s", ex)

            threading.Thread(target=_reboot, daemon=True).start()

        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_auth_check(self) -> None:
        expected_pass = os.environ.get("DASHBOARD_PASSWORD", "").strip()
        ip = rate_limiter.get_client_ip(self)
        is_locked, remaining = rate_limiter.is_locked_out(ip)
        self._send_json({
            "auth_required": bool(expected_pass),
            "authenticated": self._is_authenticated(),
            "locked_out": is_locked,
            "retry_after_seconds": remaining,
        })

    def _handle_login(self) -> None:
        expected_pass = os.environ.get("DASHBOARD_PASSWORD", "").strip()
        if not expected_pass:
            self._send_json({"success": True, "auth_required": False, "message": "No password configured"})
            return

        ip = rate_limiter.get_client_ip(self)
        is_locked, remaining = rate_limiter.is_locked_out(ip)
        if is_locked:
            logger.warning("Rejected login attempt from locked-out IP %s (%d seconds remaining)", ip, remaining)
            self._send_json(
                {
                    "success": False,
                    "error": f"Account temporarily locked due to too many failed login attempts! Try again in {remaining} seconds.",
                    "locked_out": True,
                    "retry_after_seconds": remaining,
                },
                status=429,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            provided_pass = str(data.get("password", "")).strip()

            if provided_pass == expected_pass:
                rate_limiter.record_successful_login(ip)
                content = json.dumps({"success": True, "token": expected_pass, "message": "Authenticated successfully"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"dash_auth={expected_pass}; Path=/; SameSite=Strict")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
            else:
                # Artificial delay to throttle automated bots
                time.sleep(rate_limiter.delay_seconds)
                is_locked_now, rem_sec = rate_limiter.record_failed_attempt(ip)
                if is_locked_now:
                    self._send_json(
                        {
                            "success": False,
                            "error": f"Account locked! {rate_limiter.max_attempts} failed login attempts recorded. Access locked for {rem_sec} seconds.",
                            "locked_out": True,
                            "retry_after_seconds": rem_sec,
                        },
                        status=429,
                    )
                else:
                    self._send_json({"success": False, "error": "Invalid password"}, status=401)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, status=400)

    # ── REST API Handlers ─────────────────────────────────────

    def _handle_status(self) -> None:
        state = self.state_store.load_state() if self.state_store else None
        critical_errors = state.critical_errors if state else []
        metrics = get_market_metrics_cached()
        last_regime = state.last_regime if (state and state.last_regime) else None
        if not last_regime or last_regime == "UNKNOWN":
            btc_m = metrics.get("BTC", {})
            if "change_sma150" in btc_m:
                last_regime = "bull" if btc_m["change_sma150"] >= 0 else "bear"
            else:
                last_regime = "bull"

        data = {
            "run_mode": self.config.run_mode.name if self.config else "UNKNOWN",
            "last_regime": last_regime,
            "last_run_ts": state.last_run_ts if state else None,
            "last_cycle_success": state.last_cycle_success if state else True,
            "critical_errors_count": len(critical_errors),
            "latest_error": critical_errors[-1] if critical_errors else None,
            "critical_errors": critical_errors[-5:],
            "kill_switch": self.config.risk.kill_switch if self.config else False,
            "assets": list(self.config.strategy.assets.keys()) if self.config else [],
            "market_metrics": metrics,
            "strategy_state": state.strategy_state if state else {},
        }
        self._send_json(data)

    def _handle_portfolio(self) -> None:
        if not self.gateway:
            self._send_json({"error": "Gateway unavailable"}, status=503)
            return

        try:
            from src.services.portfolio_service import PortfolioService
            ps = PortfolioService(self.gateway)
            snapshot = ps.get_portfolio()

            state = self.state_store.load_state() if self.state_store else None
            initial_val = state.session_initial_value_usd if state else None
            initial_prices = dict(state.session_initial_prices) if (state and state.session_initial_prices) else {}
            state_updated = False

            strategy_positions = state.strategy_state.get("positions", {}) if (state and state.strategy_state) else {}

            holdings_list = []
            for symbol, h in snapshot.holdings.items():
                weight = (h.value_usd / snapshot.total_value_usd * 100) if snapshot.total_value_usd > 0 else 0.0
                
                # Determine current unit price of asset
                current_price = 0.0
                if symbol in ("USDT", "USD", "BUSD", "USDC"):
                    current_price = 1.0
                elif h.total != 0:
                    current_price = abs(h.value_usd / h.total)
                else:
                    try:
                        pair = f"{symbol}/USDT"
                        current_price = self.gateway.fetch_ticker_price(pair)
                    except Exception:
                        current_price = 0.0

                # Auto-record initial price baseline for period return calculation
                init_price = initial_prices.get(symbol)
                if (init_price is None or init_price <= 0) and current_price > 0:
                    initial_prices[symbol] = current_price
                    init_price = current_price
                    state_updated = True

                # Determine active strategy entry price if available, otherwise baseline init_price
                pos_info = strategy_positions.get(symbol, {})
                entry_price = float(pos_info.get("entry_px", 0.0) or 0.0) if pos_info.get("active") else 0.0
                if entry_price <= 0:
                    entry_price = init_price or 0.0

                change_pct = 0.0
                net_change_pct = 0.0
                if symbol not in ("USDT", "USD", "BUSD", "USDC") and entry_price > 0 and current_price > 0:
                    change_pct = ((current_price - entry_price) / entry_price) * 100.0
                    # Net PnL % relative to entry price, deducting 0.1% buy fee and 0.1% estimated sell fee
                    # Net multiplier = (1 - 0.001) * (1 - 0.001) * (current_price / entry_price)
                    net_multiplier = 0.998001 * (current_price / entry_price)
                    net_change_pct = (net_multiplier - 1.0) * 100.0

                holdings_list.append({
                    "symbol": symbol,
                    "free": h.free,
                    "locked": h.locked,
                    "total": h.total,
                    "value_usd": h.value_usd,
                    "weight_pct": round(weight, 2),
                    "current_price": round(current_price, 4 if current_price < 10 else 2),
                    "initial_price": round(init_price, 4 if init_price < 10 else 2) if init_price else None,
                    "entry_price": round(entry_price, 4 if entry_price < 10 else 2) if entry_price else None,
                    "change_pct": round(change_pct, 2),
                    "net_change_pct": round(net_change_pct, 2),
                })

            crypto_value_usd = sum(
                h.value_usd for sym, h in snapshot.holdings.items()
                if sym not in ("USDT", "USD", "BUSD", "USDC")
            )
            estimated_exit_fees_usd = crypto_value_usd * 0.001
            net_total_value_usd = snapshot.total_value_usd - estimated_exit_fees_usd

            net_pnl_usd = (net_total_value_usd - initial_val) if initial_val is not None else 0.0
            net_pnl_pct = (net_pnl_usd / initial_val * 100.0) if (initial_val and initial_val > 0) else 0.0

            # Record historical PnL point
            if state:
                current_time_ms = snapshot.timestamp_ms or int(time.time() * 1000)
                if not hasattr(state, "pnl_history") or state.pnl_history is None:
                    state.pnl_history = []
                
                should_append = False
                if not state.pnl_history:
                    should_append = True
                else:
                    last_point = state.pnl_history[-1]
                    last_ts = last_point.get("ts", 0)
                    last_val = last_point.get("val", 0.0)
                    if (current_time_ms - last_ts >= 30000) or abs(last_val - net_total_value_usd) >= 0.05:
                        should_append = True

                if should_append:
                    point = {
                        "ts": current_time_ms,
                        "val": round(net_total_value_usd, 2),
                        "pnl_usd": round(net_pnl_usd, 2),
                        "pnl_pct": round(net_pnl_pct, 2),
                    }
                    state.pnl_history.append(point)
                    if len(state.pnl_history) > 5000:
                        state.pnl_history = state.pnl_history[-5000:]
                    state_updated = True

            data = {
                "total_value_usd": round(snapshot.total_value_usd, 2),
                "net_total_value_usd": round(net_total_value_usd, 2),
                "estimated_exit_fees_usd": round(estimated_exit_fees_usd, 2),
                "net_pnl_usd": round(net_pnl_usd, 2),
                "net_pnl_pct": round(net_pnl_pct, 2),
                "holdings": holdings_list,
                "timestamp_ms": snapshot.timestamp_ms,
                "session_initial_value_usd": round(initial_val, 2) if initial_val is not None else None,
                "session_fees": state.session_fees if state else {},
                "session_initial_prices": initial_prices,
                "pnl_history": state.pnl_history if state else [],
            }

            # Auto-initialize baseline if empty
            if state:
                if initial_val is None:
                    state.session_initial_value_usd = snapshot.total_value_usd
                    state_updated = True
                    data["session_initial_value_usd"] = round(snapshot.total_value_usd, 2)
                if state_updated:
                    state.session_initial_prices = initial_prices
                    self.state_store.save_state(state)

            self._send_json(data)
        except Exception as e:
            logger.error("Failed to compute portfolio status: %s", e)
            err_msg = str(e)
            is_auth_err = ("-2015" in err_msg or "Invalid API-key" in err_msg or "Authentication failed" in err_msg or "permissions" in err_msg)
            self._send_json({
                "error": err_msg,
                "is_auth_error": is_auth_err,
                "code": -2015 if "-2015" in err_msg else 500,
                "server_ip": "172.236.200.8",
                "message": "Binance authentication rejected (-2015: Invalid API-key, IP whitelist, or Futures permission)." if is_auth_err else err_msg,
            }, status=500)

    def _handle_orders(self) -> None:
        state = self.state_store.load_state() if self.state_store else None
        if not state:
            self._send_json({"pending": [], "completed": []})
            return

        data = {
            "pending": state.pending_orders,
            "completed": state.completed_orders[-1000:],  # Return up to 1000 completed orders
        }
        self._send_json(data)

    def _get_active_log_path(self) -> Path:
        candidate_paths = []
        if self.log_file_path:
            candidate_paths.append(Path(self.log_file_path))
        if self.config and getattr(self.config, "logging", None) and getattr(self.config.logging, "file", None):
            candidate_paths.append(Path(self.config.logging.file))

        project_dir = Path(__file__).resolve().parent.parent.parent.parent
        candidate_paths.extend([
            Path("logs/bot.log"),
            project_dir / "RUN" / "logs" / "bot.log",
            project_dir / "logs" / "bot.log",
        ])

        existing = []
        seen = set()
        for cand in candidate_paths:
            try:
                res = cand.resolve()
                if res in seen:
                    continue
                seen.add(res)
                if res.is_file():
                    existing.append(res)
            except Exception:
                pass

        if existing:
            # Pick the log file that was most recently modified
            existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return existing[0]

        return candidate_paths[0].resolve() if candidate_paths else Path("logs/bot.log").resolve()

    def _handle_logs(self) -> None:
        log_path = self._get_active_log_path()
        lines = []
        ignored_patterns = (
            "Loaded state:",
            "Portfolio snapshot:",
            "No state file found at",
            "No new closed candles",
            "Starting reconciliation...",
            "Reconciliation complete — state is consistent",
        )
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                    filtered = [
                        line.strip() for line in all_lines
                        if line.strip() and not any(pat in line for pat in ignored_patterns)
                    ]
                    lines = filtered[-1000:]  # Last 1000 meaningful lines
            except Exception as e:
                lines = [f"Error reading log file: {e}"]
        else:
            lines = ["No log file generated yet."]

        self._send_json({"logs": lines})

    def _handle_download_logs(self) -> None:
        log_path = self._get_active_log_path()
        if not os.path.exists(log_path):
            self._send_json({"error": "Log file not found"}, status=404)
            return

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            ignored_patterns = (
                "Loaded state:",
                "Portfolio snapshot:",
                "No state file found at",
                "No new closed candles",
                "Starting reconciliation...",
                "Reconciliation complete — state is consistent",
            )
            filtered = [
                line for line in all_lines
                if line.strip() and not any(pat in line for pat in ignored_patterns)
            ]
            content = "".join(filtered).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="bot_logs.log"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_json({"error": f"Failed to download logs: {e}"}, status=500)

    def _handle_clear_logs(self) -> None:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
            cleared_line = f"{timestamp} | INFO     | bot.web.server | System logs cleared by user\n"

            candidate_paths = []
            if self.log_file_path:
                candidate_paths.append(Path(self.log_file_path))
            if self.config and getattr(self.config, "logging", None) and getattr(self.config.logging, "file", None):
                candidate_paths.append(Path(self.config.logging.file))

            project_dir = Path(__file__).resolve().parent.parent.parent.parent
            candidate_paths.extend([
                Path("logs/bot.log"),
                project_dir / "RUN" / "logs" / "bot.log",
                project_dir / "logs" / "bot.log",
            ])

            cleared_any = False
            seen = set()
            for cand in candidate_paths:
                try:
                    res = cand.resolve()
                    if res in seen:
                        continue
                    seen.add(res)
                    if res.is_file():
                        with open(res, "w", encoding="utf-8") as f:
                            f.write(cleared_line)
                        cleared_any = True
                except Exception as ex:
                    logger.warning("Could not truncate candidate log file %s: %s", cand, ex)

            if not cleared_any:
                active_path = self._get_active_log_path()
                active_path.parent.mkdir(parents=True, exist_ok=True)
                with open(active_path, "w", encoding="utf-8") as f:
                    f.write(cleared_line)

            for h in list(logging.getLogger().handlers) + list(logging.getLogger("bot").handlers):
                try:
                    h.flush()
                except Exception:
                    pass

            logger.info("System logs cleared by user via API")
            self._send_json({"success": True, "message": "System logs cleared successfully"})
        except Exception as e:
            logger.error("Failed to clear logs via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    @staticmethod
    def _fetch_ohlcv_safe(exchange: Any, symbol: str, timeframe: str = "4h", limit: int = 120) -> list:
        try:
            if exchange and hasattr(exchange, "fetch_ohlcv"):
                res = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                if res and len(res) > 0:
                    return res
        except Exception as e:
            logger.debug("CCXT fetch_ohlcv failed for %s (%s): %s", symbol, timeframe, e)

        import urllib.request
        import json
        clean_sym = symbol.split(":")[0].replace("/", "").replace("-", "")
        urls = [
            f"https://api.binance.com/api/v3/klines?symbol={clean_sym}&interval={timeframe}&limit={limit}",
            f"https://data-api.binance.vision/api/v3/klines?symbol={clean_sym}&interval={timeframe}&limit={limit}",
            f"https://api1.binance.com/api/v3/klines?symbol={clean_sym}&interval={timeframe}&limit={limit}",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        raw = json.loads(resp.read().decode("utf-8"))
                        return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in raw]
            except Exception:
                pass
        return []

    def _handle_strategy_conditions(self) -> None:
        try:
            import time
            import numpy as np
            import pandas as pd
            from src.strategy.indicators import add_indicators, candles_to_dataframe
            from src.core.models import Candle

            state = self.state_store.load_state() if self.state_store else None
            last_regime = state.last_regime if (state and state.last_regime) else "BEAR"
            strat_state = state.strategy_state if state else {}
            macro_state = strat_state.get("macro_state", {}) if isinstance(strat_state, dict) else {}
            positions_state = (
                macro_state.get("positions")
                or (strat_state.get("positions") if isinstance(strat_state, dict) else {})
                or {}
            )
            bull_peak = float(macro_state.get("bull_peak") or strat_state.get("bull_peak", 0.0) or 0.0)
            bars_since_circuit_trip = int(macro_state.get("bars_since_circuit_trip") or strat_state.get("bars_since_circuit_trip", 999))

            min_adx_map = {"BTC": 20.0, "ETH": 22.0, "SOL": 24.0}
            weight_map = {"BTC": 0.40, "ETH": 0.30, "SOL": 0.30}
            trail_atr_map = {"BTC": (10.0, 5.0), "ETH": (9.0, 4.5), "SOL": (7.5, 5.0)}
            init_risk_atr_map = {"BTC": 4.0, "ETH": 4.0, "SOL": 3.0}

            symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
            assets_data = {}
            btc_daily_close = 0.0
            btc_sma150 = 0.0
            btc_ema50_daily = 0.0
            btc_ema20_daily = 0.0
            btc_ema9_daily = 0.0
            btc_5d_high = 0.0
            btc_pb_from_5d = 0.0
            btc_atr_pct = 0.035
            btc_adx_daily = 20.0
            btc_intraday_dip_pct = 0.0

            exchange = _get_shared_exchange(self.gateway)

            # 1. Fetch BTC daily first to establish Macro Regime & Crash Shield parameters
            try:
                btc_daily_ohlcv = self._fetch_ohlcv_safe(exchange, "BTC/USDT", timeframe="1d", limit=200)
                if btc_daily_ohlcv:
                    df_btc_daily = pd.DataFrame(btc_daily_ohlcv, columns=["ts", "Open", "High", "Low", "Close", "Vol"])
                    btc_daily_close = float(df_btc_daily["Close"].iloc[-1])
                    if len(df_btc_daily) >= 150:
                        btc_sma150 = float(df_btc_daily["Close"].rolling(150).mean().iloc[-1])
                    if len(df_btc_daily) >= 50:
                        btc_ema50_daily = float(df_btc_daily["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
                    if len(df_btc_daily) >= 20:
                        btc_ema20_daily = float(df_btc_daily["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
                    if len(df_btc_daily) >= 9:
                        btc_ema9_daily = float(df_btc_daily["Close"].ewm(span=9, adjust=False).mean().iloc[-1])
                    if len(df_btc_daily) >= 5:
                        btc_5d_high = float(df_btc_daily["High"].rolling(5).max().iloc[-1])
                        if btc_5d_high > 0:
                            btc_pb_from_5d = round(((btc_daily_close - btc_5d_high) / btc_5d_high) * 100.0, 2)
                    if len(df_btc_daily) >= 14:
                        tr1 = df_btc_daily["High"] - df_btc_daily["Low"]
                        tr2 = (df_btc_daily["High"] - df_btc_daily["Close"].shift(1)).abs()
                        tr3 = (df_btc_daily["Low"] - df_btc_daily["Close"].shift(1)).abs()
                        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                        atr14 = tr.rolling(14).mean()
                        btc_atr_pct = float((atr14 / df_btc_daily["Close"]).iloc[-1])

                        # ADX daily
                        delta = df_btc_daily["Close"].diff()
                        high_diff = df_btc_daily["High"].diff()
                        low_diff = -df_btc_daily["Low"].diff()
                        pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
                        neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
                        atr_safe = atr14.replace(0, np.nan)
                        pos_di = (100 * pd.Series(pos_dm, index=df_btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
                        neg_di = (100 * pd.Series(neg_dm, index=df_btc_daily.index).ewm(alpha=1/14, min_periods=14).mean() / atr_safe).fillna(0.0)
                        denom = (pos_di + neg_di).replace(0, np.nan)
                        dx = (100 * (pos_di - neg_di).abs() / denom).fillna(0.0)
                        adx_daily = dx.ewm(alpha=1/14, min_periods=14).mean()
                        if not adx_daily.dropna().empty:
                            btc_adx_daily = float(adx_daily.dropna().iloc[-1])

                    c_open = float(df_btc_daily["Open"].iloc[-1])
                    c_low = float(df_btc_daily["Low"].iloc[-1])
                    if c_open > 0:
                        btc_intraday_dip_pct = round(((c_low - c_open) / c_open) * 100.0, 2)
            except Exception as ex_btc:
                logger.warning("Failed fetching BTC daily candles for macro regime: %s", ex_btc)

            if btc_daily_close <= 0.0 and self.gateway:
                try:
                    btc_daily_close = float(self.gateway.fetch_ticker_price("BTC/USDT"))
                except Exception:
                    pass

            # Regime Determination: matches engine.py and regime_adaptive_strategy.py
            if btc_daily_close <= 0.0 or btc_sma150 <= 0.0:
                macro_regime = (last_regime.upper() if last_regime else "BEAR")
            else:
                is_bear_trend = (btc_daily_close < btc_ema20_daily) and (
                    (btc_ema20_daily < btc_ema50_daily) or (btc_daily_close < btc_sma150)
                )
                macro_regime = "BEAR" if is_bear_trend or (btc_daily_close < btc_sma150) else "BULL"

            peak = max(bull_peak, btc_daily_close)
            pullback_pct = round(((btc_daily_close - peak) / peak) * 100.0, 2) if peak > 0 else 0.0
            under_ema = (btc_daily_close < btc_ema20_daily) if btc_ema20_daily > 0 else False

            # Momentum Validation Gate (Institutional Crash Shield)
            strat_cfg = getattr(self.config, "strategy", None) if self.config else None
            try:
                raw_cutoff = getattr(strat_cfg, "momentum_cutoff_pct", -0.02)
                cutoff_pct = float(raw_cutoff) * 100.0 if raw_cutoff is not None else -2.0
            except (TypeError, ValueError):
                cutoff_pct = -2.0

            try:
                raw_wick = getattr(strat_cfg, "flash_wick_limit", -0.038)
                flash_wick_limit_pct = float(raw_wick) * 100.0 if raw_wick is not None else -3.8
            except (TypeError, ValueError):
                flash_wick_limit_pct = -3.8

            flash_triggered = (btc_intraday_dip_pct < flash_wick_limit_pct)

            in_momentum = (macro_regime == "BULL") and (btc_pb_from_5d >= cutoff_pct) and (btc_daily_close >= btc_ema9_daily)
            safe_haven_active = (macro_regime == "BULL") and not in_momentum

            try:
                conviction_lev = float(getattr(strat_cfg, "conviction_leverage", 10.0))
            except (TypeError, ValueError):
                conviction_lev = 10.0

            try:
                mid_lev = float(getattr(strat_cfg, "mid_leverage", 5.0))
            except (TypeError, ValueError):
                mid_lev = 5.0

            try:
                base_lev = float(getattr(strat_cfg, "base_leverage", 2.5))
            except (TypeError, ValueError):
                base_lev = 2.5

            raw_ladder = getattr(strat_cfg, "ladder_steps", [1.0, 2.0, 4.0, 10.0])
            ladder_steps = [float(x) for x in raw_ladder] if isinstance(raw_ladder, (list, tuple)) else [1.0, 2.0, 4.0, 10.0]
            if not ladder_steps:
                ladder_steps = [1.0, 2.0, 4.0, 10.0]

            if macro_regime == "BEAR":
                leverage = 0.0
                active_tier = "BEAR SHORT HEDGE (35% @ 2.0x Short BTC + 65% Cash Yield)"
                ladder_step = "N/A"
                ladder_cap = 0.0
                total_crypto_exposure = -70.0
            elif safe_haven_active:
                leverage = 1.0
                active_tier = f"🛡️ Crash Shield Safe Haven (1.0x Spot | 60% Cash @ 4% APY, 5d_pb={btc_pb_from_5d:+.1f}%)"
                ladder_step = "Safe Haven (Protected)"
                ladder_cap = 1.0
                total_crypto_exposure = 30.0
            else:
                # Active in Momentum: Conviction Rocket & Volatility Tiers
                if btc_atr_pct < 0.022 and btc_adx_daily >= 24.0:
                    selected_lev = conviction_lev
                    active_tier = f"🚀 Conviction Rocket 10x (ATR={btc_atr_pct*100:.2f}%, ADX={btc_adx_daily:.1f})"
                elif btc_atr_pct < 0.028:
                    selected_lev = mid_lev
                    active_tier = f"⚡ Mid Volatility Tier ({mid_lev:.1f}x, ATR={btc_atr_pct*100:.2f}%)"
                else:
                    selected_lev = base_lev
                    active_tier = f"Base Bull Tier ({base_lev:.1f}x, ATR={btc_atr_pct*100:.2f}%)"

                # Re-entry ladder capping
                if 1 <= bars_since_circuit_trip <= len(ladder_steps):
                    ladder_step = f"Step {bars_since_circuit_trip}/{len(ladder_steps)}"
                    ladder_cap = ladder_steps[bars_since_circuit_trip - 1]
                    if selected_lev > ladder_cap:
                        selected_lev = ladder_cap
                        active_tier += f" | Re-Entry Ladder ({ladder_cap:.1f}x Cap)"
                else:
                    ladder_step = f"Completed (Full {conviction_lev:.0f}x Unlocked)"
                    ladder_cap = conviction_lev

                # Flash circuit breaker override
                if selected_lev > 1.0 and flash_triggered:
                    selected_lev = 1.0
                    active_tier = f"⚡ Flash Breaker Triggered (1.0x Cut, dip={btc_intraday_dip_pct:.1f}%)"

                leverage = selected_lev
                total_crypto_exposure = round((0.70 * leverage + 0.30) * 100.0, 0)

            # 2. Iterate each symbol and compute decision tree & indicator meters
            for pair in symbols:
                coin = pair.split("/")[0]
                try:
                    ohlcv = self._fetch_ohlcv_safe(exchange, pair, timeframe="4h", limit=300)
                    if not ohlcv:
                        continue
                    candles = [
                        Candle(
                            timestamp_ms=int(c[0]),
                            open=float(c[1]),
                            high=float(c[2]),
                            low=float(c[3]),
                            close=float(c[4]),
                            volume=float(c[5]),
                        )
                        for c in ohlcv
                    ]
                    df = candles_to_dataframe(candles)
                    df_ind = add_indicators(df)
                    if df_ind.empty:
                        continue

                    r_last = df_ind.iloc[-1]
                    c_close = float(r_last["Close"])
                    c_high = float(r_last["High"])
                    c_low = float(r_last["Low"])
                    c_atr = float(r_last["ATR"])
                    c_adx = float(r_last["ADX"]) if not np.isnan(r_last["ADX"]) else 0.0
                    donchian30 = float(r_last["Donchian30"]) if not np.isnan(r_last["Donchian30"]) else 0.0
                    ema20 = float(r_last["EMA20"])
                    ema50 = float(r_last["EMA50"])
                    ema200 = float(r_last["EMA200"])
                    asset_regime = str(r_last["Regime"])

                    min_adx = min_adx_map.get(coin, 20.0)
                    target_weight = weight_map.get(coin, 0.33)

                    regime_ok = asset_regime in ("STRONG_BULL_TREND", "TREND")
                    donchian_ok = (c_close >= donchian30) if donchian30 > 0 else False
                    adx_ok = (c_adx >= min_adx)
                    all_entry_met = (regime_ok and donchian_ok and adx_ok)

                    gap_usd = round(c_close - donchian30, 2) if donchian30 > 0 else 0.0
                    gap_pct = round(((c_close - donchian30) / donchian30) * 100.0, 2) if donchian30 > 0 else 0.0

                    pos_info = positions_state.get(coin, {})
                    is_active = bool(pos_info.get("active", False))
                    entry_px = float(pos_info.get("entry_px", 0.0) or 0.0)
                    high_water = float(pos_info.get("high_water", 0.0) or 0.0)
                    atr_at_entry = float(pos_info.get("atr_at_entry", 0.0) or 0.0)
                    entry_mode = str(pos_info.get("mode", "STRONG_BULL_TREND"))

                    trail_tuple = trail_atr_map.get(coin, (10.0, 5.0))
                    tb = trail_tuple[0] if entry_mode == "STRONG_BULL_TREND" else trail_tuple[1]
                    init_risk_atr = init_risk_atr_map.get(coin, 4.0)

                    trailing_stop = round(high_water - tb * c_atr, 2) if is_active and high_water > 0 else None
                    initial_stop = round(entry_px - init_risk_atr * atr_at_entry, 2) if is_active and entry_px > 0 else None

                    buy_tree_nodes = [
                        {
                            "id": "node_macro_regime",
                            "title": "1. משטר שוק מקרו (Macro Regime)",
                            "subtitle": "BTC מעל SMA-150 יומית (תנאי בסיס ללונג)",
                            "criteria": f"BTC Close > SMA150 (${btc_sma150:,.0f})",
                            "actual": f"${btc_daily_close:,.0f} vs ${btc_sma150:,.0f}",
                            "met": macro_regime == "BULL",
                        },
                        {
                            "id": "node_crash_shield_momentum",
                            "title": "2. מגן מפולת ושער מומנטום (Crash Shield Gate)",
                            "subtitle": "נסיגה עד 2%- משיא 5 ימים ומחיר מעל EMA9 יומית לפתיחת מינוף מוגבר",
                            "criteria": f"5d PB >= {cutoff_pct:.1f}% & Close >= EMA9 (${btc_ema9_daily:,.0f})",
                            "actual": f"5d PB: {btc_pb_from_5d:+.2f}% | EMA9: ${btc_ema9_daily:,.0f} ({'🚀 In Momentum' if in_momentum else '🛡️ Safe Haven'})",
                            "met": in_momentum,
                        },
                        {
                            "id": "node_leverage_tier",
                            "title": "3. מדרג מינוף שוורית (Dynamic Conviction Tier)",
                            "subtitle": "מינוף דינמי (10.0x / 5.0x / 2.5x) לפי ATR% ו-ADX וסולם התאוששות",
                            "criteria": f"Dynamic Leverage: {leverage:.1f}x (חשיפה {total_crypto_exposure:.0f}%)",
                            "actual": active_tier,
                            "met": leverage >= 2.5,
                        },
                        {
                            "id": "node_ema_alignment",
                            "title": "4. מבנה ממוצעים (EMA Trend)",
                            "subtitle": "מגמת עלייה בנכס (Strong Bull / Trend)",
                            "criteria": "Regime in [STRONG_BULL, TREND]",
                            "actual": asset_regime,
                            "met": regime_ok,
                        },
                        {
                            "id": "node_donchian_breakout",
                            "title": "5. פריצת דונצ'יאן 30 / כניסה חוזרת EMA20",
                            "subtitle": "סגירת 4H מעל שיא 30 נרות או תיקון ממוצע",
                            "criteria": f"Close >= Donchian30 (${donchian30:,.2f}) OR EMA20 Re-entry",
                            "actual": f"${c_close:,.2f} ({gap_pct:+.2f}%)",
                            "met": donchian_ok,
                        },
                        {
                            "id": "node_adx_filter",
                            "title": "6. עוצמת מגמה (ADX Filter)",
                            "subtitle": "מדד ADX מעל סף המינימום לעוצמה",
                            "criteria": f"ADX >= {min_adx}",
                            "actual": f"{c_adx:.1f}",
                            "met": adx_ok,
                        },
                        {
                            "id": "node_pyramiding",
                            "title": "7. פירמידינג מוגן (Shielded Pyramiding Additions)",
                            "subtitle": "הוספת 50%+ / 30%+ בטרנד חזק (נעול ומבוטל ב-Safe Haven)",
                            "criteria": "Open PnL >= 0.6 ATR & Pullback >= 1.5 ATR (Strong Bull & In Momentum)",
                            "actual": "Active Position Pyramiding Ready" if (is_active and in_momentum) else ("Safe Haven Locked" if safe_haven_active else "Initial Entry Mode"),
                            "met": True if (not is_active or (asset_regime == "STRONG_BULL_TREND" and in_momentum)) else False,
                        }
                    ]

                    sell_tree_nodes = [
                        {
                            "id": "node_bear_emergency",
                            "title": "1. יציאת דובים ושורט 35% (Bear Exit & 35% Short Hedge)",
                            "subtitle": "מעבר למשטר דובים (BTC < SMA150) ופתיחת 35% שורט ממונף 2.0x על BTC (סה\"כ 70% שורט)",
                            "criteria": "BTC < SMA150 or EMA20 < EMA50 -> Close Longs & Open 35% @ 2.0x BTC Short (65% Cash APY)",
                            "actual": "BEAR ACTIVE (35% @ 2.0x Short BTC + 65% Cash APY)" if macro_regime == "BEAR" else "BULL ACTIVE (Safe)",
                            "triggered": macro_regime == "BEAR",
                        },
                        {
                            "id": "node_crash_shield",
                            "title": "2. מגן מפולת מוסדי (Institutional Crash Shield)",
                            "subtitle": "נסיגה של 2%- משיא 5 ימים או שבירת EMA9 מורידה מיד ל-1.0x ספוט (60% מזומן בתשואה 4%)",
                            "criteria": f"5d Pullback < {cutoff_pct:.1f}% OR Close < EMA9 (${btc_ema9_daily:,.0f})",
                            "actual": f"5d PB: {btc_pb_from_5d:+.2f}%, Under EMA9: {btc_daily_close < btc_ema9_daily} ({'🛡️ TRIGGERED' if safe_haven_active else 'SAFE'})",
                            "triggered": safe_haven_active,
                        },
                        {
                            "id": "node_flash_circuit_breaker",
                            "title": "3. מפסק ביטחון לנרות פלאש (Flash Circuit Breaker)",
                            "subtitle": f"צניחה תוך-יומית מנר הפתיחה מעבר ל-{flash_wick_limit_pct:.1f}%- חותכת מיד ל-1.0x",
                            "criteria": f"Intraday Dip < {flash_wick_limit_pct:.1f}% -> Cut to 1.0x",
                            "actual": f"Intraday Dip: {btc_intraday_dip_pct:+.2f}% ({'TRIGGERED' if flash_triggered else 'SAFE'})",
                            "triggered": flash_triggered,
                        },
                        {
                            "id": "node_initial_risk_stop",
                            "title": "4. סטופ סיכון ראשוני (Initial Risk Stop)",
                            "subtitle": "ירידה מתחת לסיכון הראשוני המורשה בכניסה",
                            "criteria": f"Low <= Initial Stop (${initial_stop:,.2f})" if initial_stop else f"Initial Stop = ${c_close - init_risk_atr * c_atr:,.2f}",
                            "actual": f"Low ${c_low:,.2f}" + (f" vs Stop ${initial_stop:,.2f}" if initial_stop else ""),
                            "triggered": (c_low <= initial_stop) if (is_active and initial_stop) else False,
                        },
                        {
                            "id": "node_atr_trailing_stop",
                            "title": "5. סטופ נגרר דינמי (ATR Trailing Stop)",
                            "subtitle": "נפילה משיא הפוזיציה מעבר למרחק ATR מורשה",
                            "criteria": f"Low <= Trailing Stop (${trailing_stop:,.2f})" if trailing_stop else f"Trailing Stop = ${c_high - tb * c_atr:,.2f}",
                            "actual": f"Low ${c_low:,.2f}" + (f" vs Stop ${trailing_stop:,.2f}" if trailing_stop else ""),
                            "triggered": (c_low <= trailing_stop) if (is_active and trailing_stop) else False,
                        },
                        {
                            "id": "node_ema_breakdown",
                            "title": "6. שבירת ממוצעים (EMA Exit)",
                            "subtitle": "סגירה מתחת ל-EMA50 (Trend בלבד — EMA200 לא פעיל ב-Strong Bull)",
                            "criteria": "Close < EMA50 (Trend only, ema_exit_strong=DISABLED)",
                            "actual": f"Close ${c_close:,.2f} vs EMA50 ${ema50:,.2f} / EMA200 ${ema200:,.2f}",
                            "triggered": (c_close < ema50) if (entry_mode == "TREND") else False,
                        }
                    ]

                    assets_data[coin] = {
                        "symbol": pair,
                        "close": round(c_close, 2),
                        "high": round(c_high, 2),
                        "low": round(c_low, 2),
                        "donchian30": round(donchian30, 2),
                        "adx": round(c_adx, 1),
                        "min_adx": min_adx,
                        "atr": round(c_atr, 2),
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "ema200": round(ema200, 2),
                        "asset_regime": asset_regime,
                        "target_weight_pct": round(target_weight * 100, 0),
                        "entry_conditions": {
                            "regime_ok": regime_ok,
                            "donchian_ok": donchian_ok,
                            "adx_ok": adx_ok,
                            "all_met": all_entry_met,
                            "donchian_gap_usd": gap_usd,
                            "donchian_gap_pct": gap_pct,
                        },
                        "position": {
                            "active": is_active,
                            "entry_price": round(entry_px, 2) if entry_px > 0 else None,
                            "high_water": round(high_water, 2) if high_water > 0 else None,
                            "trailing_stop": trailing_stop,
                            "initial_stop": initial_stop,
                            "ema50_exit_price": round(ema50, 2),
                            "ema200_exit_price": round(ema200, 2),
                        },
                        "buy_tree_nodes": buy_tree_nodes,
                        "sell_tree_nodes": sell_tree_nodes,
                    }

                except Exception as ex:
                    logger.warning("Failed processing conditions for %s: %s", pair, ex)

            result = {
                "timestamp_ms": int(time.time() * 1000),
                "macro_regime": {
                    "regime": macro_regime,
                    "btc_close": round(btc_daily_close, 2),
                    "btc_sma150": round(btc_sma150, 2),
                    "btc_ema50_daily": round(btc_ema50_daily, 2),
                    "btc_ema20_daily": round(btc_ema20_daily, 2),
                    "btc_ema9_daily": round(btc_ema9_daily, 2),
                    "btc_5d_high": round(btc_5d_high, 2),
                    "dist_from_5d_high_pct": round(btc_pb_from_5d, 2),
                    "in_momentum": in_momentum,
                    "safe_haven_active": safe_haven_active,
                    "conviction_rocket_active": (leverage >= 10.0),
                    "sma_gap_pct": round(((btc_daily_close - btc_sma150) / btc_sma150) * 100.0, 2) if btc_sma150 > 0 else 0.0,
                    "bull_peak": round(peak, 2),
                    "pullback_pct": pullback_pct,
                    "under_ema20_daily": under_ema,
                    "risk_guard_active": safe_haven_active,
                    "stepped_pullback_active": False,
                    "effective_leverage": round(leverage, 1),
                    "btc_atr_pct": round(btc_atr_pct * 100.0, 2),
                    "btc_adx_daily": round(btc_adx_daily, 1),
                    "btc_intraday_dip_pct": btc_intraday_dip_pct,
                    "flash_wick_limit_pct": flash_wick_limit_pct,
                    "flash_circuit_triggered": flash_triggered,
                    "bars_since_circuit_trip": bars_since_circuit_trip,
                    "ladder_step": ladder_step,
                    "ladder_cap": round(ladder_cap, 1),
                    "active_tier": active_tier,
                    "total_crypto_weight_pct": total_crypto_exposure,
                    "cash_weight_pct": 60.0 if safe_haven_active else (65.0 if macro_regime == "BEAR" else 0.0),
                    "bear_short_hedge_pct": 35.0 if macro_regime == "BEAR" else 0.0,
                },
                "assets": assets_data,
            }
            self._send_json(result)
        except Exception as e:
            logger.error("Failed to compute strategy conditions: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_trigger_cycle(self) -> None:
        if self.orchestrator:
            try:
                success = self.orchestrator.run_once(force=True)
                self._send_json({"success": success, "message": "Cycle completed (forced evaluation)"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, status=500)
        else:
            self._send_json({"error": "Orchestrator not attached"}, status=400)

    def _handle_toggle_killswitch(self) -> None:
        if self.config:
            self.config.risk.kill_switch = not self.config.risk.kill_switch
            status = "ACTIVATED" if self.config.risk.kill_switch else "DEACTIVATED"
            logger.warning("Kill switch toggled via API: %s", status)

            # Persist to config.yaml so restarts maintain kill switch state
            try:
                from pathlib import Path
                from src.config.config_manager import ConfigManager
                cfg_path = "RUN/config.yaml" if Path("RUN/config.yaml").exists() else "config.yaml"
                cm = ConfigManager(cfg_path)
                cm.save_kill_switch(self.config.risk.kill_switch)
            except Exception as ex:
                logger.warning("Could not persist kill switch state to config.yaml: %s", ex)

            self._send_json({"kill_switch": self.config.risk.kill_switch, "message": f"Kill switch {status}"})
        else:
            self._send_json({"error": "Config unavailable"}, status=500)

    def _handle_get_dry_run_balances(self) -> None:
        if hasattr(self.gateway, "fetch_balance"):
            raw_bal = self.gateway.fetch_balance()
            balances = {k: v.get("total", 0.0) for k, v in raw_bal.items() if isinstance(v, dict)}
        elif self.config and hasattr(self.config, "dry_run"):
            balances = self.config.dry_run.initial_balances
        else:
            balances = {"USDT": 1000.0}
        self._send_json({"balances": balances})

    def _handle_update_dry_run_balances(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
            new_balances = data.get("balances", {})

            parsed = {str(k).upper(): float(v) for k, v in new_balances.items() if float(v) >= 0}
            if not parsed:
                self._send_json({"error": "No valid balances provided"}, status=400)
                return

            if hasattr(self.gateway, "set_balances"):
                self.gateway.set_balances(parsed)

            if self.config and hasattr(self.config, "dry_run"):
                self.config.dry_run.initial_balances = parsed

            # Persist to config.yaml on disk so restarts retain these holdings
            try:
                from pathlib import Path
                from src.config.config_manager import ConfigManager
                cfg_path = "RUN/config.yaml" if Path("RUN/config.yaml").exists() else "config.yaml"
                cm = ConfigManager(cfg_path)
                cm.save_dry_run_balances(parsed)
            except Exception as ex:
                logger.warning("Could not save dry run balances to config.yaml: %s", ex)

            # Persist to state_store (bot_state.json)
            if self.state_store:
                try:
                    state = self.state_store.load_state()
                    state.strategy_state["dry_run_balances"] = parsed
                    if data.get("clear_history", True):
                        state.completed_orders.clear()
                        state.pending_orders.clear()
                        state.session_initial_value_usd = None
                        state.session_fees.clear()
                        state.session_initial_prices.clear()
                        if hasattr(state, "pnl_history") and state.pnl_history:
                            state.pnl_history.clear()
                    self.state_store.save_state(state)
                    logger.info("Persisted dry run balances into bot_state.json")
                except Exception as ex:
                    logger.warning("Could not persist dry run balances to state store: %s", ex)

            logger.info("Dry run balances updated via API: %s", parsed)
            self._send_json({"success": True, "balances": parsed, "message": "Dry Run holdings updated successfully"})
        except Exception as e:
            logger.error("Failed to update dry run balances via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_reset_stats(self) -> None:
        if not self.state_store:
            self._send_json({"error": "State store unavailable"}, status=503)
            return

        try:
            state = self.state_store.load_state()

            if self.gateway:
                from src.services.portfolio_service import PortfolioService
                ps = PortfolioService(self.gateway)
                snapshot = ps.get_portfolio()
                crypto_val = sum(
                    h.value_usd for sym, h in snapshot.holdings.items()
                    if sym not in ("USDT", "USD", "BUSD", "USDC")
                )
                est_exit_fees = crypto_val * 0.001
                state.session_initial_value_usd = snapshot.total_value_usd - est_exit_fees
            else:
                state.session_initial_value_usd = None

            state.session_fees.clear()
            state.session_initial_prices.clear()
            state.completed_orders.clear()
            if hasattr(state, "pnl_history") and state.pnl_history:
                state.pnl_history.clear()

            # Record baseline initial point after reset
            if state.session_initial_value_usd is not None:
                now_ms = int(time.time() * 1000)
                state.pnl_history.append({
                    "ts": now_ms,
                    "val": round(state.session_initial_value_usd, 2),
                    "pnl_usd": 0.0,
                    "pnl_pct": 0.0
                })

            self.state_store.save_state(state)
            logger.info("Session stats and PNL reset via API.")
            self._send_json({"success": True, "message": "Session stats reset successfully"})
        except Exception as e:
            logger.error("Failed to reset stats via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_clear_orders(self) -> None:
        if not self.state_store:
            self._send_json({"error": "State store unavailable"}, status=503)
            return

        try:
            state = self.state_store.load_state()
            completed_count = len(state.completed_orders) if hasattr(state, "completed_orders") else 0
            pending_count = len(state.pending_orders) if hasattr(state, "pending_orders") else 0
            total_count = completed_count + pending_count

            if hasattr(state, "completed_orders"):
                state.completed_orders.clear()
            if hasattr(state, "pending_orders"):
                state.pending_orders.clear()

            self.state_store.save_state(state)
            logger.info("Cleared %d orders from state via API", total_count)
            self._send_json({
                "success": True,
                "message": f"Successfully cleared {total_count} orders from history",
                "cleared_count": total_count,
            })
        except Exception as e:
            logger.error("Failed to clear orders via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_clear_errors(self) -> None:
        try:
            if self.orchestrator:
                self.orchestrator.clear_critical_errors()
                logger.info("Cleared critical errors via orchestrator in memory and disk")
                self._send_json({"success": True, "message": "System errors cleared successfully"})
            elif self.state_store:
                state = self.state_store.load_state()
                state.critical_errors.clear()
                self.state_store.save_state(state)
                logger.info("Cleared critical errors from state store via API")
                self._send_json({"success": True, "message": "System errors cleared successfully"})
            else:
                self._send_json({"error": "State store unavailable"}, status=503)
        except Exception as e:
            logger.error("Failed to clear critical errors via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_get_updater_status(self) -> None:
        try:
            project_dir = Path(__file__).resolve().parent.parent.parent.parent
            pid_file = project_dir / "logs" / "updater.pid"
            is_running = False
            pid = None

            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    is_running = True
                except (ValueError, OSError):
                    is_running = False

            self._send_json({
                "active": is_running,
                "pid": pid if is_running else None,
                "status": "ACTIVE" if is_running else "STOPPED"
            })
        except Exception as e:
            logger.error("Failed to check updater status: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_toggle_updater(self) -> None:
        try:
            import subprocess
            project_dir = Path(__file__).resolve().parent.parent.parent.parent
            script_path = project_dir / "RUN" / "scripts" / "auto_updater.sh"
            pid_file = project_dir / "logs" / "updater.pid"

            is_running = False
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    is_running = True
                except (ValueError, OSError):
                    is_running = False

            if is_running:
                subprocess.run([str(script_path), "stop"], cwd=str(project_dir), capture_output=True)
                action_msg = "Git Auto-Updater paused"
            else:
                subprocess.run([str(script_path), "start"], cwd=str(project_dir), capture_output=True)
                action_msg = "Git Auto-Updater started"

            time.sleep(0.3)

            is_running_now = False
            pid_now = None
            if pid_file.exists():
                try:
                    pid_now = int(pid_file.read_text().strip())
                    os.kill(pid_now, 0)
                    is_running_now = True
                except (ValueError, OSError):
                    is_running_now = False

            logger.info("Auto-updater toggled via API: %s (Active: %s)", action_msg, is_running_now)
            self._send_json({
                "active": is_running_now,
                "pid": pid_now if is_running_now else None,
                "message": action_msg
            })
        except Exception as e:
            logger.error("Failed to toggle auto-updater: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_manual_pull(self) -> None:
        try:
            import subprocess
            project_dir = Path(__file__).resolve().parent.parent.parent.parent
            script_path = project_dir / "RUN" / "scripts" / "safe_pull.sh"
            if script_path.exists():
                cmd = [str(script_path), "main"]
            else:
                cmd = ["bash", "-c", "git stash push --include-untracked -m 'Auto-save before manual pull' && git pull origin main && (git stash pop || git reset --hard HEAD)"]
            
            res = subprocess.run(
                cmd,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout = res.stdout.strip() if res.stdout else ""
            stderr = res.stderr.strip() if res.stderr else ""
            output_msg = stdout or stderr

            updated = "Already up to date" not in output_msg and "Already up-to-date" not in output_msg
            
            logger.info("Manual Git Pull executed via API: %s (Output: %s)", "Success" if res.returncode == 0 else "Failed", output_msg)

            if res.returncode == 0:
                msg = "Updated code pulled successfully from GitHub!" if updated else "Code is already up to date."
                self._send_json({
                    "success": True,
                    "updated": updated,
                    "message": msg,
                    "output": output_msg
                })
            else:
                self._send_json({
                    "success": False,
                    "error": f"Git pull failed: {output_msg}"
                }, status=500)
        except Exception as e:
            logger.error("Failed to execute manual git pull: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _get_telegram_service(self) -> Any:
        if DashboardRequestHandler.telegram_service:
            return DashboardRequestHandler.telegram_service
        if self.orchestrator and hasattr(self.orchestrator, "_telegram_service") and self.orchestrator._telegram_service:
            return self.orchestrator._telegram_service
        if self.config and hasattr(self.config, "telegram"):
            from src.services.telegram_service import TelegramService
            tg_cfg = self.config.telegram
            svc = TelegramService(
                bot_token=tg_cfg.bot_token,
                chat_id=tg_cfg.chat_id,
                enabled=tg_cfg.enabled,
                dashboard_url=tg_cfg.dashboard_url,
            )
            DashboardRequestHandler.telegram_service = svc
            return svc
        return None

    def _handle_get_telegram(self) -> None:
        svc = self._get_telegram_service()
        token = svc.bot_token if svc else ""
        masked_token = ""
        if token:
            if len(token) > 8:
                masked_token = f"{token[:4]}...{token[-4:]}"
            else:
                masked_token = "****"

        data = {
            "enabled": svc.enabled if svc else False,
            "bot_token": token,
            "masked_token": masked_token,
            "chat_id": svc.chat_id if svc else "",
            "dashboard_url": svc.dashboard_url if svc else "",
            "is_configured": svc.is_configured() if svc else False,
        }
        self._send_json(data)

    def _handle_update_telegram(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))

            enabled = bool(data.get("enabled", False))
            bot_token = str(data.get("bot_token", "")).strip()
            chat_id = str(data.get("chat_id", "")).strip()
            dashboard_url = str(data.get("dashboard_url", "")).strip()

            svc = self._get_telegram_service()
            if not svc:
                from src.services.telegram_service import TelegramService
                svc = TelegramService(bot_token=bot_token, chat_id=chat_id, enabled=enabled, dashboard_url=dashboard_url)
                DashboardRequestHandler.telegram_service = svc
            else:
                svc.enabled = enabled
                svc.bot_token = bot_token
                svc.chat_id = chat_id
                svc.dashboard_url = dashboard_url

            if self.orchestrator:
                self.orchestrator._telegram_service = svc
                if hasattr(self.orchestrator, "_order_manager") and self.orchestrator._order_manager:
                    self.orchestrator._order_manager._telegram_service = svc

            try:
                from pathlib import Path
                from src.config.config_manager import ConfigManager
                cfg_path = "RUN/config.yaml" if Path("RUN/config.yaml").exists() else "config.yaml"
                cm = ConfigManager(cfg_path)
                cm.save_telegram_config(enabled=enabled, bot_token=bot_token, chat_id=chat_id, dashboard_url=dashboard_url)
            except Exception as ex:
                logger.warning("Could not save telegram config to config.yaml: %s", ex)

            if self.state_store:
                try:
                    st = self.state_store.load_state()
                    st.strategy_state["telegram"] = {
                        "enabled": enabled,
                        "bot_token": bot_token,
                        "chat_id": chat_id,
                        "dashboard_url": dashboard_url,
                    }
                    self.state_store.save_state(st)
                    logger.info("Persisted Telegram configuration to state_store")
                except Exception as ex:
                    logger.warning("Could not persist Telegram config into state_store: %s", ex)

            if self.config and hasattr(self.config, "telegram"):
                self.config.telegram.enabled = enabled
                self.config.telegram.bot_token = bot_token
                self.config.telegram.chat_id = chat_id
                self.config.telegram.dashboard_url = dashboard_url

            logger.info("Telegram configuration updated via API (Enabled: %s, Chat ID: %s)", enabled, chat_id)
            self._send_json({
                "success": True,
                "message": "Telegram settings saved successfully in configuration file!",
                "enabled": enabled,
                "is_configured": svc.is_configured(),
            })
        except Exception as e:
            logger.error("Failed to update telegram configuration via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _handle_test_telegram(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8")) if length > 0 else {}

            bot_token = str(data.get("bot_token", "")).strip()
            chat_id = str(data.get("chat_id", "")).strip()

            # Retrieve last completed trade from state if available
            last_trade = None
            if self.state_store:
                try:
                    st = self.state_store.load_state()
                    if st.completed_orders:
                        last_trade = st.completed_orders[-1]
                except Exception as ex:
                    logger.debug("Could not fetch last completed order for telegram test: %s", ex)

            run_mode_str = "DRY_RUN"
            if self.config and hasattr(self.config, "run_mode"):
                mode = self.config.run_mode
                run_mode_str = mode.name if hasattr(mode, "name") else str(mode)

            if bot_token and chat_id:
                from src.services.telegram_service import TelegramService
                dash_url = self.config.telegram.dashboard_url if (self.config and hasattr(self.config, "telegram")) else ""
                test_svc = TelegramService(bot_token=bot_token, chat_id=chat_id, enabled=True, dashboard_url=dash_url)
                success, msg = test_svc.send_test_notification(last_trade=last_trade, run_mode=run_mode_str)
            else:
                svc = self._get_telegram_service()
                if not svc or not svc.is_configured():
                    self._send_json({"success": False, "error": "Telegram is not configured. Please enter Bot Token and Chat ID."}, status=400)
                    return
                success, msg = svc.send_test_notification(last_trade=last_trade, run_mode=run_mode_str)

            if success:
                self._send_json({"success": True, "message": msg, "has_last_trade": bool(last_trade)})
            else:
                self._send_json({"success": False, "error": msg}, status=400)
        except Exception as e:
            logger.error("Failed to test Telegram notification via API: %s", e)
            self._send_json({"error": str(e)}, status=500)

    # ── Helpers ──────────────────────────────────────────────

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        try:
            content = json.dumps(data, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Client disconnected during response stream.")

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard HTTP logging noise in console unless debug
        logger.debug(format, *args)


def run_dashboard_server(
    config: Any,
    gateway: Any,
    state_store: Any,
    orchestrator: Any = None,
    telegram_service: Any = None,
    host: str = "0.0.0.0",
    port: int = 8080,
    max_retries: int = 15,
) -> ThreadedHTTPServer:
    """Initialize and start the dashboard HTTP server."""
    DashboardRequestHandler.config = config
    DashboardRequestHandler.gateway = gateway
    DashboardRequestHandler.state_store = state_store
    DashboardRequestHandler.orchestrator = orchestrator
    DashboardRequestHandler.telegram_service = telegram_service
    DashboardRequestHandler.log_file_path = config.logging.file if config else "logs/bot.log"

    for attempt in range(max_retries):
        try:
            server = ThreadedHTTPServer((host, port), DashboardRequestHandler)
            logger.info("Dashboard web server listening on http://%s:%d", host, port)
            return server
        except OSError as ex:
            if ex.errno == 98 and attempt < max_retries - 1:
                logger.warning(
                    "Port %d busy (Errno 98). Retrying bind in 1s... (%d/%d)",
                    port, attempt + 1, max_retries
                )
                time.sleep(1.0)
            else:
                raise
