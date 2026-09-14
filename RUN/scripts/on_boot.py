#!/usr/bin/env python3
"""
Server Boot Recovery & Auto-Starter for Trading Bot.

Executed automatically on system boot via systemd and/or crontab.
Ensures network connectivity, detects the last active mode (LIVE vs DRY_RUN),
launches the 24/7 bot supervisor, and dispatches a confirmation alert to Telegram.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RUN_DIR = SCRIPT_DIR.parent
PROJECT_DIR = RUN_DIR.parent
LOGS_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOGS_DIR / "boot.log"


def log_boot(msg: str) -> None:
    """Write timestamped message to stdout and logs/boot.log."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [BOOT-RECOVERY] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except Exception:
        pass


def wait_for_network(max_attempts: int = 15, delay: float = 2.0) -> bool:
    """Wait for internet and DNS connectivity to ensure exchange & Telegram reachability."""
    log_boot("Checking network connectivity...")
    targets = [
        ("1.1.1.1", 53),
        ("8.8.8.8", 53),
        ("api.telegram.org", 443),
    ]

    for attempt in range(1, max_attempts + 1):
        for host, port in targets:
            try:
                with socket.create_connection((host, port), timeout=2.5):
                    log_boot(f"Network is ONLINE (connected to {host}:{port} on attempt {attempt}).")
                    return True
            except Exception:
                continue
        log_boot(f"Network not ready yet (attempt {attempt}/{max_attempts}). Waiting {delay:.1f}s...")
        time.sleep(delay)

    log_boot("⚠️ Network wait timed out. Proceeding anyway with local boot sequence...")
    return False


def is_bot_already_running(port: int = 8090) -> bool:
    """Check if bot_runner or dashboard server is already active."""
    # 1. Check PID files
    runner_pid_file = LOGS_DIR / "bot_runner.pid"
    if runner_pid_file.exists():
        try:
            pid = int(runner_pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            log_boot(f"Existing bot_runner process detected (PID: {pid}).")
            return True
        except (ValueError, OSError):
            pass

    # 2. Check port 8090
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                log_boot(f"Port {port} is already actively listening.")
                return True
    except Exception:
        pass

    return False


def resolve_run_mode() -> str:
    """Detect run mode from logs/last_mode, RUN/logs/last_mode, or config.yaml."""
    candidates = [
        LOGS_DIR / "last_mode",
        RUN_DIR / "logs" / "last_mode",
    ]
    for p in candidates:
        if p.exists():
            try:
                mode = p.read_text(encoding="utf-8").strip().upper()
                if mode in ("LIVE", "DRY_RUN"):
                    log_boot(f"Detected persistent mode from {p.name}: {mode}")
                    return mode
            except Exception:
                pass

    # Check config.yaml
    cfg_path = RUN_DIR / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            mode = str(cfg.get("run_mode", "DRY_RUN")).strip().upper()
            if mode in ("LIVE", "DRY_RUN"):
                log_boot(f"Detected mode from config.yaml: {mode}")
                return mode
        except Exception:
            pass

    log_boot("Defaulting to DRY_RUN mode.")
    return "DRY_RUN"


def send_telegram_reboot_alert(mode: str) -> None:
    """Send boot confirmation alert via TelegramService."""
    try:
        sys.path.insert(0, str(RUN_DIR))
        from src.utils.env_manager import load_dotenv
        load_dotenv()

        from src.services.telegram_service import TelegramService

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() in ("true", "1", "yes")
        dash_url = os.environ.get("TELEGRAM_DASHBOARD_URL", "").strip() or "http://localhost:8090"

        if not token or not chat_id:
            cfg_path = RUN_DIR / "config.yaml"
            if cfg_path.exists():
                import yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                tg_cfg = cfg.get("telegram", {})
                if not enabled and "enabled" in tg_cfg:
                    enabled = bool(tg_cfg.get("enabled", False))
                if not token:
                    token = str(tg_cfg.get("bot_token", "")).strip()
                if not chat_id:
                    chat_id = str(tg_cfg.get("chat_id", "")).strip()
                if not dash_url:
                    dash_url = str(tg_cfg.get("dashboard_url", "")).strip() or "http://localhost:8090"

        svc = TelegramService(bot_token=token, chat_id=chat_id, enabled=enabled, dashboard_url=dash_url)
        if svc.enabled and svc.is_configured():
            log_boot("Sending Telegram boot alert...")
            sent = svc.send_boot_notification(run_mode=mode, details="Auto-started by server boot supervisor")
            if sent:
                log_boot("Telegram boot alert sent successfully.")
            else:
                log_boot("Telegram boot alert dispatch returned False.")
        else:
            log_boot("Telegram alerts disabled or unconfigured — skipping Telegram notification.")
    except Exception as ex:
        log_boot(f"Could not send Telegram boot alert: {ex}")


def main() -> None:
    log_boot("=" * 60)
    log_boot("SERVER BOOT SEQUENCE INITIATED")
    log_boot(f"Project root: {PROJECT_DIR}")

    # Check if already running
    if is_bot_already_running(port=8090):
        log_boot("Trading bot is already active and healthy. Exiting boot launcher cleanly.")
        sys.exit(0)

    # Wait for network
    wait_for_network(max_attempts=15, delay=2.0)

    # Determine mode
    mode = resolve_run_mode()
    log_boot(f"Launching bot in mode: {mode} (Port: 8090)...")

    # Start bot launcher script
    start_script = SCRIPT_DIR / "start_bot.sh"
    if not start_script.exists():
        log_boot(f"ERROR: Cannot find {start_script}")
        sys.exit(1)

    cmd = ["/usr/bin/env", "bash", str(start_script), mode, "8090"]
    env = os.environ.copy()
    if mode == "LIVE":
        env["CONFIRM_LIVE"] = "YES_I_UNDERSTAND"

    try:
        res = subprocess.run(
            cmd,
            cwd=str(RUN_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        log_boot(f"start_bot.sh output:\n{res.stdout.strip()}")
        if res.stderr.strip():
            log_boot(f"start_bot.sh stderr:\n{res.stderr.strip()}")
    except Exception as ex:
        log_boot(f"CRITICAL: Failed to run start_bot.sh: {ex}")
        sys.exit(1)

    # Wait 3s for daemon initialization
    time.sleep(3.0)

    # Send Telegram alert
    send_telegram_reboot_alert(mode)
    log_boot("SERVER BOOT SEQUENCE COMPLETED SUCCESSFULLY")
    log_boot("=" * 60)


if __name__ == "__main__":
    main()
