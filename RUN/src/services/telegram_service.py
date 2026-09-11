"""
Telegram notification service.

Dispatches HTML-formatted alerts for completed trades and system events.
All calls are strictly wrapped in try-except blocks to guarantee zero impact
on trading logic or process stability if Telegram API requests fail.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("bot.services.telegram")


class TelegramService:
    """
    Robust Telegram Notification Service.

    Supports sending raw messages, trade fill notifications, and testing credentials.
    Guarantees non-blocking, exception-safe behavior.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = False,
        dashboard_url: str = "",
        timeout_seconds: float = 8.0,
    ):
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self.enabled = enabled
        self.dashboard_url = dashboard_url.strip()
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        """Returns True if bot_token and chat_id are present."""
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        """
        Send a raw HTML message to Telegram.

        Returns True if successful, False on failure or when disabled.
        Never raises an exception.
        """
        if not self.enabled or not self.is_configured():
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status == 200:
                    logger.info("Telegram notification sent successfully to chat %s", self.chat_id)
                    return True
                else:
                    logger.warning("Telegram API responded with status %s", resp.status)
                    return False

        except Exception as e:
            logger.warning("Failed to send Telegram message: %s", e)
            return False

    def send_trade_notification(
        self,
        order_data: Dict[str, Any],
        run_mode: str = "DRY_RUN",
        dashboard_url: Optional[str] = None,
        is_test: bool = False,
    ) -> bool:
        """
        Format and dispatch a trade execution alert.

        Guaranteed to catch all exceptions and never crash the trade runner.
        If is_test is True, sends test header even if global enabled switch is off.
        """
        if not is_test and not self.enabled:
            return False
        if not self.is_configured():
            return False

        try:
            side_raw = order_data.get("side", "BUY")
            if hasattr(side_raw, "value"):
                side_str = str(side_raw.value).upper()
            else:
                side_str = str(side_raw).upper()

            symbol = str(order_data.get("symbol", "N/A"))
            amount = float(order_data.get("filled_amount") or order_data.get("amount") or 0.0)
            price = float(order_data.get("average_price") or order_data.get("price") or order_data.get("estimated_price") or 0.0)
            fees = float(order_data.get("fees") or 0.0)
            fee_curr = str(order_data.get("fee_currency", "") or "").strip()
            reason = str(order_data.get("reason", "Strategy rebalance") or "Strategy rebalance").strip()

            total_usd = amount * price
            price_str = f"${price:,.4f}" if (0 < price < 10) else f"${price:,.2f}"
            side_is_buy = "BUY" in side_str
            side_emoji = "🟢" if side_is_buy else "🔴"
            action_text = "BUY" if side_is_buy else "SELL"

            url_to_link = dashboard_url or self.dashboard_url or "http://localhost:8090"
            if url_to_link and not url_to_link.startswith("http"):
                url_to_link = f"http://{url_to_link}"

            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            header = "🧪 <b>Connection Test — Sample Order (Last Executed Trade):</b>" if is_test else "⚡ <b>Trade Alert — Order Executed!</b>"

            msg = (
                f"{header}\n\n"
                f"{side_emoji} <b>Order Type:</b> {action_text}\n"
                f"🪙 <b>Asset:</b> <code>{symbol}</code>\n"
                f"📊 <b>Amount:</b> <code>{amount:.6f}</code>\n"
                f"💵 <b>Execution Price:</b> <code>{price_str}</code>\n"
                f"💰 <b>Total Value:</b> <code>${total_usd:,.2f}</code>\n"
                f"🏷️ <b>Fee:</b> <code>{fees:.6f} {fee_curr}</code>\n"
                f"🎯 <b>Reason/Strategy:</b> {reason}\n"
                f"⚙️ <b>Engine Mode:</b> <code>{run_mode}</code>\n"
                f"⏱️ <b>Time:</b> {time_str}\n\n"
                f"🌐 <b><a href=\"{url_to_link}\">Click here to open live dashboard</a></b>"
            )

            if is_test:
                # Direct send for test mode (bypassing self.enabled check in send_message)
                return self._send_raw_html(msg)
            return self.send_message(msg)

        except Exception as e:
            logger.warning("Error formatting or sending Telegram trade notification: %s", e)
            return False

    def _send_raw_html(self, text: str) -> bool:
        """Internal helper to send raw HTML without checking self.enabled (used for testing)."""
        if not self.is_configured():
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning("Failed to send raw Telegram message: %s", e)
            return False

    def send_test_notification(
        self,
        last_trade: Optional[Dict[str, Any]] = None,
        run_mode: str = "DRY_RUN",
    ) -> Tuple[bool, str]:
        """
        Send a test message to verify Telegram Bot Token and Chat ID.

        If last_trade is provided, sends a formatted test notification using the last trade.
        If no trades exist, sends an English test message.
        """
        if not self.is_configured():
            return False, "Please configure Bot Token and Chat ID in system settings"

        try:
            if last_trade:
                success = self.send_trade_notification(last_trade, run_mode=run_mode, is_test=True)
                if success:
                    return True, "Test message with last trade sent successfully to Telegram!"
                else:
                    return False, "Failed to send test message with last trade"
            else:
                url_to_link = self.dashboard_url or "http://localhost:8090"
                if url_to_link and not url_to_link.startswith("http"):
                    url_to_link = f"http://{url_to_link}"

                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                msg = (
                    f"🧪 <b>TEST / SYSTEM CHECK</b>\n\n"
                    f"<b>Status:</b> Telegram alert pipeline is active and working properly.\n"
                    f"<b>Notice:</b> No recorded trades executed yet in system state.\n"
                    f"<b>Engine Mode:</b> <code>{run_mode}</code>\n"
                    f"<b>Timestamp:</b> {time_str}\n\n"
                    f"🌐 <b><a href=\"{url_to_link}\">Click here to open live dashboard</a></b>"
                )
                success = self._send_raw_html(msg)
                if success:
                    return True, "Test message sent successfully to Telegram!"
                else:
                    return False, "Failed to send test message to Telegram"

        except Exception as e:
            return False, f"Error sending test message: {e}"
