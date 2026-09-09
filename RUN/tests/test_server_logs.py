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


def test_handle_strategy_conditions_crash_shield():
    handler = DummyHandler("/api/strategy/conditions")
    handler.gateway = MagicMock()
    handler.config = MagicMock()
    handler.config.strategy.momentum_cutoff_pct = -0.02
    handler.config.strategy.conviction_leverage = 10.0
    handler.config.strategy.flash_wick_limit = -0.038
    handler.config.strategy.mid_leverage = 5.0
    handler.config.strategy.base_leverage = 2.5
    handler.config.strategy.ladder_steps = [1.0, 2.0, 4.0, 10.0]
    handler.state_store = MagicMock()
    handler.state_store.load_state.return_value = MagicMock(strategy_state={})

    # Mock daily BTC data: 200 candles where latest candle is down 3%
    candles_1d = []
    base_ts = 1600000000000
    for i in range(200):
        px = 60000.0 + i * 50.0
        candles_1d.append([base_ts + i * 86400000, px, px + 500, px - 500, px, 100.0])
    last_px = candles_1d[-1][4]
    candles_1d[-1][1] = last_px
    candles_1d[-1][4] = last_px * 0.97  # -3% drop from peak

    handler._fetch_ohlcv_safe = MagicMock(return_value=candles_1d)
    handler._handle_strategy_conditions()

    response_bytes = handler.wfile.getvalue()
    data = json.loads(response_bytes.decode("utf-8"))
    assert "macro_regime" in data
    macro = data["macro_regime"]
    assert "in_momentum" in macro
    assert "safe_haven_active" in macro
    assert "conviction_rocket_active" in macro
    assert "dist_from_5d_high_pct" in macro
    assert macro["safe_haven_active"] is True
    assert macro["effective_leverage"] == 1.0
    assert macro["total_crypto_weight_pct"] == 30.0
    assert macro["cash_weight_pct"] == 60.0


def test_handle_clear_logs(tmp_path):
    log_file = tmp_path / "test_clear.log"
    log_file.write_text(
        "2026-09-09T21:00:00+00:00 | INFO | bot.orchestrator | Old log line 1\n"
        "2026-09-09T21:00:01+00:00 | INFO | bot.orchestrator | Old log line 2\n",
        encoding="utf-8",
    )

    DummyHandler.log_file_path = str(log_file)
    handler = DummyHandler("/api/logs/clear")
    handler._handle_clear_logs()

    assert handler.sent_status == 200
    response_bytes = handler.wfile.getvalue()
    data = json.loads(response_bytes.decode("utf-8"))
    assert data.get("success") is True

    # Check file content has been cleared and contains reset notification
    content = log_file.read_text(encoding="utf-8")
    assert "Old log line 1" not in content
    assert "System logs cleared by user" in content

    # Verify fetching logs after clear returns the reset line
    get_handler = DummyHandler("/api/logs")
    get_handler._handle_logs()
    get_data = json.loads(get_handler.wfile.getvalue().decode("utf-8"))
    assert len(get_data.get("logs", [])) == 1
    assert "System logs cleared by user" in get_data["logs"][0]


def test_do_post_clear_logs(tmp_path):
    log_file = tmp_path / "test_post_clear.log"
    log_file.write_text("Old log before post\n", encoding="utf-8")

    DummyHandler.log_file_path = str(log_file)
    handler = DummyHandler("/api/logs/clear")
    handler.do_POST()

    assert handler.sent_status == 200
    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert data.get("success") is True
    assert "Old log before post" not in log_file.read_text(encoding="utf-8")

