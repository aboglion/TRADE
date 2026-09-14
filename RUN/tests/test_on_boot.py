"""
Unit tests for on_boot auto-start logic and recovery.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts directory is in sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import on_boot


def test_resolve_run_mode_from_last_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(on_boot, "LOGS_DIR", tmp_path)
    (tmp_path / "last_mode").write_text("LIVE\n", encoding="utf-8")
    assert on_boot.resolve_run_mode() == "LIVE"


def test_resolve_run_mode_default(tmp_path, monkeypatch):
    monkeypatch.setattr(on_boot, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(on_boot, "RUN_DIR", tmp_path)
    assert on_boot.resolve_run_mode() == "DRY_RUN"


def test_is_bot_already_running_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(on_boot, "LOGS_DIR", tmp_path)
    runner_pid_file = tmp_path / "bot_runner.pid"
    # Write current test process PID which is definitely alive
    runner_pid_file.write_text(str(os.getpid()), encoding="utf-8")
    assert on_boot.is_bot_already_running(port=59999) is True


def test_is_bot_already_running_dead_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(on_boot, "LOGS_DIR", tmp_path)
    runner_pid_file = tmp_path / "bot_runner.pid"
    # PID 9999999 is almost certainly non-existent
    runner_pid_file.write_text("9999999", encoding="utf-8")
    assert on_boot.is_bot_already_running(port=59999) is False


@patch("socket.create_connection")
def test_wait_for_network_success(mock_conn):
    mock_conn.return_value.__enter__.return_value = MagicMock()
    assert on_boot.wait_for_network(max_attempts=1) is True


@patch("socket.create_connection", side_effect=OSError("Network unreachable"))
def test_wait_for_network_timeout(mock_conn):
    assert on_boot.wait_for_network(max_attempts=1, delay=0.01) is False
