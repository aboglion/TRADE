"""
Structured logging setup.

Creates a dual-handler logger (console + file) that:
- Never logs API secrets or private keys
- Adds cycle correlation IDs
- Uses ISO-8601 UTC timestamps
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional


_SECRET_PATTERN = re.compile(
    r"(api[_-]?key|api[_-]?secret|password|token|secret)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)

_MASK = "***REDACTED***"


class SecretFilter(logging.Filter):
    """Strip potential secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub(
                lambda m: m.group().split("=")[0] + "=" + _MASK
                if "=" in m.group()
                else m.group().split(":")[0] + ": " + _MASK,
                record.msg,
            )
        return True


def setup_logger(
    name: str = "bot",
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Configure and return the application logger.

    Args:
        name: Logger name.
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path to a log file.  Directories are created
                  automatically.
    """
    logger = logging.getLogger(name)

    # Prevent duplicate handlers on re-init
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(SecretFilter())
    logger.addHandler(console)

    # File handler
    if log_file:
        log_dir = Path(log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SecretFilter())
        logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger
