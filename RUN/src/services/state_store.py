"""
State store — persistent bot state management.

Implements atomic writes (write to temp → rename) to prevent
corruption if the process crashes mid-write.

The JSON backend is simple and human-readable.  The interface
supports easy replacement with SQLite or PostgreSQL in the future.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from src.core.exceptions import StateCorruptionError
from src.core.interfaces import IStateStore
from src.core.models import BotState

logger = logging.getLogger("bot.services.state")


class JsonStateStore(IStateStore):
    """
    JSON file-based state persistence with atomic writes.

    Writes go to a temp file first, then atomic rename to the target.
    A backup is kept of the previous state for manual recovery.
    """

    def __init__(self, path: str):
        self._path = Path(path)
        self._backup_path = Path(str(path) + ".bak")

    def load_state(self) -> BotState:
        """
        Load state from disk.

        Returns a fresh BotState if no file exists or the file is corrupt.
        """
        if not self._path.exists():
            logger.debug("No state file found at %s — starting fresh", self._path)
            return BotState()

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)

            state = BotState.from_dict(data)
            logger.debug(
                "Loaded state: last_candle=%s, pending=%d, completed=%d",
                state.last_processed_candle_ts,
                len(state.pending_orders),
                len(state.completed_orders),
            )
            return state

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("State file corrupt: %s — attempting backup", e)

            # Try backup
            if self._backup_path.exists():
                try:
                    with open(self._backup_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    state = BotState.from_dict(data)
                    logger.warning("Recovered state from backup")
                    return state
                except Exception as e2:
                    logger.error("Backup also corrupt: %s", e2)

            raise StateCorruptionError(
                f"Cannot load state from {self._path} or backup: {e}"
            )

    def save_state(self, state: BotState) -> None:
        """
        Atomically persist bot state.

        1. Write to temp file in same directory
        2. Backup current state file
        3. Atomic rename temp → target
        """
        # Ensure directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)

        data = state.to_dict()

        # Write to temp file (same filesystem for atomic rename)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix="state_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            # Backup current state
            if self._path.exists():
                shutil.copy2(str(self._path), str(self._backup_path))

            # Atomic rename
            os.replace(tmp_path, str(self._path))

            logger.debug("State saved to %s", self._path)

        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
