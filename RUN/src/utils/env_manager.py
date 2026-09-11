"""
Environment variable & .env file manager.

Ensures critical configurations (API keys, Telegram credentials, secrets)
are safely loaded into os.environ and atomically persisted directly into .env.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger("bot.utils.env_manager")

# Resolve standard project directories
_CURRENT_DIR = Path(__file__).resolve().parent
_RUN_DIR = _CURRENT_DIR.parent.parent
_PROJECT_DIR = _RUN_DIR.parent


def get_project_dirs() -> tuple[Path, Path]:
    """Return (RUN_DIR, PROJECT_DIR)."""
    return _RUN_DIR, _PROJECT_DIR


def find_env_file(preferred_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Find the canonical .env file location.
    Prioritizes explicit path, then RUN/.env, then PROJECT_ROOT/.env.
    """
    if preferred_path:
        p = Path(preferred_path).resolve()
        if p.exists() or p.parent.exists():
            return p

    run_env = _RUN_DIR / ".env"
    if run_env.exists():
        return run_env

    root_env = _PROJECT_DIR / ".env"
    if root_env.exists():
        return root_env

    # Default to RUN/.env
    return run_env


def ensure_env_symlink() -> None:
    """Ensure root PROJECT_DIR/.env points to RUN_DIR/.env via symlink."""
    try:
        run_env = _RUN_DIR / ".env"
        root_env = _PROJECT_DIR / ".env"

        if not run_env.exists() and root_env.exists() and not root_env.is_symlink():
            # If root exists and RUN doesn't, move or copy to RUN/.env
            run_env.write_text(root_env.read_text(encoding="utf-8"), encoding="utf-8")

        if run_env.exists():
            if not root_env.exists() and not root_env.is_symlink():
                root_env.symlink_to(run_env)
            elif root_env.is_symlink():
                target = root_env.resolve()
                if target != run_env.resolve():
                    root_env.unlink()
                    root_env.symlink_to(run_env)
    except Exception as ex:
        logger.debug("Could not verify .env symlink: %s", ex)


def load_dotenv(
    path: Optional[Union[str, Path]] = None,
    override: bool = False,
) -> Dict[str, str]:
    """
    Load .env files into environment variables from canonical candidate locations.
    Returns a dictionary of parsed environment variables.
    """
    ensure_env_symlink()
    loaded_vars: Dict[str, str] = {}

    candidates = []
    if path:
        candidates.append(Path(path))

    cur_dir = Path.cwd()
    candidates.extend([
        _RUN_DIR / ".env",
        _PROJECT_DIR / ".env",
        cur_dir / "RUN" / ".env",
        cur_dir / ".env",
        Path("/root/TRADE/RUN/.env"),
        Path("/root/TRADE/.env"),
    ])

    seen = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            if cand.is_file():
                with open(cand, "r", encoding="utf-8") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip()
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        if not k:
                            continue

                        # Never overwrite with placeholder values
                        if v in ("your_api_key_here", "your_api_secret_here"):
                            continue

                        loaded_vars[k] = v
                        if override or (k not in os.environ) or not os.environ[k] or os.environ[k] in ("your_api_key_here", "your_api_secret_here"):
                            os.environ[k] = v
        except Exception as e:
            logger.debug("Could not read candidate .env %s: %s", cand, e)

    return loaded_vars


def update_env_file(
    updates: Dict[str, Any],
    path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    Safely update or insert key-value pairs directly in the .env file.
    Preserves comments, formatting, and existing values.
    Atomically writes to disk and immediately updates os.environ.
    """
    if not updates:
        return True

    env_path = find_env_file(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert values to strings
    clean_updates: Dict[str, str] = {}
    for k, v in updates.items():
        key = str(k).strip()
        if not key:
            continue
        if isinstance(v, bool):
            val = "true" if v else "false"
        elif v is None:
            val = ""
        else:
            val = str(v).strip()
        clean_updates[key] = val

    existing_lines: list[str] = []
    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()
        except Exception as ex:
            logger.error("Failed to read existing .env file at %s: %s", env_path, ex)
            return False

    keys_handled = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            k = k.strip()
            if k in clean_updates:
                val = clean_updates[k]
                new_lines.append(f'{k}="{val}"\n')
                keys_handled.add(k)
                continue
        new_lines.append(line)

    # Append any remaining keys that weren't in the original file
    unhandled_keys = [k for k in clean_updates if k not in keys_handled]
    if unhandled_keys:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")

        # Categorize telegram vs exchange keys
        tg_keys = [k for k in unhandled_keys if k.startswith("TELEGRAM_")]
        other_keys = [k for k in unhandled_keys if not k.startswith("TELEGRAM_")]

        if tg_keys:
            new_lines.append("# Telegram Alerts Configuration\n")
            for k in tg_keys:
                new_lines.append(f'{k}="{clean_updates[k]}"\n')

        if other_keys:
            if tg_keys:
                new_lines.append("\n")
            for k in other_keys:
                new_lines.append(f'{k}="{clean_updates[k]}"\n')

    # Atomic write to temporary file in same directory, then rename
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=str(env_path.parent), encoding="utf-8", delete=False) as tf:
            temp_file = Path(tf.name)
            tf.writelines(new_lines)

        temp_file.replace(env_path)

        # Update in-memory os.environ immediately
        for k, v in clean_updates.items():
            os.environ[k] = v

        ensure_env_symlink()
        logger.info("Successfully persisted %d keys to %s", len(clean_updates), env_path)
        return True
    except Exception as ex:
        logger.error("Failed to write updated .env file at %s: %s", env_path, ex)
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        return False


def get_env_var(key: str, default: str = "") -> str:
    """Retrieve an environment variable, reloading .env if absent."""
    val = os.environ.get(key)
    if val is not None and val.strip():
        return val.strip()
    load_dotenv()
    return os.environ.get(key, default).strip()
