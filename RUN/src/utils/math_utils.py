"""
Math and precision utilities for order sizing.

Handles exchange-specific precision rules, minimum order sizes, and
safe arithmetic to prevent floating-point surprises.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


def parse_precision_to_decimals(precision: Any) -> int:
    """
    Convert CCXT precision representation to an integer number of decimal places.

    CCXT returns precision in two formats:
    - Decimal places as int/str: 4, 8, "4" -> 4
    - Tick / step size as float/str: 0.0001, 1e-05 -> 4, 5
    """
    if precision is None:
        return 8
    try:
        val = float(precision)
    except (ValueError, TypeError):
        return 8

    if val <= 0:
        return 0
    if val < 1.0:
        return int(round(abs(math.log10(val))))
    return int(val)


def truncate_to_precision(value: float, precision: Any) -> float:
    """
    Truncate (floor) a float to *precision* decimal places.

    Unlike round(), this never rounds up — critical for order amounts
    where exceeding available balance causes rejection.
    """
    dec_places = parse_precision_to_decimals(precision)
    if dec_places <= 0:
        return float(int(value))
    factor = 10 ** dec_places
    return math.floor(value * factor) / factor


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns *default* instead of raising on zero."""
    if abs(denominator) < 1e-12:
        return default
    return numerator / denominator


def pct_change(old: float, new: float) -> float:
    """Percentage change from *old* to *new*."""
    return safe_divide(new - old, old, 0.0)


def is_above_min_order(
    amount: float,
    price: float,
    min_amount: float,
    min_notional: float,
) -> bool:
    """
    Check if an order meets exchange minimums.

    Args:
        amount: Order quantity in base currency.
        price: Current price.
        min_amount: Minimum order amount (e.g., 0.00001 BTC).
        min_notional: Minimum order value in USD (e.g., $10).
    """
    if amount < min_amount:
        return False
    if amount * price < min_notional:
        return False
    return True


def compute_order_amount(
    target_value_usd: float,
    price: float,
    amount_precision: Any,
    min_amount: float,
    min_notional: float,
) -> Optional[float]:
    """
    Compute a valid order amount from a target USD value.

    Returns None if the amount is below exchange minimums.
    """
    if price <= 0:
        return None
    raw_amount = target_value_usd / price
    amount = truncate_to_precision(raw_amount, amount_precision)
    if not is_above_min_order(amount, price, min_amount, min_notional):
        return None
    return amount


def get_market_constraints(market_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract precision and limit constraints from CCXT market info.

    Returns a dict with keys:
        amount_precision, price_precision, min_amount, min_notional, min_price
    """
    limits = market_info.get("limits", {})
    precision = market_info.get("precision", {})

    amount_limits = limits.get("amount", {})
    price_limits = limits.get("price", {})
    cost_limits = limits.get("cost", {})

    raw_amt_prec = precision.get("amount", 8)
    raw_price_prec = precision.get("price", 2)

    return {
        "amount_precision": parse_precision_to_decimals(raw_amt_prec),
        "price_precision": parse_precision_to_decimals(raw_price_prec),
        "min_amount": amount_limits.get("min", 0.0) or 0.0,
        "max_amount": amount_limits.get("max") or float("inf"),
        "min_price": price_limits.get("min", 0.0) or 0.0,
        "min_notional": cost_limits.get("min", 10.0) or 10.0,
    }

