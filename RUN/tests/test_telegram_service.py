"""
Unit tests for TelegramService and Telegram configuration persistence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config.config_manager import ConfigManager, TelegramConfig
from src.services.telegram_service import TelegramService


class TestTelegramService(unittest.TestCase):
    def test_disabled_by_default(self):
        svc = TelegramService()
        self.assertFalse(svc.enabled)
        self.assertFalse(svc.is_configured())
        self.assertFalse(svc.send_message("Test message"))

    def test_send_trade_notification_when_disabled(self):
        svc = TelegramService(bot_token="12345:dummy_token", chat_id="987654", enabled=False)
        order_data = {
            "side": "BUY",
            "symbol": "BTC/USDT",
            "filled_amount": 0.05,
            "average_price": 60000.0,
            "fees": 3.0,
            "fee_currency": "USDT",
            "reason": "Test trade",
        }
        res = svc.send_trade_notification(order_data)
        self.assertFalse(res)

    @patch("urllib.request.urlopen")
    def test_send_trade_notification_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        svc = TelegramService(
            bot_token="12345:dummy_token",
            chat_id="987654",
            enabled=True,
            dashboard_url="http://my-trading-server.com:8080",
        )
        order_data = {
            "side": "BUY",
            "symbol": "BTC/USDT",
            "filled_amount": 0.1,
            "average_price": 65000.0,
            "fees": 6.5,
            "fee_currency": "USDT",
            "reason": "Bull regime entry",
        }
        res = svc.send_trade_notification(order_data, run_mode="DRY_RUN")
        self.assertTrue(res)
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_exception_suppression_guarantee(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Simulated network down / DNS failure")

        svc = TelegramService(
            bot_token="12345:dummy_token", chat_id="987654", enabled=True
        )
        # Should not raise exception
        res = svc.send_message("Test crash resilience")
        self.assertFalse(res)

        order_data = {"side": "SELL", "symbol": "ETH/USDT", "amount": 1.0, "price": 3000.0}
        res2 = svc.send_trade_notification(order_data)
        self.assertFalse(res2)

    @patch("urllib.request.urlopen")
    def test_send_test_notification_with_last_trade(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        svc = TelegramService(bot_token="12345:dummy_token", chat_id="987654", enabled=False)
        last_trade = {
            "side": "SELL",
            "symbol": "SOL/USDT",
            "filled_amount": 5.0,
            "average_price": 140.0,
            "fees": 0.7,
            "fee_currency": "USDT",
            "reason": "Micro profit lock",
        }
        success, msg = svc.send_test_notification(last_trade=last_trade, run_mode="DRY_RUN")
        self.assertTrue(success)
        self.assertIn("העסקה האחרונה", msg)

    @patch("urllib.request.urlopen")
    def test_send_test_notification_without_trades(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        svc = TelegramService(bot_token="12345:dummy_token", chat_id="987654", enabled=False)
        success, msg = svc.send_test_notification(last_trade=None, run_mode="DRY_RUN")
        self.assertTrue(success)
        self.assertIn("באנגלית", msg)

    def test_config_manager_persistence(self):
        yaml_content = """
run_mode: DRY_RUN
telegram:
  enabled: false
  bot_token: ""
  chat_id: ""
  dashboard_url: ""
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False) as tf:
            tf.write(yaml_content)
            tf_path = tf.name

        try:
            cm = ConfigManager(tf_path)
            cfg = cm.load()
            self.assertFalse(cfg.telegram.enabled)

            # Update telegram config
            cm.save_telegram_config(
                enabled=True,
                bot_token="test_token_123",
                chat_id="chat_999",
                dashboard_url="http://server:8090",
            )

            # Reload and verify persistence
            cm2 = ConfigManager(tf_path)
            cfg2 = cm2.load()
            self.assertTrue(cfg2.telegram.enabled)
            self.assertEqual(cfg2.telegram.bot_token, "test_token_123")
            self.assertEqual(cfg2.telegram.chat_id, "chat_999")
            self.assertEqual(cfg2.telegram.dashboard_url, "http://server:8090")

        finally:
            Path(tf_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
