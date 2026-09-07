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

mkdir -p "$LOGS_DIR"
STOP_FLAG="${LOGS_DIR}/stop.flag"

# 1. Set intentional stop flag so bot_runner knows not to start fallback server
touch "$STOP_FLAG"

# 2. First send SIGTERM to recorded PIDs
for pid_file in "${LOGS_DIR}/bot_runner.pid" "${LOGS_DIR}/bot.pid"; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -SIGTERM "$pid" 2>/dev/null || true
        fi
    fi
done

# Also send SIGTERM to any python processes running bot_runner.py or main.py
pkill -SIGTERM -f "(bot_runner\.py|main\.py)" 2>/dev/null || true

# 3. Gracefully poll for up to 4 seconds (20 x 0.2s)
for _ in $(seq 1 20); do
    RUNNER_RUNNING=0
    MAIN_RUNNING=0

    if pgrep -f "(bot_runner\.py|main\.py)" >/dev/null 2>&1; then
        RUNNER_RUNNING=1
    fi

    if fuser "${PORT}/tcp" >/dev/null 2>&1; then
        MAIN_RUNNING=1
    fi

    if [ "$RUNNER_RUNNING" -eq 0 ] && [ "$MAIN_RUNNING" -eq 0 ]; then
        break
    fi

    sleep 0.2
done

# 4. If any processes still linger, escalate to SIGKILL and clear port
if pgrep -f "(bot_runner\.py|main\.py)" >/dev/null 2>&1 || fuser "${PORT}/tcp" >/dev/null 2>&1; then
    pkill -SIGKILL -f "(bot_runner\.py|main\.py)" 2>/dev/null || true
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
    sleep 0.5
fi

# 5. Clean up pid files and stop flag
rm -f "${LOGS_DIR}/bot_runner.pid" "${LOGS_DIR}/bot.pid" "$STOP_FLAG"

echo "🛑 Stopped all bot background processes"
exit 0
