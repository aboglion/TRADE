"""
Unit tests for TaxHistoryService — Comprehensive Trade & Tax History System.
"""

import unittest
import time
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.services.tax_history_service import TaxHistoryService, get_tax_history_service


class TestTaxHistoryService(unittest.TestCase):

    def setUp(self):
        self.service = TaxHistoryService()

    def test_resolve_timeframe_all(self):
        now_ms = 1750000000000
        since, until = self.service._resolve_timeframe("all", now_ms)
        self.assertEqual(since, 0)
        self.assertEqual(until, now_ms)

    def test_resolve_timeframe_1y(self):
        now_ms = 1750000000000
        since, until = self.service._resolve_timeframe("1y", now_ms)
        expected_since = now_ms - (365 * 24 * 3600 * 1000)
        self.assertEqual(since, expected_since)
        self.assertEqual(until, now_ms)

    def test_resolve_timeframe_calendar_year(self):
        now_ms = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        since, until = self.service._resolve_timeframe("2025", now_ms)
        dt_start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(since, int(dt_start.timestamp() * 1000))

    def test_resolve_timeframe_custom(self):
        custom_s = 1700000000000
        custom_e = 1710000000000
        since, until = self.service._resolve_timeframe("custom", 1720000000000, custom_s, custom_e)
        self.assertEqual(since, custom_s)
        self.assertEqual(until, custom_e)

    def test_calculate_tax_summary(self):
        sample_trades = [
            {
                "symbol": "BTC/USDT",
                "coin": "BTC",
                "side": "BUY",
                "total_usd": 10000.0,
                "fee_usd": 10.0,
                "realized_pnl_usd": 0.0,
            },
            {
                "symbol": "BTC/USDT",
                "coin": "BTC",
                "side": "SELL",
                "total_usd": 12000.0,
                "fee_usd": 12.0,
                "realized_pnl_usd": 2000.0,
            },
            {
                "symbol": "ETH/USDT",
                "coin": "ETH",
                "side": "BUY",
                "total_usd": 5000.0,
                "fee_usd": 5.0,
                "realized_pnl_usd": 0.0,
            },
            {
                "symbol": "BTC/USDT",
                "coin": "BTC",
                "side": "FUNDING",
                "total_usd": 2.5,
                "fee_usd": 0.0,
                "realized_pnl_usd": -2.5,
            },
        ]

        summary = self.service.calculate_tax_summary(sample_trades)
        self.assertEqual(summary["total_operations"], 4)
        self.assertEqual(summary["total_buy_orders"], 2)
        self.assertEqual(summary["total_sell_orders"], 1)
        self.assertEqual(summary["total_volume_usd"], 27002.5)
        self.assertEqual(summary["total_buy_volume_usd"], 15000.0)
        self.assertEqual(summary["total_sell_volume_usd"], 12000.0)
        self.assertEqual(summary["total_fees_usd"], 27.0)
        self.assertEqual(summary["total_realized_pnl_usd"], 1997.5)
        self.assertIn("BTC", summary["per_coin_summary"])
        self.assertIn("ETH", summary["per_coin_summary"])
        self.assertEqual(summary["per_coin_summary"]["BTC"]["buy_count"], 1)
        self.assertEqual(summary["per_coin_summary"]["BTC"]["sell_count"], 1)

    def test_filter_trades(self):
        sample_trades = [
            {"symbol": "BTC/USDT", "coin": "BTC", "side": "BUY", "action_type": "BUY"},
            {"symbol": "ETH/USDT", "coin": "ETH", "side": "SELL", "action_type": "SELL"},
            {"symbol": "SOL/USDT", "coin": "SOL", "side": "BUY", "action_type": "BUY"},
        ]

        # Filter by coin
        btc_only = self.service._filter_trades(sample_trades, "BTC", "ALL")
        self.assertEqual(len(btc_only), 1)
        self.assertEqual(btc_only[0]["coin"], "BTC")

        # Filter by side
        buys_only = self.service._filter_trades(sample_trades, "ALL", "BUY")
        self.assertEqual(len(buys_only), 2)

        # Filter by both
        eth_sell = self.service._filter_trades(sample_trades, "ETH", "SELL")
        self.assertEqual(len(eth_sell), 1)

    def test_generate_tax_csv(self):
        sample_trades = [
            {
                "datetime_utc": "2026-09-13 10:00:00",
                "datetime_local": "2026-09-13 13:00:00",
                "coin": "BTC",
                "symbol": "BTC/USDT",
                "side": "BUY",
                "market": "FUTURES_USDT_M",
                "amount": 0.1,
                "price": 60000.0,
                "total_usd": 6000.0,
                "fee_usd": 6.0,
                "realized_pnl_usd": 0.0,
                "id": "trade_12345",
                "source": "BINANCE_FUTURES",
                "status": "FILLED",
                "notes": "Test trade",
            }
        ]
        summary = self.service.calculate_tax_summary(sample_trades)
        csv_text = self.service.generate_tax_csv("all", sample_trades, summary)

        self.assertIn("Trading & Capital Gains Tax Report", csv_text)
        self.assertIn("BTC/USDT", csv_text)
        self.assertIn("trade_12345", csv_text)
        self.assertIn("6000.00", csv_text)

    def test_fallback_to_bot_state_when_binance_fails(self):
        mock_state = MagicMock()
        mock_state.completed_orders = [
            {
                "client_order_id": "bot_test_order_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.05,
                "price": 80000.0,
                "fees": 2.0,
                "timestamp": int(time.time() * 1000),
                "status": "filled",
            }
        ]
        mock_store = MagicMock()
        mock_store.load_state.return_value = mock_state

        svc = TaxHistoryService(state_store=mock_store)

        with patch.object(svc, "_get_binance_credentials", return_value=("invalid_k", "invalid_s")):
            with patch("ccxt.binance", side_effect=Exception('{"code":-2015,"msg":"Invalid API-key"}')):
                res = svc.fetch_all_history(timeframe="all", force_refresh=True)
                self.assertFalse(res["binance_status"]["connected"])
                self.assertTrue(res["binance_status"]["is_auth_or_ip_error"])
                self.assertGreaterEqual(res["total_count"], 1)
                self.assertEqual(res["trades"][0]["client_order_id"], "bot_test_order_1")


if __name__ == "__main__":
    unittest.main()
