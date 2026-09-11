"""
Tests for LoginRateLimiter and Brute-Force Password Protection.
"""

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


def test_fallback_crash_handler_api_json_response():
    from scripts.bot_runner import FallbackCrashHandler
    from io import BytesIO

    class DummySocket:
        def makefile(self, *args, **kwargs):
            return BytesIO(b"GET /api/strategy/conditions HTTP/1.1\r\nHost: localhost\r\n\r\n")

    class MockHandler(FallbackCrashHandler):
        def __init__(self):
            self.rfile = BytesIO(b"GET /api/strategy/conditions HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.wfile = BytesIO()
            self.headers = {}
            self.path = "/api/strategy/conditions"
            self.command = "GET"

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            if not hasattr(self, "sent_headers"):
                self.sent_headers = {}
            self.sent_headers[keyword] = value

        def end_headers(self):
            pass

    handler = MockHandler()
    handler.do_GET()

    assert handler.status_code == 503
    assert handler.sent_headers.get("Content-Type") == "application/json"
    output_body = handler.wfile.getvalue().decode("utf-8")
    import json
    parsed = json.loads(output_body)
    assert "error" in parsed
    assert "crashed" in parsed["error"].lower() or "stopped" in parsed["error"].lower()

