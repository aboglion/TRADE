"""
Math and precision utilities for order sizing.

Handles exchange-specific precision rules, minimum order sizes, and
safe arithmetic to prevent floating-point surprises.
"""

from __future__ import annotations

import math
from typing import Any


def parse_precision_to_decimals(precision: Any) -> int:
    """
    Convert CCXT precision representation to an integer number of decimal places.

    CCXT returns precision in two formats:
    - Decimal places as int/str: 4, 8, "4" -> 4
    - Tick / step size as float/str: 0.0001, 1e-05, 1.0 -> 4, 5, 0
    """
    if precision is None:
        return 8
    if isinstance(precision, int):
        return max(0, precision)
    try:
        val = float(precision)
    except (ValueError, TypeError):
        return 8

    if val <= 0:
        return 0
    if val < 1.0:
        try:
            s = f"{val:.16f}".rstrip("0")
            if "." in s:
                return len(s.split(".")[1])
            return 0
        except Exception:
            return round(abs(math.log10(val)))
    return int(val)


def truncate_to_precision(value: float, precision: Any) -> float:
    """
    Truncate (floor towards zero) a float to *precision* decimal places.

    Unlike round(), this never rounds up in magnitude — critical for order amounts
    where exceeding available balance causes rejection. Symmetrically truncates
    negative numbers towards zero (e.g. -1.2345 with prec 2 -> -1.23).
    """
    if math.isnan(value) or math.isinf(value):
        return 0.0
    dec_places = parse_precision_to_decimals(precision)
    if dec_places <= 0:
        sign = -1.0 if value < 0 else 1.0
        return sign * float(int(abs(value) + 1e-11))
    factor = 10 ** dec_places
    sign = -1.0 if value < 0 else 1.0
    val_abs = abs(value)
    truncated = math.floor(round(val_abs * factor, 8)) / factor
    return sign * round(truncated, dec_places)


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
    Check if an order meets exchange minimums with float precision tolerance.

    Args:
        amount: Order quantity in base currency.
        price: Current price.
        min_amount: Minimum order amount (e.g., 0.00001 BTC).
        min_notional: Minimum order value in USD (e.g., $10).
    """
    if amount <= 0.0 or price <= 0.0:
        return False
    notional = amount * price
    # Use 1e-8 tolerance for notional and 1e-10 for amount to avoid floating point precision traps
    return (amount >= (min_amount - 1e-10)) and (notional >= (min_notional - 1e-8))


def compute_order_amount(
    target_value_usd: float,
    price: float,
    amount_precision: Any,
    min_amount: float,
    min_notional: float,
) -> float | None:
    """
    Compute a valid order amount from a target USD value.

    Returns None if the amount is below exchange minimums.
    """
    if price <= 0.0 or target_value_usd <= 0.0:
        return None
    raw_amount = target_value_usd / price
    amount = truncate_to_precision(raw_amount, amount_precision)
    if not is_above_min_order(amount, price, min_amount, min_notional):
        return None
    return amount


def get_market_constraints(market_info: dict[str, Any]) -> dict[str, Any]:
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


def clamp_leverage_by_binance_bracket(symbol: str, requested_lev: float, notional_usd: float) -> float:
    """
    Clamp requested leverage to official Binance USDT-M Futures Tiered Margin Brackets.
    Prevents API error -4028 (leverage exceeds bracket maximum).
    """
    clean_sym = symbol.split("/")[0].split(":")[0].upper()
    req = max(1.0, float(requested_lev))

    if "BTC" in clean_sym:
        if notional_usd <= 50_000.0:
            max_lev = 20.0
        elif notional_usd <= 250_000.0:
            max_lev = 10.0
        elif notional_usd <= 1_000_000.0:
            max_lev = 5.0
        elif notional_usd <= 5_000_000.0:
            max_lev = 3.0
        else:
            max_lev = 2.0
    elif "ETH" in clean_sym:
        if notional_usd <= 40_000.0:
            max_lev = 20.0
        elif notional_usd <= 200_000.0:
            max_lev = 10.0
        elif notional_usd <= 800_000.0:
            max_lev = 5.0
        elif notional_usd <= 2_000_000.0:
            max_lev = 3.0
        else:
            max_lev = 2.0
    else:  # SOL and other alts
        if notional_usd <= 20_000.0:
            max_lev = 20.0
        elif notional_usd <= 100_000.0:
            max_lev = 10.0
        elif notional_usd <= 500_000.0:
            max_lev = 5.0
        else:
            max_lev = 2.0

    return min(req, max_lev)


def get_binance_bracket_info(symbol: str, requested_lev: float, notional_usd: float) -> dict:
    """
    Return comprehensive Binance Tiered Margin Bracket metadata for a given symbol and notional value.
    """
    clean_sym = symbol.split("/")[0].split(":")[0].upper()
    req = max(1.0, float(requested_lev))

    if "BTC" in clean_sym:
        if notional_usd <= 50_000.0:
            tier, max_lev, max_notional, bracket_str = 1, 20.0, 50_000.0, "Tier 1: ≤$50,000 (Max 20x)"
        elif notional_usd <= 250_000.0:
            tier, max_lev, max_notional, bracket_str = 2, 10.0, 250_000.0, "Tier 2: ≤$250,000 (Max 10x)"
        elif notional_usd <= 1_000_000.0:
            tier, max_lev, max_notional, bracket_str = 3, 5.0, 1_000_000.0, "Tier 3: ≤$1,000,000 (Max 5x)"
        elif notional_usd <= 5_000_000.0:
            tier, max_lev, max_notional, bracket_str = 4, 3.0, 5_000_000.0, "Tier 4: ≤$5,000,000 (Max 3x)"
        else:
            tier, max_lev, max_notional, bracket_str = 5, 2.0, float("inf"), "Tier 5: >$5,000,000 (Max 2x)"
    elif "ETH" in clean_sym:
        if notional_usd <= 40_000.0:
            tier, max_lev, max_notional, bracket_str = 1, 20.0, 40_000.0, "Tier 1: ≤$40,000 (Max 20x)"
        elif notional_usd <= 200_000.0:
            tier, max_lev, max_notional, bracket_str = 2, 10.0, 200_000.0, "Tier 2: ≤$200,000 (Max 10x)"
        elif notional_usd <= 800_000.0:
            tier, max_lev, max_notional, bracket_str = 3, 5.0, 800_000.0, "Tier 3: ≤$800,000 (Max 5x)"
        elif notional_usd <= 2_000_000.0:
            tier, max_lev, max_notional, bracket_str = 4, 3.0, 2_000_000.0, "Tier 4: ≤$2,000,000 (Max 3x)"
        else:
            tier, max_lev, max_notional, bracket_str = 5, 2.0, float("inf"), "Tier 5: >$2,000,000 (Max 2x)"
    else:  # SOL and other alts
        if notional_usd <= 20_000.0:
            tier, max_lev, max_notional, bracket_str = 1, 20.0, 20_000.0, "Tier 1: ≤$20,000 (Max 20x)"
        elif notional_usd <= 100_000.0:
            tier, max_lev, max_notional, bracket_str = 2, 10.0, 100_000.0, "Tier 2: ≤$100,000 (Max 10x)"
        elif notional_usd <= 500_000.0:
            tier, max_lev, max_notional, bracket_str = 3, 5.0, 500_000.0, "Tier 3: ≤$500,000 (Max 5x)"
        else:
            tier, max_lev, max_notional, bracket_str = 4, 2.0, float("inf"), "Tier 4: >$500,000 (Max 2x)"

    clamped = min(req, max_lev)
    is_clamped = (req > max_lev)

    return {
        "symbol": clean_sym,
        "requested_leverage": req,
        "clamped_leverage": clamped,
        "max_allowed_leverage": max_lev,
        "tier": tier,
        "tier_max_notional": max_notional,
        "bracket_desc": bracket_str,
        "is_clamped": is_clamped,
    }



