#!/usr/bin/env python3
"""
Bot Runner & Fallback Supervisor (24/7 High Availability).

Monitors `main.py` execution. If `main.py` crashes or exits unexpectedly,
this supervisor catches the crash, sends a Telegram notification, and launches
a Fallback Emergency Web Server on port 8090.

The Fallback Web Server displays:
  - Crash alert & error summary
  - Live log viewer (last 200 lines of logs/bot.log)
  - Interactive "Restart System" button allowing browser-based restart over HTTP
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import signal
import subprocess
import urllib.request
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional

# Ensure project root & RUN dir are in path
SCRIPT_DIR = Path(__file__).resolve().parent
RUN_DIR = SCRIPT_DIR.parent
PROJECT_DIR = RUN_DIR.parent

sys.path.insert(0, str(RUN_DIR))
sys.path.insert(0, str(PROJECT_DIR))


def log_runner(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [RUNNER] {msg}"
    print(formatted, flush=True)
    
    # Also append to logs/bot.log so it appears in the log viewer
    log_path = PROJECT_DIR / "logs" / "bot.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{formatted}\n")
    except Exception:
        pass


def send_telegram_crash_alert(exit_code: int, last_logs: List[str]) -> None:
    """Send an emergency crash notification to Telegram if configured in config.yaml or .env."""
    try:
        config_path = RUN_DIR / "config.yaml"
        if not config_path.exists():
            return

        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        tg_cfg = cfg.get("telegram", {})
        enabled = tg_cfg.get("enabled", False)
        bot_token = str(tg_cfg.get("bot_token", "")).strip()
        chat_id = str(tg_cfg.get("chat_id", "")).strip()
        dash_url = str(tg_cfg.get("dashboard_url", "")).strip() or "http://localhost:8090"

        if not enabled or not bot_token or not chat_id:
            return

        if dash_url and not dash_url.startswith("http"):
            dash_url = f"http://{dash_url}"

        error_snippet = "\n".join(last_logs[-5:]) if last_logs else "No log detail available."
        # Truncate snippet if too long
        if len(error_snippet) > 800:
            error_snippet = error_snippet[-800:]

        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            f"🚨 <b>CRITICAL CRASH ALERT!</b>\n\n"
            f"⚠️ <b>Trading engine process stopped unexpectedly!</b>\n"
            f"❌ <b>Exit Code:</b> <code>{exit_code}</code>\n"
            f"⏱️ <b>Crash Time:</b> {time_str}\n\n"
            f"📝 <b>Recent logs before crash:</b>\n"
            f"<code>{error_snippet}</code>\n\n"
            f"🌐 <b>Emergency Fallback Server active on port 8090. Click to view logs & restart:</b>\n"
            f"<b><a href=\"{dash_url}\">{dash_url}</a></b>"
        )

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            if resp.status == 200:
                log_runner("Emergency Telegram crash alert sent successfully.")
    except Exception as ex:
        log_runner(f"Could not send Telegram crash alert: {ex}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass
        super().server_bind()


class FallbackCrashHandler(SimpleHTTPRequestHandler):
    """Emergency HTTP Request Handler served when main.py crashes."""

    exit_code: int = 1
    crash_time: str = ""
    port: int = 8090
    restart_requested: bool = False
    server_instance: Optional[ThreadedHTTPServer] = None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path in ("/", "", "/index", "/index.html"):
            self._serve_crash_page()
        elif clean_path == "/api/status":
            self._send_json({
                "status": "CRASHED",
                "exit_code": FallbackCrashHandler.exit_code,
                "crash_time": FallbackCrashHandler.crash_time,
                "message": "Main bot process crashed. Emergency Log Server is active."
            })
        elif clean_path == "/api/logs":
            self._handle_logs()
        else:
            self._serve_crash_page()

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path in ("/api/restart", "/api/trigger"):
            log_runner("Restart requested via Emergency Web Server UI!")
            FallbackCrashHandler.restart_requested = True
            self._send_json({"success": True, "message": "Restarting main bot process..."})
            if FallbackCrashHandler.server_instance:
                # Schedule server shutdown in separate thread so HTTP response finishes first
                import threading
                threading.Thread(target=FallbackCrashHandler.server_instance.shutdown).start()
        else:
            self._send_json({"error": "Unknown endpoint"}, status=404)

    def _handle_logs(self) -> None:
        log_path = PROJECT_DIR / "logs" / "bot.log"
        lines = []
        ignored_patterns = (
            "Loaded state:",
            "Portfolio snapshot:",
            "No state file found at",
            "No new closed candles",
        )
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                    filtered = [
                        line.strip() for line in all_lines
                        if line.strip() and not any(pat in line for pat in ignored_patterns)
                    ]
                    lines = filtered[-1000:]
            except Exception as e:
                lines = [f"Error reading log file: {e}"]
        else:
            lines = ["No log file generated yet."]
        self._send_json({"logs": lines, "exit_code": FallbackCrashHandler.exit_code, "crash_time": FallbackCrashHandler.crash_time})

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        try:
            content = json.dumps(data, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            pass

    def _serve_crash_page(self) -> None:
        html = f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚨 System Alert - Bot Process Crashed</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: #151c2c;
            --red-alert: #ef4444;
            --red-glow: rgba(239, 68, 68, 0.2);
            --green-btn: #10b981;
            --green-hover: #059669;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --border-color: #232d42;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-primary);
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 1000px;
            width: 100%;
            margin-top: 30px;
        }}
        .header-card {{
            background: var(--card-bg);
            border: 2px solid var(--red-alert);
            box-shadow: 0 0 25px var(--red-glow);
            border-radius: 16px;
            padding: 24px 32px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 20px;
            margin-bottom: 24px;
        }}
        .title-area {{ display: flex; align-items: center; gap: 16px; }}
        .alert-icon {{ font-size: 2.5rem; animation: pulse 1.5s infinite; }}
        @keyframes pulse {{
            0% {{ transform: scale(1); opacity: 1; }}
            50% {{ transform: scale(1.15); opacity: 0.8; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}
        h1 {{ font-size: 1.6rem; color: #ffffff; font-weight: 700; }}
        p.subtitle {{ color: var(--text-secondary); font-size: 0.95rem; margin-top: 4px; }}
        
        .restart-btn {{
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            color: #ffffff;
            border: none;
            padding: 14px 28px;
            font-size: 1.1rem;
            font-weight: 700;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .restart-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(16, 185, 129, 0.6);
            background: linear-gradient(135deg, #059669 0%, #047857 100%);
        }}
        .restart-btn:active {{ transform: translateY(0); }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .info-box {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px 20px;
        }}
        .info-label {{ font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 6px; }}
        .info-val {{ font-size: 1.2rem; font-weight: 700; color: #ffffff; }}
        .badge-red {{ color: var(--red-alert); }}
        
        .log-section {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
        }}
        .log-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }}
        .log-header h2 {{ font-size: 1.2rem; font-weight: 600; color: #ffffff; }}
        .live-tag {{
            background: rgba(239, 68, 68, 0.15);
            color: var(--red-alert);
            border: 1px solid var(--red-alert);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        
        .log-console {{
            background: #07090e;
            border: 1px solid #1a2336;
            border-radius: 10px;
            padding: 16px;
            height: 480px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.88rem;
            line-height: 1.5;
            color: #d1d5db;
            direction: ltr;
            text-align: left;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        .log-line {{ margin-bottom: 2px; }}
        .log-error {{ color: #f87171; font-weight: bold; }}
        .log-warn {{ color: #fbbf24; }}
        .log-info {{ color: #60a5fa; }}
        
        #toast {{
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: var(--green-btn);
            color: #ffffff;
            padding: 14px 24px;
            border-radius: 10px;
            font-weight: 600;
            display: none;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            z-index: 999;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header-card">
            <div class="title-area">
                <div class="alert-icon">🚨</div>
                <div>
                    <h1>System Alert: Trading Engine Stopped / Crashed</h1>
                    <p class="subtitle">Emergency diagnostic log server active on port 8090</p>
                </div>
            </div>
            <button class="restart-btn" onclick="restartBot()">
                <span>🔄</span> Restart Trading Engine Now
            </button>
        </div>

        <div class="info-grid">
            <div class="info-box">
                <div class="info-label">Engine Status</div>
                <div class="info-val badge-red">CRASHED / STOPPED</div>
            </div>
            <div class="info-box">
                <div class="info-label">Exit Code</div>
                <div class="info-val">{FallbackCrashHandler.exit_code}</div>
            </div>
            <div class="info-box">
                <div class="info-label">Crash Time</div>
                <div class="info-val" style="font-size: 1rem;">{FallbackCrashHandler.crash_time}</div>
            </div>
        </div>

        <div class="log-section">
            <div class="log-header">
                <h2>📋 Error Log & Detailed Traceback (logs/bot.log)</h2>
                <div class="live-tag">Auto Refresh (5s)</div>
            </div>
            <div class="log-console" id="logConsole">Loading logs...</div>
        </div>
    </div>

    <div id="toast"></div>

    <script>
        function showToast(msg) {{
            const t = document.getElementById("toast");
            t.innerText = msg;
            t.style.display = "block";
            setTimeout(() => {{ t.style.display = "none"; }}, 4000);
        }}

        async function fetchLogs() {{
            try {{
                const res = await fetch("/api/logs");
                if (!res.ok) return;
                const data = await res.json();
                const container = document.getElementById("logConsole");
                if (data.logs && data.logs.length > 0) {{
                    const htmlLines = data.logs.map(line => {{
                        let cls = "log-line";
                        if (line.includes("ERROR") || line.includes("CRITICAL") || line.includes("Exception") || line.includes("Traceback")) cls += " log-error";
                        else if (line.includes("WARNING")) cls += " log-warn";
                        else if (line.includes("INFO")) cls += " log-info";
                        return `<div class="${{cls}}">${{line.replace(/</g, "&lt;").replace(/>/g, "&gt;")}}</div>`;
                    }});
                    container.innerHTML = htmlLines.join("");
                    container.scrollTop = container.scrollHeight;
                }} else {{
                    container.innerText = "No log data available in log file.";
                }}
            }} catch (err) {{
                console.error("Error fetching logs:", err);
            }}
        }}

        async function restartBot() {{
            if (!confirm("Restart the trading engine now?")) return;
            showToast("Sending restart command to engine...");
            try {{
                const res = await fetch("/api/restart", {{ method: "POST" }});
                const data = await res.json();
                if (data.success) {{
                    showToast("Restart command received! Engine is starting up... Please wait for page refresh.");
                    setTimeout(() => {{
                        window.location.reload();
                    }}, 4000);
                }} else {{
                    alert("Error restarting engine: " + (data.error || "Unknown error"));
                }}
            }} catch (err) {{
                showToast("Trading engine restarted! Reloading page...");
                setTimeout(() => {{ window.location.reload(); }}, 3000);
            }}
        }}

        fetchLogs();
        setInterval(fetchLogs, 5000);
    </script>
</body>
</html>"""
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_fallback_server(port: int, exit_code: int) -> bool:
    """
    Run Emergency Fallback HTTP Server on the specified port.
    Returns True if user requested restart, False if stopped manually.
    """
    crash_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    FallbackCrashHandler.exit_code = exit_code
    FallbackCrashHandler.crash_time = crash_time
    FallbackCrashHandler.port = port
    FallbackCrashHandler.restart_requested = False

    log_runner(f"🚨 Launching Emergency Fallback Log Server on http://0.0.0.0:{port}")

    server = None
    for attempt in range(15):
        try:
            server = ThreadedHTTPServer(("0.0.0.0", port), FallbackCrashHandler)
            break
        except OSError as ex:
            if ex.errno == 98 and attempt < 14:
                log_runner(f"Port {port} busy during fallback server startup. Retrying in 1s... ({attempt + 1}/15)")
                time.sleep(1.0)
            else:
                raise

    if server is None:
        return False

    FallbackCrashHandler.server_instance = server

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        log_runner("Fallback server interrupted.")
    finally:
        server.server_close()

    return FallbackCrashHandler.restart_requested


