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
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    # ── REST API Handlers ─────────────────────────────────────

    def _handle_status(self) -> None:
        state = self.state_store.load_state() if self.state_store else None
        data = {
            "run_mode": self.config.run_mode.name if self.config else "UNKNOWN",
            "last_regime": state.last_regime if state else "UNKNOWN",
            "last_run_ts": state.last_run_ts if state else None,
            "last_cycle_success": state.last_cycle_success if state else True,
            "critical_errors_count": len(state.critical_errors) if state else 0,
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

            data = {
                "total_value_usd": round(snapshot.total_value_usd, 2),
                "holdings": holdings_list,
                "timestamp_ms": snapshot.timestamp_ms,
            }
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
