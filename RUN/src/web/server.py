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
import time
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


def _fetch_market_metrics_worker() -> None:
    global _market_metrics_cache, _market_metrics_updating
    try:
        import ccxt
        import numpy as np
        exchange = ccxt.binance({"timeout": 5000, "enableRateLimit": False})
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

        if clean_path == "/api/status":
            self._handle_status()
        elif clean_path == "/api/portfolio":
            self._handle_portfolio()
        elif clean_path == "/api/orders":
            self._handle_orders()
        elif clean_path == "/api/logs":
            self._handle_logs()
        elif clean_path == "/api/dry_run/balances":
            self._handle_get_dry_run_balances()
        elif clean_path == "/api/updater":
            self._handle_get_updater_status()
        elif clean_path == "/api/telegram":
            self._handle_get_telegram()
        else:
            # Fallback to serving static files (index.html, style.css, app.js)
            if clean_path in ("/", "", "/index", "/index.html"):
                self.path = "/index.html"
            else:
                self.path = clean_path
            super().do_GET()

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
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

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
        data = {
            "run_mode": self.config.run_mode.name if self.config else "UNKNOWN",
            "last_regime": state.last_regime if state else "UNKNOWN",
            "last_run_ts": state.last_run_ts if state else None,
            "last_cycle_success": state.last_cycle_success if state else True,
            "critical_errors_count": len(critical_errors),
            "latest_error": critical_errors[-1] if critical_errors else None,
            "critical_errors": critical_errors[-5:],
            "kill_switch": self.config.risk.kill_switch if self.config else False,
            "assets": list(self.config.strategy.assets.keys()) if self.config else [],
            "market_metrics": get_market_metrics_cached(),
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
            self._send_json({"error": str(e)}, status=500)

    def _handle_orders(self) -> None:
        state = self.state_store.load_state() if self.state_store else None
        if not state:
            self._send_json({"pending": [], "completed": []})
            return

        data = {
            "pending": state.pending_orders,
            "completed": state.completed_orders[-100:],  # Last 100 completed
        }
        self._send_json(data)

    def _handle_logs(self) -> None:
        log_path = self.log_file_path or (self.config.logging.file if self.config else "logs/bot.log")
        lines = []
        ignored_patterns = (
            "Loaded state:",
            "Portfolio snapshot:",
            "No state file found at",
            "No new closed candles",
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
                state.session_initial_value_usd = snapshot.total_value_usd
            else:
                state.session_initial_value_usd = None

            state.session_fees.clear()
            state.session_initial_prices.clear()
            state.completed_orders.clear()

            self.state_store.save_state(state)
            logger.info("Session stats and PNL reset via API.")
            self._send_json({"success": True, "message": "Session stats reset successfully"})
        except Exception as e:
            logger.error("Failed to reset stats via API: %s", e)
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
            res = subprocess.run(
                ["git", "pull", "origin", "main", "--autostash"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode != 0:
                res = subprocess.run(
                    ["git", "pull", "origin", "main"],
                    cwd=str(project_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            stdout = res.stdout.strip() if res.stdout else ""
            stderr = res.stderr.strip() if res.stderr else ""
            output_msg = stdout or stderr

            updated = "Already up to date" not in stdout and "Already up-to-date" not in stdout
            
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
) -> ThreadedHTTPServer:
    """Initialize and start the dashboard HTTP server."""
    DashboardRequestHandler.config = config
    DashboardRequestHandler.gateway = gateway
    DashboardRequestHandler.state_store = state_store
    DashboardRequestHandler.orchestrator = orchestrator
    DashboardRequestHandler.telegram_service = telegram_service
    DashboardRequestHandler.log_file_path = config.logging.file if config else "logs/bot.log"

    server = ThreadedHTTPServer((host, port), DashboardRequestHandler)
    logger.info("Dashboard web server listening on http://%s:%d", host, port)
    return server
