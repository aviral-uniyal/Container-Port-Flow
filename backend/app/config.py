"""
Configuration helpers for the Container-Port-Flow backend.

Resolves paths relative to the *repository root* (two directories above this
file) so that the backend works regardless of the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repository root: backend/app/config.py  →  ../../
_THIS_DIR = Path(__file__).resolve().parent          # backend/app/
REPO_ROOT = _THIS_DIR.parent.parent                  # project root

# Default path to the C++ JSON-Lines executable, relative to REPO_ROOT.
_DEFAULT_EXE_REL = os.path.join("os-simulation", "port_json_iface.exe")

def get_cpp_exe_path() -> Path:
    """Return the resolved path to the C++ executable.

    Priority:
        1. ``CPP_EXE_PATH`` environment variable (absolute or relative to cwd).
        2. ``<REPO_ROOT>/os-simulation/port_json_iface.exe``.
    """
    env = os.environ.get("CPP_EXE_PATH")
    if env:
        return Path(env).resolve()
    return (REPO_ROOT / _DEFAULT_EXE_REL).resolve()
