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
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, Optional

logger = logging.getLogger("bot.web.server")

STATIC_DIR = Path(__file__).parent / "static"


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

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self._handle_status()
        elif self.path == "/api/portfolio":
            self._handle_portfolio()
        elif self.path == "/api/orders":
            self._handle_orders()
        elif self.path == "/api/logs":
            self._handle_logs()
        elif self.path == "/api/dry_run/balances":
            self._handle_get_dry_run_balances()
        else:
            # Fallback to serving static files (index.html, style.css, app.js)
            if self.path in ("/", ""):
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/trigger":
            self._handle_trigger_cycle()
        elif self.path == "/api/killswitch":
            self._handle_toggle_killswitch()
        elif self.path == "/api/dry_run/balances":
            self._handle_update_dry_run_balances()
        elif self.path == "/api/errors/clear":
            self._handle_clear_errors()
        elif self.path == "/api/reset_stats":
            self._handle_reset_stats()
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

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

            holdings_list = []
            for symbol, h in snapshot.holdings.items():
                weight = (h.value_usd / snapshot.total_value_usd * 100) if snapshot.total_value_usd > 0 else 0.0
                holdings_list.append({
                    "symbol": symbol,
                    "free": h.free,
                    "locked": h.locked,
                    "total": h.total,
                    "value_usd": h.value_usd,
                    "weight_pct": round(weight, 2),
                })

            state = self.state_store.load_state() if self.state_store else None
            initial_val = state.session_initial_value_usd if state else None

            data = {
                "total_value_usd": round(snapshot.total_value_usd, 2),
                "holdings": holdings_list,
                "timestamp_ms": snapshot.timestamp_ms,
                "session_initial_value_usd": round(initial_val, 2) if initial_val is not None else None,
                "session_fees": state.session_fees if state else {}
            }

            # Auto-initialize baseline if empty
            if state and initial_val is None:
                state.session_initial_value_usd = snapshot.total_value_usd
                self.state_store.save_state(state)
                data["session_initial_value_usd"] = round(snapshot.total_value_usd, 2)

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
            "completed": state.completed_orders[-50:],  # Last 50 completed
        }
        self._send_json(data)

    def _handle_logs(self) -> None:
        log_path = self.log_file_path or (self.config.logging.file if self.config else "logs/bot.log")
        lines = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                    lines = [line.strip() for line in all_lines[-100:]]  # Last 100 lines
            except Exception as e:
                lines = [f"Error reading log file: {e}"]
        else:
            lines = ["No log file generated yet."]

        self._send_json({"logs": lines})

    def _handle_trigger_cycle(self) -> None:
        if self.orchestrator:
            try:
                success = self.orchestrator.run_once()
                self._send_json({"success": success, "message": "Cycle completed"})
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

    # ── Helpers ──────────────────────────────────────────────

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        content = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard HTTP logging noise in console unless debug
        logger.debug(format, *args)


def run_dashboard_server(
    config: Any,
    gateway: Any,
    state_store: Any,
    orchestrator: Any = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> ThreadedHTTPServer:
    """Initialize and start the dashboard HTTP server."""
    DashboardRequestHandler.config = config
    DashboardRequestHandler.gateway = gateway
    DashboardRequestHandler.state_store = state_store
    DashboardRequestHandler.orchestrator = orchestrator
    DashboardRequestHandler.log_file_path = config.logging.file if config else "logs/bot.log"

    server = ThreadedHTTPServer((host, port), DashboardRequestHandler)
    logger.info("Dashboard web server listening on http://%s:%d", host, port)
    return server