def main() -> None:
    # Forward CLI args to main.py
    cli_args = sys.argv[1:]

    # Extract port if passed via --port
    port = 8090
    for i, arg in enumerate(cli_args):
        if arg == "--port" and i + 1 < len(cli_args):
            try:
                port = int(cli_args[i + 1])
            except ValueError:
                pass

    main_py_path = RUN_DIR / "main.py"

    log_runner("Starting Bot Supervisor loop 24/7...")

    shutdown_flag = False

    def handle_signal(sig, frame):
        nonlocal shutdown_flag
        log_runner(f"Received signal {sig}. Terminating bot runner...")
        shutdown_flag = True
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not shutdown_flag:
        log_runner(f"Launching main.py process: {sys.executable} {main_py_path} {' '.join(cli_args)}")
        
        proc = subprocess.Popen(
            [sys.executable, str(main_py_path)] + cli_args,
            cwd=str(RUN_DIR)
        )

        # Wait for child process
        try:
            exit_code = proc.wait()
        except KeyboardInterrupt:
            log_runner("KeyboardInterrupt received while waiting for main.py. Terminating child process...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            break

        log_runner(f"main.py exited with code: {exit_code}")

        # If clean exit (0), don't trigger emergency server unless requested
        if exit_code == 0:
            log_runner("main.py exited normally (Code 0). Exiting supervisor.")
            break

        # Read last 50 lines of logs/bot.log for Telegram crash alert
        log_path = PROJECT_DIR / "logs" / "bot.log"
        last_logs = []
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    last_logs = [line.strip() for line in f.readlines()[-50:] if line.strip()]
            except Exception:
                pass

        # Send Telegram Crash Notification
        send_telegram_crash_alert(exit_code, last_logs)

        # Launch Emergency Fallback HTTP Server
        log_runner("Starting Emergency Fallback Log Server...")
        should_restart = run_fallback_server(port=port, exit_code=exit_code)

        if should_restart:
            log_runner("User requested restart from Emergency Web UI. Waiting for port to free up...")
            for _ in range(10):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.5)
                        if s.connect_ex(("127.0.0.1", port)) != 0:
                            break
                except Exception:
                    break
                time.sleep(0.5)
            log_runner("Rebooting main.py...")
            continue
        else:
            log_runner("Fallback server exited without restart request. Supervisor shutting down.")
            break


if __name__ == "__main__":
    main()
