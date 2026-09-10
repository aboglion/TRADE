#!/usr/bin/env bash
# ==============================================================================
# Robust Background Daemon Launcher for Trading Bot
# Spawns bot_runner.py in its own detached POSIX session (start_new_session)
# ensuring it runs 24/7 immune to terminal/SSH disconnects or subshell closures.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$RUN_DIR/.." && pwd)"
LOGS_DIR="${PROJECT_DIR}/logs"
PORT="${2:-8090}"
MODE="${1:-DRY_RUN}"

mkdir -p "$LOGS_DIR"

# Auto-locate python in virtual environment
PYTHON=""
for cand in "${PROJECT_DIR}/.venv/bin/python3" "${PROJECT_DIR}/venv/bin/python3" "/root/TRADE/venv/bin/python3"; do
    if [ -x "$cand" ]; then
        PYTHON="$cand"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    PYTHON="$(command -v python3 || echo python)"
fi

# Stop existing instance cleanly before starting
"$SCRIPT_DIR/stop_bot.sh" "$PORT" >/dev/null 2>&1 || true

# Start via python subprocess with start_new_session to ensure true daemonization
"$PYTHON" -c "
import os, subprocess, sys
log_file = open('${LOGS_DIR}/bot.log', 'a', encoding='utf-8')
env = os.environ.copy()
if '$MODE' == 'LIVE':
    env['CONFIRM_LIVE'] = 'YES_I_UNDERSTAND'
subprocess.Popen(
    [
        '$PYTHON',
        '${SCRIPT_DIR}/bot_runner.py',
        '--mode', '$MODE',
        '--dashboard',
        '--port', '$PORT'
    ],
    cwd='${PROJECT_DIR}',
    stdin=subprocess.DEVNULL,
    stdout=log_file,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    env=env,
)
"

# Brief wait to ensure process registered in pid files
sleep 1.0

if [ "$MODE" = "LIVE" ]; then
    echo "🔥 Bot started in LIVE mode with Emergency Crash Fallback Server (24/7 on port $PORT)"
else
    echo "⚡ Bot started in DRY_RUN mode with Emergency Crash Fallback Server (24/7 on port $PORT)"
fi
