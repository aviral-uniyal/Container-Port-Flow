"""
Configuration helpers for the Container-Port-Flow backend.

Resolves paths relative to the *repository root* (two directories above this
file) so that the backend works regardless of the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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


# ─── Database configuration ──────────────────────────────────────────────────


def get_db_config() -> dict[str, Any]:
    """Return PostgreSQL connection parameters from environment variables.

    Environment variables (with development defaults):
        ``DATABASE_URL`` – optional full PostgreSQL DSN / URL.
        ``PGHOST``       – database host        (default ``localhost``)
        ``PGPORT``       – database port        (default ``5432``)
        ``PGDATABASE``   – database name        (default ``container_port_flow``)
        ``PGUSER``       – database user        (default ``postgres``)
        ``PGPASSWORD``   – database password

    Returns
    -------
    dict
        Keyword arguments suitable for ``psycopg2.connect(**config)``.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return {"dsn": database_url}

    return {
        "host":     os.environ.get("PGHOST", "localhost"),
        "port":     int(os.environ.get("PGPORT", "5432")),
        "dbname":   os.environ.get("PGDATABASE", "container_port_flow"),
        "user":     os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("PGPASSWORD"),
    }
