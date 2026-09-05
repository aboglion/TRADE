#!/usr/bin/env bash

# ==============================================================================
# Git Repository Auto-Updater & Process Restarter
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_FILE="${PROJECT_DIR}/logs/updater.pid"
LOG_FILE="${PROJECT_DIR}/logs/updater.log"
BRANCH="main"
CHECK_INTERVAL=60 # Check interval in seconds

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

run_loop() {
    log "🟢 Git Auto-Updater started (Monitoring branch '$BRANCH' every ${CHECK_INTERVAL}s)"
    
    # Cleanup PID file on exit
    trap 'log "🛑 Auto-Updater stopped."; rm -f "$PID_FILE"; exit 0' SIGINT SIGTERM EXIT

    while true; do
        cd "$PROJECT_DIR" || exit 1

        # Fetch remote ref silently
        if git fetch origin "$BRANCH" >/dev/null 2>&1; then
            LOCAL=$(git rev-parse HEAD 2>/dev/null)
            REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null)

            if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
                COMMIT_MSG=$(git log -1 --pretty=format:"%h - %an: %s" "origin/$BRANCH")
                log "🚀 New commit detected on GitHub!"
                log "Detail: $COMMIT_MSG"
                log "🔄 Executing 'make restart'..."

                # Execute make restart and write output to log
                if make restart >> "$LOG_FILE" 2>&1; then
                    log "✅ Restart completed successfully."
                else
                    log "❌ Restart encountered issues. Check $LOG_FILE for details."
                fi
            fi
        else
            log "⚠️ Warning: Could not fetch from origin/$BRANCH (Network issue or permissions)"
        fi

        sleep "$CHECK_INTERVAL"
    done
}

start_daemon() {
    mkdir -p "${PROJECT_DIR}/logs"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "⚠️ Auto-updater is already running (PID: $(cat "$PID_FILE"))"
        exit 0
    fi

    nohup "$0" run >> "$LOG_FILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PID_FILE"
    echo "⚡ Auto-updater started in background (PID: $PID)"
    echo "📋 View logs with: make watch-logs"
}

stop_daemon() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null
            rm -f "$PID_FILE"
            echo "🛑 Stopped auto-updater (PID: $PID)"
        else
            echo "⚠️ Stale PID file found, removing..."
            rm -f "$PID_FILE"
        fi
    else
        pkill -f "auto_updater\.sh" 2>/dev/null && echo "🛑 Stopped auto-updater process" || echo "No auto-updater process currently running."
    fi
}

status_daemon() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "🟢 Auto-updater is running (PID: $(cat "$PID_FILE"))"
    else
        echo "🔴 Auto-updater is not running"
    fi
}

case "$1" in
    start)
        start_daemon
        ;;
    stop)
        stop_daemon
        ;;
    status)
        status_daemon
        ;;
    run)
        run_loop
        ;;
    *)
        start_daemon
        ;;
esac
