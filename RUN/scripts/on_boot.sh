#!/usr/bin/env bash
# ==============================================================================
# Shell wrapper for on_boot.py — Triggered on system reboot
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$RUN_DIR/.." && pwd)"

# Auto-locate python in virtual environment or system
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

exec "$PYTHON" "${SCRIPT_DIR}/on_boot.py"
