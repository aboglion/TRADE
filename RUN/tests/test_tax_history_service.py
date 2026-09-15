"""
Unit tests for TaxHistoryService — Comprehensive Trade & Tax History System.
"""

import unittest
import time
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.services.tax_history_service import (
    TaxHistoryService,
    get_tax_history_service,
    _BINANCE_INCEPTION_MS,
    _TRANSFER_WINDOW_MS,
    _MAX_TRANSFER_CHUNKS,
    _P2P_INCEPTION_MS,
)


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

    def test_fifo_cost_basis_spot_trades(self):
        trades = [
            {
                "id": "buy1",
                "timestamp_ms": 1000,
                "coin": "BTC",
                "symbol": "BTC/USDT",
                "side": "BUY",
                "market": "SPOT",
                "amount": 0.0032,
                "price": 30000.0,
                "total_usd": 96.0,
                "fee_usd": 0.096,
            },
            {
                "id": "buy2",
                "timestamp_ms": 2000,
                "coin": "BTC",
                "symbol": "BTC/USDT",
                "side": "BUY",
                "market": "SPOT",
                "amount": 0.0032,
                "price": 40000.0,
                "total_usd": 128.0,
                "fee_usd": 0.128,
            },
            {
                "id": "sell1",
                "timestamp_ms": 3000,
                "coin": "BTC",
                "symbol": "BTC/USDT",
                "side": "SELL",
                "market": "SPOT",
                "amount": 0.0032,
                "price": 50000.0,
                "total_usd": 160.0,
                "fee_usd": 0.160,
            },
        ]

        processed = self.service._apply_fifo_cost_basis(trades)
        # Sells should match buy1 lot (FIFO)
        sell_trade = next(t for t in processed if t["id"] == "sell1")
        self.assertEqual(sell_trade["cost_basis_usd"], 96.0)
        # Net Realized PnL = Proceeds (160.0) - Cost Basis (96.0) - Sell Fee (0.16) - Buy Fee (0.096)
        expected_pnl = round(160.0 - 96.0 - 0.160 - 0.096, 4)
        self.assertAlmostEqual(sell_trade["realized_pnl_usd"], expected_pnl, places=3)
        self.assertGreater(sell_trade["realized_pnl_pct"], 60.0)

    def test_deposits_withdrawals_tracking(self):
        trades = [
            {
                "id": "dep1",
                "timestamp_ms": 1000,
                "coin": "USDT",
                "side": "DEPOSIT",
                "action_type": "DEPOSIT",
                "total_usd": 500.0,
                "amount": 500.0,
                "fee_usd": 0.0,
            },
            {
                "id": "wd1",
                "timestamp_ms": 2000,
                "coin": "USDT",
                "side": "WITHDRAW",
                "action_type": "WITHDRAW",
                "total_usd": 200.0,
                "amount": 200.0,
                "fee_usd": 1.0,
            },
        ]

        summary = self.service.calculate_tax_summary(trades)
        self.assertEqual(summary["total_deposits_count"], 1)
        self.assertEqual(summary["total_deposits_usd"], 500.0)
        self.assertEqual(summary["total_withdrawals_count"], 1)
        self.assertEqual(summary["total_withdrawals_usd"], 200.0)

    def test_manual_deposit_lifecycle_and_fifo(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "manual_deposits.json"
            with patch.object(self.service, "_get_manual_deposits_file", return_value=test_file):
                # 1. Add manual deposit
                dep = self.service.add_manual_deposit({
                    "coin": "BTC",
                    "amount": 0.05,
                    "price": 20000.0,
                    "notes": "ארנק חומרה",
                })
                self.assertEqual(dep["coin"], "BTC")
                self.assertEqual(dep["amount"], 0.05)
                self.assertEqual(dep["cost_basis_usd"], 1000.0)

                # 2. Verify retrieval
                items = self.service.get_manual_deposits()
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], dep["id"])

                # 3. Test FIFO matching with this deposit
                sell_trade = {
                    "id": "sell_btc",
                    "timestamp_ms": int(time.time() * 1000) + 10000,
                    "coin": "BTC",
                    "symbol": "BTC/USDT",
                    "side": "SELL",
                    "market": "SPOT",
                    "amount": 0.05,
                    "price": 30000.0,
                    "total_usd": 1500.0,
                    "fee_usd": 1.5,
                }
                fifo_trades = self.service._apply_fifo_cost_basis([dep, sell_trade])
                sold = next(t for t in fifo_trades if t["id"] == "sell_btc")
                # Cost basis should be $1000 from the manual deposit!
                self.assertEqual(sold["cost_basis_usd"], 1000.0)
                # Realized gain = 1500 - 1000 - 1.5 = 498.5
                self.assertAlmostEqual(sold["realized_pnl_usd"], 498.5, places=2)

                # 4. Delete deposit
                res = self.service.delete_manual_deposit(dep["id"])
                self.assertTrue(res)
                self.assertEqual(len(self.service.get_manual_deposits()), 0)


    # ------------------------------------------------------------------
    # Full-history deposit lookback, fiat/P2P parsing, error transparency
    # ------------------------------------------------------------------

    def test_build_transfer_chunks_all_time_covers_inception(self):
        now_ms = int(datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
        chunks = TaxHistoryService._build_transfer_chunks(_BINANCE_INCEPTION_MS, now_ms, _MAX_TRANSFER_CHUNKS)
        self.assertLessEqual(len(chunks), _MAX_TRANSFER_CHUNKS)
        # Oldest chunk reaches exchange inception; newest chunk reaches "now"
        self.assertEqual(min(c[0] for c in chunks), _BINANCE_INCEPTION_MS)
        self.assertEqual(max(c[1] for c in chunks), now_ms)
        # No gaps between consecutive chunks (full coverage)
        ordered = sorted(chunks)
        for i in range(1, len(ordered)):
            self.assertLessEqual(ordered[i][0], ordered[i - 1][1] + 1)
        # Every chunk respects the 85-day SAPI window
        for cs, ce in chunks:
            self.assertLessEqual(ce - cs, _TRANSFER_WINDOW_MS)

    def test_build_transfer_chunks_calendar_year_covers_january(self):
        since = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        until = int(datetime(2023, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
        span_chunks = (until - since) // _TRANSFER_WINDOW_MS + 2
        chunks = TaxHistoryService._build_transfer_chunks(since, until, span_chunks)
        # A full calendar year needs >4 chunks (old MAX_CHUNKS=4 cap missed January)
        self.assertGreater(len(chunks), 4)
        self.assertEqual(min(c[0] for c in chunks), since)

    def test_parse_fiat_order_handles_trade_success_and_ammout_typo(self):
        rec = TaxHistoryService._parse_fiat_order({
            "orderNo": "F123",
            "status": "Trade Success",
            "ammout": "5000.00",
            "fiatCurrency": "USD",
            "commission": "10.0",
            "money": "0.16",
            "price": "31250",
            "source": "Mars",
            "createTime": 1687000000000,
        }, "fiat_0")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["side"], "DEPOSIT")
        self.assertEqual(rec["action_type"], "FIAT_DEPOSIT")
        self.assertEqual(rec["total_usd"], 5000.0)
        self.assertEqual(rec["cost_basis_usd"], 5000.0)
        self.assertEqual(rec["fee_usd"], 10.0)
        self.assertIn("0.16", rec["notes"])

    def test_parse_fiat_order_rejects_non_success(self):
        for st in ("Processing", "Failure", "Order Expired", "Refunded", ""):
            rec = TaxHistoryService._parse_fiat_order(
                {"orderNo": "X", "status": st, "amount": "100", "createTime": 1687000000000},
                "fiat_0",
            )
            self.assertIsNone(rec, f"status '{st}' should not parse as a completed fiat order")

    def test_parse_fiat_order_withdraw_side(self):
        rec = TaxHistoryService._parse_fiat_order({
            "orderNo": "W9",
            "status": "SUCCESSFUL",
            "amount": "250.0",
            "fiatCurrency": "ILS",
            "createTime": 1687000000000,
        }, "fiat_1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["side"], "WITHDRAW")
        self.assertEqual(rec["cost_basis_usd"], 0.0)
        self.assertEqual(rec["coin"], "ILS")

    def test_parse_p2p_order_buy_and_rejections(self):
        buy = TaxHistoryService._parse_p2p_order({
            "orderStatus": "COMPLETED",
            "tradeType": "BUY",
            "asset": "BTC",
            "amount": "0.00319",
            "fiatAmount": "95.7",
            "fiatCurrency": "USD",
            "orderFinishTime": 1687000000000,
            "orderNumber": "P2P1",
            "payType": "BankTransfer",
        })
        self.assertIsNotNone(buy)
        self.assertEqual(buy["side"], "BUY")
        self.assertEqual(buy["market"], "P2P")
        self.assertEqual(buy["coin"], "BTC")
        self.assertEqual(buy["cost_basis_usd"], 95.7)
        self.assertAlmostEqual(buy["price"], 30000.0, places=2)

        # Non-completed / invalid orders are rejected
        self.assertIsNone(TaxHistoryService._parse_p2p_order({
            "orderStatus": "CANCELLED", "tradeType": "BUY", "asset": "BTC",
            "amount": "1", "fiatAmount": "100", "orderFinishTime": 1687000000000,
        }))
        self.assertIsNone(TaxHistoryService._parse_p2p_order({
            "orderStatus": "COMPLETED", "tradeType": "BUY", "asset": "",
            "amount": "1", "fiatAmount": "100", "orderFinishTime": 1687000000000,
        }))

    def test_deposit_record_feeds_fifo_inventory(self):
        trades = [
            {
                "id": "dep_btc", "timestamp_ms": 1000, "coin": "BTC", "symbol": "BTC/USDT",
                "side": "DEPOSIT", "action_type": "DEPOSIT", "market": "TRANSFER",
                "amount": 0.00319, "price": 25000.0, "total_usd": 79.75, "fee_usd": 0.0,
            },
            {
                "id": "sell_btc", "timestamp_ms": 2000, "coin": "BTC", "symbol": "BTC/USDT",
                "side": "SELL", "market": "SPOT",
                "amount": 0.00319, "price": 30685.0, "total_usd": 97.89, "fee_usd": 0.0979,
            },
        ]
        processed = self.service._apply_fifo_cost_basis(trades)
        sell = next(t for t in processed if t["id"] == "sell_btc")
        self.assertEqual(sell["cost_basis_usd"], 79.75)
        expected_pnl = round(97.89 - 79.75 - 0.0979, 4)
        self.assertAlmostEqual(sell["realized_pnl_usd"], expected_pnl, places=3)

    def test_p2p_sell_market_matches_fifo(self):
        p2p_buy = TaxHistoryService._parse_p2p_order({
            "orderStatus": "COMPLETED", "tradeType": "BUY", "asset": "BTC",
            "amount": "0.00319", "fiatAmount": "95.7", "fiatCurrency": "USD",
            "orderFinishTime": 1687000000000, "orderNumber": "P2P1",
        })
        sell = {
            "id": "sell_p2p", "timestamp_ms": 1687000001000, "coin": "BTC", "symbol": "BTC/USDT",
            "side": "SELL", "market": "P2P",
            "amount": 0.00319, "price": 30685.0, "total_usd": 97.89, "fee_usd": 0.0,
        }
        processed = self.service._apply_fifo_cost_basis([p2p_buy, sell])
        s = next(t for t in processed if t.get("id") == "sell_p2p")
        self.assertEqual(s["cost_basis_usd"], 95.7)
        self.assertGreater(s["realized_pnl_usd"], 0.0)

    def test_transfer_cache_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "exchange_transfers.json"
            with patch.object(self.service, "_get_transfer_cache_file", return_value=cache_file):
                empty = self.service._load_transfer_cache()
                self.assertEqual(empty["records"], [])
                self.assertEqual(empty["covered_since_ms"], 0)

                recs = [{"id": "dep_1", "timestamp_ms": 123, "side": "DEPOSIT"}]
                self.service._save_transfer_cache(recs, 100, 200)
                loaded = self.service._load_transfer_cache()
                self.assertEqual(loaded["records"], recs)
                self.assertEqual(loaded["covered_since_ms"], 100)
                self.assertEqual(loaded["last_fetch_ms"], 200)

    def test_fetch_transfers_surfaces_errors_instead_of_silent_zero(self):
        import tempfile
        from pathlib import Path

        now_ms = int(time.time() * 1000)
        auth_err = Exception('{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}')
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "exchange_transfers.json"
            with patch.object(self.service, "_get_transfer_cache_file", return_value=cache_file):
                with patch("ccxt.binance") as mock_binance_cls:
                    mock_ex = MagicMock()
                    mock_binance_cls.return_value = mock_ex
                    mock_ex.sapiGetCapitalDepositHisrec.side_effect = auth_err
                    mock_ex.sapiGetCapitalWithdrawHistory.side_effect = auth_err
                    mock_ex.sapiGetFiatOrders.side_effect = auth_err
                    mock_ex.sapiGetP2pOrderHistoryUser.side_effect = auth_err
                    records, meta = self.service._fetch_binance_deposits_and_withdrawals("key", "secret", 0, now_ms)

        self.assertEqual(records, [])
        self.assertTrue(len(meta["errors"]) > 0, "fetch errors must be surfaced, not swallowed")
        self.assertEqual(meta["lookback_since_ms"], _BINANCE_INCEPTION_MS)
        # All-time lookback must exceed the old 4-chunk (~340-day) cap
        self.assertGreater(meta["chunks_queried"], 30)
        joined = " ".join(meta["errors"])
        self.assertIn("deposit_history", joined)
        self.assertIn("fiat_orders_0", joined)

    def test_fetch_transfers_reuses_cached_deep_history(self):
        import tempfile
        from pathlib import Path

        now_ms = int(time.time() * 1000)
        cached_rec = {
            "id": "dep_old", "timestamp_ms": 1687000000000, "coin": "USDT", "side": "DEPOSIT",
            "action_type": "DEPOSIT", "market": "TRANSFER", "amount": 1000.0, "price": 1.0,
            "total_usd": 1000.0, "cost_basis_usd": 1000.0, "fee_usd": 0.0, "status": "SUCCESS",
            "source": "BINANCE_DEPOSIT", "datetime_utc": "2023-06-17 12:26:40",
            "datetime_local": "2023-06-17 15:26:40", "symbol": "USDT/USD",
            "realized_pnl_usd": 0.0, "trade_id": "old", "exchange_order_id": "tx",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "exchange_transfers.json"
            with patch.object(self.service, "_get_transfer_cache_file", return_value=cache_file):
                self.service._save_transfer_cache([cached_rec], _BINANCE_INCEPTION_MS, now_ms - 3600 * 1000)
                with patch("ccxt.binance") as mock_binance_cls:
                    mock_ex = MagicMock()
                    mock_binance_cls.return_value = mock_ex
                    mock_ex.sapiGetCapitalDepositHisrec.return_value = []
                    mock_ex.sapiGetCapitalWithdrawHistory.return_value = []
                    mock_ex.sapiGetFiatOrders.return_value = {"data": []}
                    mock_ex.sapiGetP2pOrderHistoryUser.return_value = {"data": []}
                    records, meta = self.service._fetch_binance_deposits_and_withdrawals("key", "secret", 0, now_ms)

        # Incremental refresh: only the recent overlap window is re-queried
        self.assertLessEqual(meta["chunks_queried"], 2)
        self.assertEqual(meta["errors"], [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "dep_old")

    def test_collect_trades_status_includes_transfer_meta(self):
        now_ms = int(time.time() * 1000)
        dep_rec = {
            "id": "dep_x", "timestamp_ms": now_ms - 1000, "coin": "USDT", "side": "DEPOSIT",
            "action_type": "DEPOSIT", "market": "TRANSFER", "amount": 500.0, "price": 1.0,
            "total_usd": 500.0, "cost_basis_usd": 500.0, "fee_usd": 0.0, "status": "SUCCESS",
            "source": "BINANCE_DEPOSIT", "symbol": "USDT/USD", "realized_pnl_usd": 0.0,
            "trade_id": "x", "exchange_order_id": "tx", "client_order_id": "",
            "datetime_utc": "2026-09-15 00:00:00", "datetime_local": "2026-09-15 03:00:00",
        }
        transfer_meta = {
            "errors": ["deposit_history: -2015"],
            "lookback_since_ms": _BINANCE_INCEPTION_MS,
            "chunks_queried": 40,
        }
        with patch.object(self.service, "_get_binance_credentials", return_value=("k", "s")):
            with patch("ccxt.binance") as mock_binance_cls:
                mock_binance_cls.return_value = MagicMock()
                with patch.object(self.service, "_fetch_binance_futures_trades", return_value=[]):
                    with patch.object(self.service, "_fetch_binance_futures_income", return_value=[]):
                        with patch.object(self.service, "_fetch_binance_spot_trades", return_value=[]):
                            with patch.object(
                                self.service,
                                "_fetch_binance_deposits_and_withdrawals",
                                return_value=([dep_rec], transfer_meta),
                            ):
                                merged, status = self.service._collect_trades(0, now_ms)

        self.assertTrue(status["connected"])
        self.assertEqual(status["deposits_count"], 1)
        self.assertEqual(status["transfer_fetch_errors"], ["deposit_history: -2015"])
        self.assertEqual(status["transfer_lookback_since_ms"], _BINANCE_INCEPTION_MS)
        self.assertEqual(status["transfer_chunks_queried"], 40)
        self.assertTrue(any(t.get("id") == "dep_x" for t in merged))

    def test_generate_tax_csv_warns_on_zero_deposits_and_missing_basis(self):
        trades = [
            {
                "datetime_utc": "2023-06-27 18:28:11", "datetime_local": "2023-06-27 21:28:11",
                "coin": "BTC", "symbol": "BTC/USDT", "side": "SELL", "market": "SPOT",
                "amount": 0.00319, "price": 30685.28, "total_usd": 97.89, "cost_basis_usd": 0.0,
                "fee_usd": 0.0979, "realized_pnl_usd": 97.79, "id": "s1", "trade_id": "s1",
                "source": "BINANCE_SPOT", "status": "FILLED", "notes": "",
            }
        ]
        summary = self.service.calculate_tax_summary(trades)
        csv_text = self.service.generate_tax_csv(
            "all", trades, summary, {"transfer_fetch_errors": ["deposit_history: -2015"]}
        )
        self.assertIn("לא אותרו הפקדות", csv_text)
        self.assertIn("deposit_history: -2015", csv_text)
        self.assertIn("מכירות ללא עלות רכישה", csv_text)

    def test_parse_p2p_order_c2c_schema(self):
        # Official Binance C2C endpoint format
        c2c_buy = TaxHistoryService._parse_p2p_order({
            "orderNumber": "C2C_999",
            "tradeType": "BUY",
            "asset": "USDT",
            "fiat": "USD",
            "amount": "500.0",
            "totalPrice": "500.0",
            "unitPrice": "1.0",
            "orderStatus": "COMPLETED",
            "createTime": 1690000000000,
        })
        self.assertIsNotNone(c2c_buy)
        self.assertEqual(c2c_buy["coin"], "USDT")
        self.assertEqual(c2c_buy["amount"], 500.0)
        self.assertEqual(c2c_buy["cost_basis_usd"], 500.0)
        self.assertEqual(c2c_buy["price"], 1.0)
        self.assertEqual(c2c_buy["market"], "P2P")

    def test_parse_convert_order(self):
        conv = TaxHistoryService._parse_convert_order({
            "quoteId": "CONV_123",
            "orderStatus": "SUCCESS",
            "fromAsset": "USDT",
            "toAsset": "BTC",
            "fromAmount": "3000.0",
            "toAmount": "0.1",
            "ratio": "0.00003333",
            "createTime": 1700000000000,
        })
        self.assertIsNotNone(conv)
        self.assertEqual(conv["coin"], "BTC")
        self.assertEqual(conv["amount"], 0.1)
        self.assertEqual(conv["cost_basis_usd"], 3000.0)
        self.assertEqual(conv["market"], "CONVERT")
        self.assertEqual(conv["side"], "BUY")

    def test_sapi_with_retry_aborts_on_1003_without_tight_loop(self):
        call_count = 0
        def fail_1003(params):
            nonlocal call_count
            call_count += 1
            raise Exception('binance 429 {"code":-1003, "msg":"Too many requests; current request has limited."}')

        errors = []
        res = TaxHistoryService._sapi_with_retry(fail_1003, {}, errors, "fiat_orders_0", max_retries=3)
        self.assertIsNone(res)
        # Should abort on first 1003 attempt to not hammer rate limiter
        self.assertEqual(call_count, 1)
        self.assertTrue(any("429" in e or "מגבלת קצב" in e for e in errors))


    # ------------------------------------------------------------------
    # P2P endpoint selection, -1021 retry, and cache-coverage hardening
    # ------------------------------------------------------------------

    def test_sapi_with_retry_retries_on_1021_with_resync(self):
        call_count = 0
        resync_count = 0

        def flaky(params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception('binance {"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}')
            return {"data": []}

        def resync():
            nonlocal resync_count
            resync_count += 1

        errors = []
        res = TaxHistoryService._sapi_with_retry(flaky, {}, errors, "withdraw_history", max_retries=3, resync=resync)
        self.assertEqual(res, {"data": []})
        self.assertEqual(call_count, 2)
        self.assertEqual(resync_count, 1)
        self.assertEqual(errors, [])

    def test_p2p_fetch_prefers_p2p_endpoint_over_deprecated_c2c(self):
        buy_order = {
            "orderStatus": "COMPLETED", "tradeType": "BUY", "asset": "BTC",
            "amount": "0.00319", "fiatAmount": "95.7", "fiatCurrency": "USD",
            "orderFinishTime": 1687000000000, "orderNumber": "P2P1",
        }

        def fake_p2p(params):
            if params.get("tradeType") == "BUY":
                return {"data": [buy_order]}
            return {"data": []}

        mock_ex = MagicMock()
        mock_ex.sapiGetC2cOrderMatchListUserOrderHistory.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
        mock_ex.sapiGetP2pOrderHistoryUser.side_effect = fake_p2p
        errors = []
        recs = self.service._fetch_binance_p2p_orders(mock_ex, errors, 0, 1750000000000)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["coin"], "BTC")
        self.assertEqual(recs[0]["cost_basis_usd"], 95.7)
        self.assertEqual(errors, [])
        # The decommissioned C2C endpoint must never be called when P2P works
        mock_ex.sapiGetC2cOrderMatchListUserOrderHistory.assert_not_called()

    def test_p2p_fetch_falls_back_to_c2c_when_p2p_unavailable(self):
        sell_order = {
            "orderNumber": "C2C_1", "tradeType": "SELL", "asset": "BTC",
            "fiat": "USD", "amount": "0.001", "totalPrice": "30.0", "unitPrice": "30000.0",
            "orderStatus": "COMPLETED", "createTime": 1687000000000,
        }

        def fake_c2c(params):
            if params.get("tradeType") == "SELL":
                return {"data": [sell_order]}
            return {"data": []}

        mock_ex = MagicMock()
        mock_ex.sapiGetP2pOrderHistoryUser.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
        mock_ex.sapiGetC2cOrderMatchListUserOrderHistory.side_effect = fake_c2c
        errors = []
        recs = self.service._fetch_binance_p2p_orders(mock_ex, errors, 0, 1750000000000)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["side"], "SELL")
        self.assertEqual(recs[0]["coin"], "BTC")
        self.assertEqual(errors, [])

    def test_p2p_fetch_records_error_only_when_all_endpoints_fail(self):
        mock_ex = MagicMock()
        mock_ex.sapiGetP2pOrderHistoryUser.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
        mock_ex.sapiGetC2cOrderMatchListUserOrderHistory.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
        errors = []
        recs = self.service._fetch_binance_p2p_orders(mock_ex, errors, 0, 1750000000000)
        self.assertEqual(recs, [])
        self.assertTrue(any("p2p_orders" in e for e in errors))

    def test_p2p_fetch_clamps_start_timestamp_to_p2p_inception(self):
        captured = {}

        def fake_p2p(params):
            captured.update(params)
            return {"data": []}

        mock_ex = MagicMock()
        mock_ex.sapiGetP2pOrderHistoryUser.side_effect = fake_p2p
        errors = []
        # since_ms=0 (all-time) must be clamped to the P2P service era, not 2017
        self.service._fetch_binance_p2p_orders(mock_ex, errors, 0, 1750000000000)
        self.assertEqual(captured.get("startTimestamp"), _P2P_INCEPTION_MS)
        self.assertEqual(errors, [])

    def test_has_critical_transfer_error_classification(self):
        self.assertTrue(TaxHistoryService._has_critical_transfer_error(["withdraw_history: -1021"]))
        self.assertTrue(TaxHistoryService._has_critical_transfer_error(["deposit_history: -2015"]))
        self.assertTrue(TaxHistoryService._has_critical_transfer_error(["transfer_master_fetcher: boom"]))
        self.assertFalse(TaxHistoryService._has_critical_transfer_error(["p2p_buy: -31002"]))
        self.assertFalse(TaxHistoryService._has_critical_transfer_error(["fiat_orders_0: x", "convert_history: y"]))
        self.assertFalse(TaxHistoryService._has_critical_transfer_error([]))

    def test_transfer_cache_advances_on_soft_errors_only(self):
        import tempfile
        from pathlib import Path

        now_ms = int(time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "exchange_transfers.json"
            with patch.object(self.service, "_get_transfer_cache_file", return_value=cache_file):
                with patch("ccxt.binance") as mock_binance_cls:
                    mock_ex = MagicMock()
                    mock_binance_cls.return_value = mock_ex
                    mock_ex.sapiGetCapitalDepositHisrec.return_value = []
                    mock_ex.sapiGetCapitalWithdrawHistory.return_value = []
                    mock_ex.sapiGetFiatOrders.return_value = {"data": []}
                    # P2P + C2C both unavailable -> soft error only
                    mock_ex.sapiGetP2pOrderHistoryUser.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
                    mock_ex.sapiGetC2cOrderMatchListUserOrderHistory.side_effect = Exception('{"code":-31002,"msg":"Illegal parameter"}')
                    with patch("time.sleep"):
                        records, meta = self.service._fetch_binance_deposits_and_withdrawals("key", "secret", 0, now_ms)
                    self.assertTrue(any("p2p_orders" in e for e in meta["errors"]))
                    # Soft-only errors must NOT block coverage advancement
                    saved = self.service._load_transfer_cache()
                    self.assertEqual(saved["covered_since_ms"], _BINANCE_INCEPTION_MS)

    def test_transfer_cache_does_not_advance_on_critical_error(self):
        import tempfile
        from pathlib import Path

        now_ms = int(time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "exchange_transfers.json"
            with patch.object(self.service, "_get_transfer_cache_file", return_value=cache_file):
                with patch("ccxt.binance") as mock_binance_cls:
                    mock_ex = MagicMock()
                    mock_binance_cls.return_value = mock_ex
                    mock_ex.sapiGetCapitalDepositHisrec.return_value = []
                    mock_ex.sapiGetCapitalWithdrawHistory.side_effect = Exception('{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}')
                    mock_ex.sapiGetFiatOrders.return_value = {"data": []}
                    mock_ex.sapiGetP2pOrderHistoryUser.return_value = {"data": []}
                    with patch("time.sleep"):
                        records, meta = self.service._fetch_binance_deposits_and_withdrawals("key", "secret", 0, now_ms)
                    self.assertTrue(any("withdraw_history" in e for e in meta["errors"]))
                    # Critical errors must block coverage advancement so gaps get retried
                    saved = self.service._load_transfer_cache()
                    self.assertEqual(saved["covered_since_ms"], 0)


if __name__ == "__main__":
    unittest.main()

