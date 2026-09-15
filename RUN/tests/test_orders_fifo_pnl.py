import pytest
from src.web.server import DashboardRequestHandler

def test_enrich_orders_with_fifo_pnl_user_scenario():
    """
    Test the exact trade scenario from user:
    1. BUY 0.002 BTC @ 78543.10 with fee 0.0
    2. SELL 0.002 BTC @ 77658.60 with fee 0.076840 USDT
    Expected: Realized PnL is -$1.85 (negative, net of all fees).
    """
    orders = [
        {
            "client_order_id": "bot_20260914200229",
            "side": "BUY",
            "symbol": "BTC/USDT",
            "filled_amount": 0.002,
            "average_price": 78543.10,
            "fees": 0.0,
            "fee_currency": "USDT",
            "status": "FILLED",
        },
        {
            "client_order_id": "bot_20260915080108",
            "side": "SELL",
            "symbol": "BTC/USDT",
            "filled_amount": 0.002,
            "average_price": 77658.60,
            "fees": 0.076840,
            "fee_currency": "USDT",
            "status": "FILLED",
        },
    ]

    enriched = DashboardRequestHandler._enrich_orders_with_fifo_pnl(orders)
    assert len(enriched) == 2

    # First order (BUY): Cost basis accumulated, no realized PnL
    buy_order = enriched[0]
    assert buy_order["realized_pnl_usd"] is None
    assert buy_order["realized_pnl_pct"] is None
    assert "רכישה" in buy_order["pnl_note"]

    # Second order (SELL): Realized PnL computed
    sell_order = enriched[1]
    # Cost = 0.002 * 78543.10 = 157.0862
    # Proceeds = 0.002 * 77658.60 = 155.3172
    # Fees = 0.076840
    # Net PnL = 155.3172 - 157.0862 - 0.076840 = -1.84584 (-$1.85)
    assert abs(sell_order["realized_pnl_usd"] - (-1.8458)) < 0.01
    assert abs(sell_order["realized_pnl_pct"] - (-1.18)) < 0.05
    assert abs(sell_order["cost_basis_usd"] - 157.09) < 0.02
    assert abs(sell_order["matched_fees_usd"] - 0.0768) < 0.001


def test_enrich_orders_with_fifo_pnl_profit_and_fees():
    """
    Test profitable sell with both buy fee and sell fee deducted from net profit.
    """
    orders = [
        {
            "client_order_id": "buy_1",
            "side": "BUY",
            "symbol": "ETH/USDT",
            "filled_amount": 1.0,
            "average_price": 2000.0,
            "fees": 2.0,  # $2 buy fee
            "fee_currency": "USDT",
            "status": "FILLED",
        },
        {
            "client_order_id": "sell_1",
            "side": "SELL",
            "symbol": "ETH/USDT",
            "filled_amount": 1.0,
            "average_price": 2500.0,
            "fees": 2.5,  # $2.5 sell fee
            "fee_currency": "USDT",
            "status": "FILLED",
        },
    ]

    enriched = DashboardRequestHandler._enrich_orders_with_fifo_pnl(orders)
    sell_order = enriched[1]
    # Gross gain = 2500 - 2000 = 500
    # Total fees = 2.0 + 2.5 = 4.5
    # Net PnL = 500 - 4.5 = 495.5
    assert abs(sell_order["realized_pnl_usd"] - 495.5) < 0.01
    assert abs(sell_order["realized_pnl_pct"] - ((495.5 / 2000.0) * 100)) < 0.01
    assert sell_order["cost_basis_usd"] == 2000.0
    assert sell_order["matched_fees_usd"] == 4.5


def test_enrich_orders_with_partial_fifo_lots():
    """
    Test partial matching across multiple lots.
    """
    orders = [
        {
            "side": "BUY", "symbol": "SOL/USDT", "filled_amount": 10.0,
            "average_price": 100.0, "fees": 1.0, "fee_currency": "USDT", "status": "FILLED",
        },
        {
            "side": "BUY", "symbol": "SOL/USDT", "filled_amount": 10.0,
            "average_price": 150.0, "fees": 1.5, "fee_currency": "USDT", "status": "FILLED",
        },
        {
            # Sell 15 SOL: 10 from lot 1 (@ 100) and 5 from lot 2 (@ 150)
            "side": "SELL", "symbol": "SOL/USDT", "filled_amount": 15.0,
            "average_price": 200.0, "fees": 3.0, "fee_currency": "USDT", "status": "FILLED",
        },
    ]

    enriched = DashboardRequestHandler._enrich_orders_with_fifo_pnl(orders)
    sell_order = enriched[2]
    # Matched cost: 10 * 100 + 5 * 150 = 1000 + 750 = 1750
    # Matched buy fees: 1.0 (from lot 1) + 0.5 * 1.5 (0.75 from lot 2) = 1.75
    # Sell fee: 3.0
    # Proceeds: 15 * 200 = 3000
    # Net PnL = 3000 - 1750 - (3.0 + 1.75) = 1245.25
    assert sell_order["cost_basis_usd"] == 1750.0
    assert sell_order["matched_fees_usd"] == 4.75
    assert abs(sell_order["realized_pnl_usd"] - 1245.25) < 0.01
