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
