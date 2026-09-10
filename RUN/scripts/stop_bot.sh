#!/usr/bin/env bash
# ==============================================================================
# Graceful & Deterministic Bot Termination Script
# Ensures bot_runner supervisor and main trading engine stop cleanly,
# preventing ghost fallback servers and port 8090 collisions.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGS_DIR="${PROJECT_DIR}/logs"
PORT="${1:-8090}"
MODE_ARG="${2:-}"

mkdir -p "$LOGS_DIR"
STOP_FLAG="${LOGS_DIR}/stop.flag"
KILL_FLAG="${LOGS_DIR}/kill.flag"

if [ "$MODE_ARG" = "--hard" ] || [ "$MODE_ARG" = "--kill" ]; then
    # Full hard kill: kill supervisor, kill main engine, kill port
    touch "$KILL_FLAG"
    for pid_file in "${LOGS_DIR}/bot_runner.pid" "${LOGS_DIR}/bot.pid"; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file" 2>/dev/null || true)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill -SIGKILL "$pid" 2>/dev/null || true
            fi
        fi
    done
    pkill -SIGKILL -f "(bot_runner\.py|main\.py)" 2>/dev/null || true
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
    rm -f "${LOGS_DIR}/bot_runner.pid" "${LOGS_DIR}/bot.pid" "$STOP_FLAG" "$KILL_FLAG"
    echo "🛑 Stopped all bot background processes"
    exit 0
fi

# Graceful stop: Set stop flag so supervisor knows it's an intentional stop
touch "$STOP_FLAG"

# Terminate main.py engine specifically so supervisor can bind fallback server
if [ -f "${LOGS_DIR}/bot.pid" ]; then
    main_pid=$(cat "${LOGS_DIR}/bot.pid" 2>/dev/null || true)
    if [ -n "$main_pid" ] && kill -0 "$main_pid" 2>/dev/null; then
        kill -SIGTERM "$main_pid" 2>/dev/null || true
    fi
fi
pkill -SIGTERM -f "RUN/main\.py" 2>/dev/null || true

# Wait up to 3s for main.py to release port
for _ in $(seq 1 15); do
    if ! pgrep -f "RUN/main\.py" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# If main.py still lingering, force kill it so fallback server can bind port
if pgrep -f "RUN/main\.py" >/dev/null 2>&1; then
    pkill -SIGKILL -f "RUN/main\.py" 2>/dev/null || true
fi

# If bot_runner is not running, also clear port
if ! pgrep -f "bot_runner\.py" >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
fi

echo "🛑 Stopped all bot background processes"
exit 0
