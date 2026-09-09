"""
Tests for server log filtering and /api/logs/download endpoint.
"""

import io
import json
from unittest.mock import MagicMock
import pytest

from src.web.server import DashboardRequestHandler


class DummyHandler(DashboardRequestHandler):
    """Subclass of DashboardRequestHandler that avoids socket/network requirements."""

    def __init__(self, request_path="/api/logs"):
        self.path = request_path
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()
        self.headers = {}
        self.server = MagicMock()
        self.client_address = ("127.0.0.1", 12345)
        self.sent_headers = {}
        self.sent_status = None

    def send_response(self, status, message=None):
        self.sent_status = status

    def send_header(self, keyword, value):
        self.sent_headers[keyword.lower()] = value

    def end_headers(self):
        pass

    def _require_auth(self) -> bool:
        return True


def test_handle_logs_filters_idle_spam(tmp_path):
    log_file = tmp_path / "test_bot.log"
    log_file.write_text(
        "2026-09-09T21:00:00+00:00 | INFO | bot.orchestrator | CYCLE START | Mode: LIVE\n"
        "2026-09-09T21:00:01+00:00 | INFO | bot.services.reconciliation | Starting reconciliation...\n"
        "2026-09-09T21:00:02+00:00 | INFO | bot.services.reconciliation | Reconciliation complete — state is consistent\n"
        "2026-09-09T21:00:03+00:00 | INFO | bot.strategy | Generated signal: BUY BTC size=0.15\n"
        "2026-09-09T21:05:00+00:00 | DEBUG | bot.orchestrator | No new closed candles — cycle idle\n"
        "2026-09-09T21:05:01+00:00 | WARNING | bot.orchestrator | ⚠️ SCAN TIMING ALERT: Cycle ran after only 120.0s (< 5m)!\n",
        encoding="utf-8",
    )

    DummyHandler.log_file_path = str(log_file)
    handler = DummyHandler("/api/logs")
    handler._handle_logs()

    response_bytes = handler.wfile.getvalue()
    response = json.loads(response_bytes.decode("utf-8"))
    logs = response.get("logs", [])

    # Ensure routine reconciliation spam was filtered
    assert not any("Starting reconciliation..." in line for line in logs)
    assert not any("Reconciliation complete — state is consistent" in line for line in logs)

    # Ensure meaningful logs are preserved
    assert any("CYCLE START" in line for line in logs)
    assert any("Generated signal: BUY BTC" in line for line in logs)
    assert any("SCAN TIMING ALERT" in line for line in logs)


def test_handle_download_logs(tmp_path):
    log_file = tmp_path / "test_download.log"
    log_file.write_text(
        "2026-09-09T21:00:00+00:00 | INFO | bot.orchestrator | Starting trading engine\n"
        "2026-09-09T21:00:01+00:00 | INFO | bot.services.reconciliation | Starting reconciliation...\n"
        "2026-09-09T21:00:02+00:00 | INFO | bot.services.reconciliation | Reconciliation complete — state is consistent\n"
        "2026-09-09T21:00:03+00:00 | INFO | bot.services.order_manager | Executed LIMIT_BUY order #101\n",
        encoding="utf-8",
    )

    DummyHandler.log_file_path = str(log_file)
    handler = DummyHandler("/api/logs/download")
    handler._handle_download_logs()

    assert handler.sent_status == 200
    assert "text/plain" in handler.sent_headers.get("content-type", "")
    assert 'attachment; filename="bot_logs.log"' in handler.sent_headers.get("content-disposition", "")

    downloaded_content = handler.wfile.getvalue().decode("utf-8")
    # Routine spam excluded
    assert "Starting reconciliation..." not in downloaded_content
    assert "Reconciliation complete — state is consistent" not in downloaded_content
    # Useful logs included
    assert "Starting trading engine" in downloaded_content
    assert "Executed LIMIT_BUY order #101" in downloaded_content
