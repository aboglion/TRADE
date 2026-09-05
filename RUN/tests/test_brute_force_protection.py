"""
Tests for LoginRateLimiter and Brute-Force Password Protection.
"""

import time
from src.web.server import LoginRateLimiter


def test_rate_limiter_allows_under_threshold():
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=10, delay_seconds=0.01)
    ip = "192.168.1.100"

    # Attempt 1
    locked, _ = limiter.record_failed_attempt(ip)
    assert not locked

    # Attempt 2
    locked, _ = limiter.record_failed_attempt(ip)
    assert not locked

    # Status check
    is_locked, rem = limiter.is_locked_out(ip)
    assert not is_locked
    assert rem == 0


def test_rate_limiter_locks_out_after_max_attempts():
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=10, delay_seconds=0.01)
    ip = "192.168.1.101"

    limiter.record_failed_attempt(ip)
    limiter.record_failed_attempt(ip)

    # 3rd attempt triggers lockout
    locked, remaining = limiter.record_failed_attempt(ip)
    assert locked
    assert remaining > 0

    # Next attempt should show locked out
    is_locked, remaining_sec = limiter.is_locked_out(ip)
    assert is_locked
    assert remaining_sec > 0


def test_rate_limiter_successful_login_resets_counter():
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=10, delay_seconds=0.01)
    ip = "192.168.1.102"

    limiter.record_failed_attempt(ip)
    limiter.record_failed_attempt(ip)

    # Successful login resets attempts
    limiter.record_successful_login(ip)

    # Next failed attempt should be attempt #1 again
    locked, _ = limiter.record_failed_attempt(ip)
    assert not locked
    is_locked, _ = limiter.is_locked_out(ip)
    assert not is_locked
