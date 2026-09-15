"""
Tests for Dashboard Authentication Session Persistence and Lock/Logout.
Ensures session persists until user explicitly clicks logout/lock,
and that cookies and client tokens are properly cleared on logout.
"""

import io
import json
import os
from unittest.mock import MagicMock
from pathlib import Path

from src.web.server import DashboardRequestHandler


class DummyAuthHandler(DashboardRequestHandler):
    """Subclass of DashboardRequestHandler for testing auth endpoints without real socket."""

    def __init__(self, request_path="/api/auth_check", method="GET", body=b"", headers=None):
        self.path = request_path
        self.command = method
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = headers or {}
        self.server = MagicMock()
        self.client_address = ("127.0.0.1", 12345)
        self.sent_headers = {}
        self.sent_status = None

    def send_response(self, status, message=None):
        self.sent_status = status

    def send_header(self, keyword, value):
        self.sent_headers[keyword.lower()] = value

    def end_headers(self):
        pass


def test_login_sets_persistent_cookie(monkeypatch):
    """Verify successful login returns a 1-year persistent cookie."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret123")

    body_bytes = json.dumps({"password": "secret123"}).encode("utf-8")
    handler = DummyAuthHandler(
        request_path="/api/login",
        method="POST",
        body=body_bytes,
        headers={"Content-Length": str(len(body_bytes))},
    )
    handler._handle_login()

    assert handler.sent_status == 200
    cookie = handler.sent_headers.get("set-cookie", "")
    assert "dash_auth=secret123" in cookie
    assert "Max-Age=31536000" in cookie
    assert "SameSite=Lax" in cookie

    response = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert response.get("success") is True
    assert response.get("token") == "secret123"


def test_logout_clears_cookie_post():
    """Verify POST /api/logout clears dash_auth cookie with Max-Age=0."""
    handler = DummyAuthHandler(request_path="/api/logout", method="POST")
    handler.do_POST()

    assert handler.sent_status == 200
    cookie = handler.sent_headers.get("set-cookie", "")
    assert "dash_auth=" in cookie
    assert "Max-Age=0" in cookie
    assert "Expires=Thu, 01 Jan 1970 00:00:00 GMT" in cookie

    response = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert response.get("success") is True
    assert "Logged out" in response.get("message", "")


def test_logout_clears_cookie_get():
    """Verify GET /api/logout also clears dash_auth cookie."""
    handler = DummyAuthHandler(request_path="/api/logout", method="GET")
    handler.do_GET()

    assert handler.sent_status == 200
    cookie = handler.sent_headers.get("set-cookie", "")
    assert "Max-Age=0" in cookie


def test_authentication_with_persistent_cookie(monkeypatch):
    """Verify _is_authenticated recognizes persistent dash_auth cookie."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret123")

    # With correct cookie
    handler = DummyAuthHandler(
        request_path="/api/status",
        headers={"Cookie": "other_pref=dark; dash_auth=secret123; user_id=42"},
    )
    assert handler._is_authenticated() is True

    # With missing cookie
    handler_bad = DummyAuthHandler(
        request_path="/api/status",
        headers={"Cookie": "other_pref=dark"},
    )
    assert handler_bad._is_authenticated() is False


def test_authentication_with_header_token(monkeypatch):
    """Verify _is_authenticated recognizes X-Dashboard-Password header from localStorage."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret123")

    handler = DummyAuthHandler(
        request_path="/api/status",
        headers={"X-Dashboard-Password": "secret123"},
    )
    assert handler._is_authenticated() is True

    handler_bad = DummyAuthHandler(
        request_path="/api/status",
        headers={"X-Dashboard-Password": "wrong_password"},
    )
    assert handler_bad._is_authenticated() is False


def test_frontend_has_lock_buttons_and_localstorage():
    """Verify static frontend files include lock buttons and localStorage session persistence."""
    web_dir = Path(__file__).resolve().parent.parent / "src" / "web" / "static"
    index_html = (web_dir / "index.html").read_text(encoding="utf-8")
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    style_css = (web_dir / "style.css").read_text(encoding="utf-8")

    # index.html contains lock buttons
    assert 'id="lockDashboardBtn"' in index_html
    assert 'id="toolbarLockBtn"' in index_html
    assert 'lockDashboard()' in index_html

    # app.js uses localStorage for long-term session persistence
    assert 'localStorage.getItem("dash_password")' in app_js
    assert 'localStorage.setItem("dash_password"' in app_js
    assert 'localStorage.removeItem("dash_password")' in app_js
    assert 'function lockDashboard()' in app_js
    assert '/api/logout' in app_js

    # style.css contains styling for .lock-badge and .btn-lock
    assert '.lock-badge' in style_css
    assert '.btn-lock' in style_css
