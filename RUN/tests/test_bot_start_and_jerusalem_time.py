"""
Unit tests for bot launch date tracking and Jerusalem timezone formatting.
"""

from datetime import datetime, timezone
import io
import json
from unittest.mock import MagicMock
import zoneinfo
import pytest

from src.core.models import BotState
from src.web.server import DashboardRequestHandler


class DummyStatusHandler(DashboardRequestHandler):
    def __init__(self, state=None, orchestrator=None):
        self.path = "/api/status"
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()
        self.headers = {}
        self.server = MagicMock()
        self.client_address = ("127.0.0.1", 12345)
        self.sent_headers = {}
        self.sent_status = None
        self._custom_state = state
        self.orchestrator = orchestrator
        self.config = MagicMock()
        self.config.run_mode.name = "DRY_RUN"
        self.config.risk.kill_switch = False
        self.config.strategy.assets = {}

    def _get_active_state(self):
        return self._custom_state

    def send_response(self, status, message=None):
        self.sent_status = status

    def send_header(self, keyword, value):
        self.sent_headers[keyword.lower()] = value

    def end_headers(self):
        pass

    def _require_auth(self) -> bool:
        return True


def test_bot_state_bot_start_ts_serialization():
    """Verify bot_start_ts roundtrips through to_dict and from_dict."""
    ts = 1789331523265
    state = BotState(bot_start_ts=ts, last_run_ts=ts)
    data = state.to_dict()
    assert data["bot_start_ts"] == ts
    assert data["last_run_ts"] == ts

    restored = BotState.from_dict(data)
    assert restored.bot_start_ts == ts
    assert restored.last_run_ts == ts


def test_server_status_returns_bot_start_ts():
    """Verify /api/status endpoint includes bot_start_ts in JSON response."""
    ts = 1789331523265
    state = BotState(bot_start_ts=ts, last_run_ts=ts)
    handler = DummyStatusHandler(state=state)
    handler._handle_status()

    output = handler.wfile.getvalue().decode("utf-8")
    parsed = json.loads(output)
    assert parsed["bot_start_ts"] == ts
    assert parsed["last_run_ts"] == ts


def test_jerusalem_and_utc_datetime_formatting():
    """Verify conversion to DD/MM/YYYY HH:MM for both UTC and Asia/Jerusalem."""
    # 1789331523265 ms = 2026-09-13 20:32:03 UTC -> 2026-09-13 23:32:03 Asia/Jerusalem (IDT: UTC+3)
    ts_sec = 1789331523.265
    dt_utc = datetime.fromtimestamp(ts_sec, timezone.utc)
    jer_tz = zoneinfo.ZoneInfo("Asia/Jerusalem")
    dt_jer = dt_utc.astimezone(jer_tz)

    utc_str = dt_utc.strftime("%d/%m/%Y %H:%M")
    jer_str = dt_jer.strftime("%d/%m/%Y %H:%M")

    assert utc_str == "13/09/2026 20:32"
    assert jer_str == "13/09/2026 23:32"
