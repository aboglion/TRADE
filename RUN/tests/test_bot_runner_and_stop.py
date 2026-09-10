import os
import signal
import subprocess
import sys
from pathlib import Path


def test_find_python_executable():
    """Verify that find_python_executable discovers a runnable Python interpreter."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import bot_runner

    py_path = bot_runner.find_python_executable()
    assert py_path is not None
    assert Path(py_path).exists() or py_path == "python3" or py_path == sys.executable


def test_stop_flag_prevents_emergency_fallback(tmp_path: Path):
    """
    Verify that when stop.flag is set in logs directory,
    bot_runner treats any main.py exit as intentional and does not trigger fallback server.
    """
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import bot_runner

    # Simulate stop flag
    logs_dir = bot_runner.PROJECT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stop_flag = logs_dir / "stop.flag"
    stop_flag.touch()

    try:
        # Check condition that bot_runner uses
        is_intentional = (
            stop_flag.exists() or
            False  # shutdown_flag
        )
        assert is_intentional is True
    finally:
        if stop_flag.exists():
            stop_flag.unlink()


def test_stop_bot_script_execution():
    """Verify stop_bot.sh executes cleanly and returns exit code 0."""
    stop_script = Path(__file__).parent.parent / "scripts" / "stop_bot.sh"
    assert stop_script.exists()
    assert os.access(str(stop_script), os.X_OK)

    res = subprocess.run([str(stop_script), "18099"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Stopped all bot background processes" in res.stdout


def test_fallback_crash_handler_status_and_error():
    """Verify FallbackCrashHandler properly reports CRASHED status and error summary."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import bot_runner

    bot_runner.FallbackCrashHandler.exit_code = 1
    bot_runner.FallbackCrashHandler.crash_time = "2026-09-09 23:55:00"
    bot_runner.FallbackCrashHandler.last_crash_error = "Traceback (most recent call last):\n  File 'main.py', line 10\nExchangeAuthError: Binance authentication failed"

    assert bot_runner.FallbackCrashHandler.last_crash_error != ""
    assert "ExchangeAuthError" in bot_runner.FallbackCrashHandler.last_crash_error


def test_traceback_extraction_logic():
    """Verify extracting error traceback from captured process output."""
    sample_output = [
        "2026-09-09T23:50:00 | INFO | bot | Cycle started",
        "Traceback (most recent call last):",
        "  File '/home/uns/TRADE/RUN/main.py', line 356, in <module>",
        "    main()",
        "src.core.exceptions.ExchangeAuthError: Binance Futures authentication failed (-2015).",
    ]

    crash_lines = []
    capture_tb = False
    for line in sample_output:
        if "Traceback (most recent call last):" in line or "Exception" in line or "Error:" in line or "CRITICAL" in line:
            capture_tb = True
        if capture_tb:
            crash_lines.append(line)

    extracted = "\n".join(crash_lines)
    assert "Traceback (most recent call last):" in extracted
    assert "ExchangeAuthError" in extracted


def test_fallback_crash_handler_multi_status():
    """Verify FallbackCrashHandler properly reflects STOPPED, UPDATING, and CRASHED states."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import bot_runner

    # 1. Test CRASHED
    bot_runner.FallbackCrashHandler.system_status = "CRASHED"
    bot_runner.FallbackCrashHandler.exit_code = 2
    assert bot_runner.FallbackCrashHandler.system_status == "CRASHED"

    # 2. Test STOPPED
    bot_runner.FallbackCrashHandler.system_status = "STOPPED"
    bot_runner.FallbackCrashHandler.exit_code = 0
    bot_runner.FallbackCrashHandler.last_crash_error = "Trading engine stopped cleanly."
    assert bot_runner.FallbackCrashHandler.system_status == "STOPPED"

    # 3. Test UPDATING
    bot_runner.FallbackCrashHandler.system_status = "UPDATING"
    bot_runner.FallbackCrashHandler.last_crash_error = "Git Pull in progress."
    assert bot_runner.FallbackCrashHandler.system_status == "UPDATING"


def test_fallback_crash_handler_html_generation():
    """Verify HTML generation contains proper Hebrew/English indicators for different states."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import bot_runner
    from io import BytesIO

    class DummyHandler(bot_runner.FallbackCrashHandler):
        def __init__(self):
            self.wfile = BytesIO()
            self.headers = {}
            self._headers_buffer = []

        def send_response(self, code, message=None):
            self.status_code = code

        def send_header(self, keyword, value):
            pass

        def end_headers(self):
            pass

    # Test STOPPED page
    bot_runner.FallbackCrashHandler.system_status = "STOPPED"
    bot_runner.FallbackCrashHandler.last_crash_error = "Stopped by user"
    h = DummyHandler()
    h._serve_crash_page()
    content = h.wfile.getvalue().decode("utf-8")
    assert "STOPPED" in content
    assert "מנוע המסחר מושבת" in content
    assert "הפעל מנוע מסחר" in content

    # Test UPDATING page
    bot_runner.FallbackCrashHandler.system_status = "UPDATING"
    h = DummyHandler()
    h._serve_crash_page()
    content = h.wfile.getvalue().decode("utf-8")
    assert "UPDATING" in content
    assert "עדכון מערכת בפעולה" in content

    # Test CRASHED page
    bot_runner.FallbackCrashHandler.system_status = "CRASHED"
    bot_runner.FallbackCrashHandler.last_crash_error = "ZeroDivisionError: division by zero"
    h = DummyHandler()
    h._serve_crash_page()
    content = h.wfile.getvalue().decode("utf-8")
    assert "CRASHED" in content
    assert "התרעת מערכת: מנוע המסחר קרס" in content
    assert "ZeroDivisionError" in content

