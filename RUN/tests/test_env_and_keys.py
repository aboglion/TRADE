"""
Tests for .env persistence and API keys management endpoints.
"""

import json
import os
from io import BytesIO
from pathlib import Path

from src.utils.env_manager import update_env_file
from src.web.server import DashboardRequestHandler


class MockServerHandler(DashboardRequestHandler):
    def __init__(self, path: str, method: str = "GET", body: dict = None):
        self.path = path
        self.command = method
        self.headers = {"Content-Length": str(len(json.dumps(body))) if body else "0"}
        self.rfile = BytesIO(json.dumps(body).encode("utf-8") if body else b"")
        self.wfile = BytesIO()
        self.sent_status = 200
        self.sent_headers = {}

    def send_response(self, code, message=None):
        self.sent_status = code

    def send_header(self, keyword, value):
        self.sent_headers[keyword] = value

    def end_headers(self):
        pass

    def _require_auth(self) -> bool:
        return True


def test_env_manager_roundtrip(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text('BINANCE_API_KEY="KEY123"\nBINANCE_API_SECRET="SEC123"\n', encoding="utf-8")

    # Update with telegram
    res = update_env_file({
        "TELEGRAM_BOT_TOKEN": "111:TOKEN",
        "TELEGRAM_CHAT_ID": "222",
        "TELEGRAM_ENABLED": True,
    }, path=env_file)
    assert res is True

    content = env_file.read_text(encoding="utf-8")
    assert 'BINANCE_API_KEY="KEY123"' in content
    assert 'BINANCE_API_SECRET="SEC123"' in content
    assert 'TELEGRAM_BOT_TOKEN="111:TOKEN"' in content
    assert 'TELEGRAM_ENABLED="true"' in content

    # In-place update
    res2 = update_env_file({"TELEGRAM_ENABLED": False}, path=env_file)
    assert res2 is True
    content2 = env_file.read_text(encoding="utf-8")
    assert 'TELEGRAM_ENABLED="false"' in content2
    assert 'BINANCE_API_KEY="KEY123"' in content2


def test_server_get_keys():
    os.environ["BINANCE_API_KEY"] = "MOCK_KEY_12345678"
    os.environ["BINANCE_API_SECRET"] = "MOCK_SECRET_XYZ"
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:BOT_TOKEN_ABC"
    os.environ["TELEGRAM_CHAT_ID"] = "987654321"
    os.environ["TELEGRAM_ENABLED"] = "true"

    handler = MockServerHandler("/api/keys", method="GET")
    handler._handle_get_keys()

    assert handler.sent_status == 200
    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert data["binance"]["configured"] is True
    assert "MOCK..." in data["binance"]["masked_key"] or "MOCK" in data["binance"]["masked_key"]
    assert data["telegram"]["configured"] is True
    assert data["telegram"]["enabled"] is True


def test_server_update_keys(tmp_path: Path):
    test_env = tmp_path / ".env"
    test_env.write_text('BINANCE_API_KEY="OLD_KEY"\n', encoding="utf-8")

    # Patch find_env_file temporarily to point to test_env
    from unittest.mock import patch
    with patch("src.utils.env_manager.find_env_file", return_value=test_env):
        handler = MockServerHandler("/api/keys", method="POST", body={
            "binance_api_key": "NEW_KEY_9999",
            "binance_api_secret": "NEW_SECRET_8888",
            "confirm_live": True,
        })
        handler._handle_update_keys()

        assert handler.sent_status == 200
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["success"] is True

        content = test_env.read_text(encoding="utf-8")
        assert 'BINANCE_API_KEY="NEW_KEY_9999"' in content
        assert 'BINANCE_API_SECRET="NEW_SECRET_8888"' in content
        assert 'CONFIRM_LIVE="YES_I_UNDERSTAND"' in content


def test_server_update_telegram_to_env(tmp_path: Path):
    test_env = tmp_path / ".env"
    test_env.write_text('BINANCE_API_KEY="STILL_HERE"\n', encoding="utf-8")

    from unittest.mock import patch
    with patch("src.utils.env_manager.find_env_file", return_value=test_env), \
         patch("src.config.config_manager.ConfigManager"):
        handler = MockServerHandler("/api/telegram", method="POST", body={
            "enabled": True,
            "bot_token": "55555:TG_TOKEN",
            "chat_id": "77777",
            "dashboard_url": "http://my-host:8090",
        })
        handler._handle_update_telegram()

        assert handler.sent_status == 200
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["success"] is True
        assert data.get("saved_to_env") is True

        content = test_env.read_text(encoding="utf-8")
        assert 'BINANCE_API_KEY="STILL_HERE"' in content
        assert 'TELEGRAM_BOT_TOKEN="55555:TG_TOKEN"' in content
        assert 'TELEGRAM_CHAT_ID="77777"' in content
        assert 'TELEGRAM_ENABLED="true"' in content
