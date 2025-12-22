"""Utility functions for example scripts."""

import os
import sys
from pathlib import Path
from typing import Optional


def _decode_env_bytes(raw: bytes) -> str:
    """Decode .env bytes with a few common encodings."""
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # Fallback: replace errors to avoid hard failure
    return raw.decode("utf-8", errors="replace")


def load_env_file(path: Path) -> bool:
    """Load environment variables from a file.

    Args:
        path: Path to the .env file

    Returns:
        True if file was loaded successfully, False otherwise
    """
    if not path.exists():
        return False

    print(f"Loading environment from {path}")
    raw = path.read_bytes()
    text = _decode_env_bytes(raw)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key] = value

    return True


def load_dotenv_files():
    """Load environment variables from .env files.

    Tries to load from .env.local first, then .env if .env.local doesn't exist.
    """
    # Get the project root directory (this file lives in the repo root)
    project_root = Path(__file__).parent

    # Try loading .env.local first, then .env if .env.local doesn't exist
    env_local_path = project_root / ".env.local"
    env_path = project_root / ".env"

    # Load .env.local if it exists, otherwise try .env
    if not load_env_file(env_local_path):
        load_env_file(env_path)


def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) gracefully."""
    print("\nExiting gracefully...")
    sys.exit(0)
