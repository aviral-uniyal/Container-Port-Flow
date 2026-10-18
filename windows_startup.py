"""Windows launcher for the local FastAPI/Vite development dashboard.

The batch entry points never parse .env.local. In particular, PostgreSQL's
password is passed to child processes only through their environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / ".env.local"
PG_BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")
PG_SERVICE = "postgresql-x64-18"
PLACEHOLDER = "YOUR_ACTUAL_POSTGRESQL_PASSWORD"
DB_KEYS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"


class SetupError(Exception):
    """An actionable setup failure with no secret-bearing diagnostic text."""


def load_config(path: Path = CONFIG_FILE) -> dict[str, str]:
    if not path.is_file():
        raise SetupError(f"Missing {path}. Copy .env.local.example to .env.local and set PGPASSWORD locally.")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise SetupError(f"Cannot read {path}.") from None
    values: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in DB_KEYS or key in values:
            raise SetupError(f"Invalid or duplicate setting in {path}; use one KEY=value line per required key.")
        values[key] = value  # Never expand or interpret CMD metacharacters.
    if any(not values.get(key) for key in DB_KEYS):
        raise SetupError(f"Complete all five PostgreSQL settings in {path}.")
    if values["PGPASSWORD"] == PLACEHOLDER:
        raise SetupError(f"Replace the PGPASSWORD placeholder in {path} with your password locally.")
    if values["PGDATABASE"] != "container_port_flow":
        raise SetupError("PGDATABASE must be container_port_flow for this project.")
    if not values["PGPORT"].isdigit() or not 1 <= int(values["PGPORT"]) <= 65535:
        raise SetupError("PGPORT must be a valid port number.")
    return values


def child_env(config: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)  # Backend config gives DATABASE_URL precedence.
    env.update(config)
    return env


def pg_tool(name: str) -> str:
    installed = PG_BIN / f"{name}.exe"
    if installed.is_file():
        return str(installed)
    found = shutil.which(name)
    if found:
        return found
    raise SetupError(f"PostgreSQL 18 {name} is unavailable; check {PG_BIN}.")


def run_quiet(args: list[str], env: dict[str, str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, env=env, cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError(f"Could not run required program: {Path(args[0]).name}.") from None


def postgres_ready(config: dict[str, str], env: dict[str, str]) -> bool:
    result = run_quiet([pg_tool("pg_isready"), "-h", config["PGHOST"], "-p", config["PGPORT"],
                        "-U", config["PGUSER"], "-d", config["PGDATABASE"]], env)
    return result.returncode == 0


def maybe_start_postgres(config: dict[str, str], env: dict[str, str]) -> None:
    if config["PGHOST"].lower() not in ("localhost", "127.0.0.1", "::1"):
        raise SetupError("PostgreSQL is unreachable; remote services are not started automatically.")
    sc = shutil.which("sc.exe")
    if not sc:
        raise SetupError("PostgreSQL is unreachable and Windows service control is unavailable.")
    status = run_quiet([sc, "query", PG_SERVICE], env)
    if status.returncode != 0:
        raise SetupError(f"PostgreSQL is unreachable and service {PG_SERVICE} was not found.")
    if not re.search(r"STATE\s*:\s*1\s+STOPPED", status.stdout):
        raise SetupError(f"PostgreSQL is unreachable; service {PG_SERVICE} is not stopped. No restart attempted.")
    print(f"Starting stopped PostgreSQL service {PG_SERVICE}...")
    started = run_quiet([sc, "start", PG_SERVICE], env, timeout=15)
    if started.returncode != 0:
        raise SetupError("Windows did not permit starting PostgreSQL. Start the service with the needed permissions.")
    for _ in range(20):
        if postgres_ready(config, env):
            return
        time.sleep(1)
    raise SetupError("PostgreSQL service started but the configured database is not reachable.")


def check_database(config: dict[str, str], env: dict[str, str], start_service: bool = False) -> None:
    if not postgres_ready(config, env):
        if start_service:
            maybe_start_postgres(config, env)
        else:
            raise SetupError("PostgreSQL is not reachable at the configured host and port.")
    result = run_quiet([
        pg_tool("psql"), "-X", "-w", "-v", "ON_ERROR_STOP=1", "-tA",
        "-h", config["PGHOST"], "-p", config["PGPORT"],
        "-U", config["PGUSER"], "-d", config["PGDATABASE"],
        "-c", "SELECT current_database()",
    ], env)
    if result.returncode != 0 or result.stdout.strip() != config["PGDATABASE"]:
        raise SetupError("PostgreSQL authentication or database access failed. Check .env.local locally.")
    print(f"Database connection OK: {config['PGDATABASE']}.")


def url_text(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.read(100_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError):
        return None


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def backend_health() -> dict | None:
    spec = url_text(f"{BACKEND_URL}/openapi.json")
    if not spec:
        return None
    try:
        if json.loads(spec).get("info", {}).get("title") != "Container-Port-Flow API":
            return None
        return json.loads(url_text(f"{BACKEND_URL}/health") or "null")
    except (ValueError, TypeError):
        return None


def frontend_ready() -> bool:
    return "Container-Port-Flow | Port Operations" in (url_text(FRONTEND_URL) or "")


def wait_for(predicate, process: subprocess.Popen | None, seconds: int) -> bool:
    for _ in range(seconds * 2):
        if predicate():
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def first_working_executable(names: tuple[str, ...], env: dict[str, str]) -> str | None:
    """Return the first PATH executable that starts successfully."""
    checked: set[str] = set()
    for name in names:
        executable = shutil.which(name, path=env.get("PATH"))
        if not executable:
            continue
        normalized = os.path.normcase(os.path.abspath(executable))
        if normalized in checked:
            continue
        checked.add(normalized)
        if run_quiet([executable, "--version"], env).returncode == 0:
            return executable
    return None


def latest_nvm_install() -> Path | None:
    installs = Path(os.environ.get("LOCALAPPDATA", "")) / "Author Software" / "nvm" / "installs"
    candidates = sorted(installs.glob("v*/node.exe"),
                        key=lambda path: tuple(int(x) for x in re.findall(r"\d+", path.parent.name)))
    return candidates[-1].parent if candidates else None


def frontend_environment(env: dict[str, str]) -> tuple[dict[str, str], str]:
    frontend_env = env.copy()
    node = first_working_executable(("node.exe", "node"), frontend_env)
    if not node:
        install_dir = latest_nvm_install()
        if not install_dir:
            raise SetupError("Node.js is unavailable. Install/select Node.js before launching the frontend.")
        frontend_env["PATH"] = str(install_dir) + os.pathsep + frontend_env.get("PATH", "")
        node = first_working_executable(("node.exe", "node"), frontend_env)
        if not node:
            raise SetupError("Node.js is unavailable. Install/select Node.js before launching the frontend.")
    npm = first_working_executable(("npm.exe", "npm.cmd", "npm"), frontend_env)
    if not npm:
        # Some NVM for Windows releases expose a proxy npm.exe on PATH while
        # the runnable npm.cmd lives in the selected version's install folder.
        install_dir = latest_nvm_install()
        if install_dir and str(install_dir).lower() not in frontend_env.get("PATH", "").lower():
            frontend_env["PATH"] = str(install_dir) + os.pathsep + frontend_env.get("PATH", "")
            npm = first_working_executable(("npm.exe", "npm.cmd", "npm"), frontend_env)
    if not npm:
        raise SetupError("npm is unavailable. Install/select Node.js before launching the frontend.")
    if not (ROOT / "frontend" / "node_modules" / "vite").exists():
        raise SetupError("Frontend dependencies are missing. Run npm install once in frontend.")
    return frontend_env, npm


def start_all(config: dict[str, str], env: dict[str, str]) -> None:
    # Validate every executable and dependency before launching either server.
    dependencies = run_quiet([sys.executable, "-c", "import uvicorn, fastapi, psycopg2"], env)
    if dependencies.returncode != 0:
        raise SetupError("Backend Python dependencies are missing. Install backend/requirements.txt into the Python environment.")
    frontend_env, npm = frontend_environment(env)
    check_database(config, env, start_service=True)

    existing_backend = backend_health()
    existing_frontend = frontend_ready()
    if existing_backend is None and port_in_use(8000):
        raise SetupError("Port 8000 is occupied by another service; no backend was launched.")
    if not existing_frontend and port_in_use(5173):
        raise SetupError("Port 5173 is occupied by another service; no frontend was launched.")

    if existing_backend is not None:
        print("Existing Container-Port-Flow backend found; preserving its session.")
    else:
        print("Starting FastAPI backend...")
        backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--reload",
                                    "--app-dir", "backend"], cwd=ROOT, env=env,
                                   creationflags=subprocess.CREATE_NEW_CONSOLE)
        if not wait_for(lambda: backend_health() is not None, backend, 30):
            raise SetupError("Backend did not become ready. Check its console window; no process was killed.")
    health = backend_health()
    if health is None or health.get("status") != "ready":
        raise SetupError("Backend health is degraded. Check its console before opening the dashboard.")

    if existing_frontend:
        print("Existing Container-Port-Flow frontend found.")
    else:
        print("Starting React/Vite frontend...")
        frontend = subprocess.Popen([npm, "run", "dev", "--", "--host", "127.0.0.1", "--strictPort"],
                                    cwd=ROOT / "frontend", env=frontend_env,
                                    creationflags=subprocess.CREATE_NEW_CONSOLE)
        if not wait_for(frontend_ready, frontend, 30):
            raise SetupError("Frontend did not become ready. Check its console window; no process was killed.")
    print(f"Opening {FRONTEND_URL} in the default browser.")
    if not webbrowser.open(FRONTEND_URL):
        print(f"Open {FRONTEND_URL} manually if the browser did not appear.")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("check", "start"):
        print("Usage: windows_startup.py check|start")
        return 2
    try:
        config = load_config()
        env = child_env(config)
        if sys.argv[1] == "check":
            check_database(config, env)
        else:
            start_all(config, env)
        return 0
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
