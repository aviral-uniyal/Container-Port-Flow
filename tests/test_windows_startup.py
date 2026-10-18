"""Non-destructive checks for the Windows launcher configuration."""

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import windows_startup as startup


class StartupConfigurationTests(TestCase):
    def test_special_characters_are_loaded_literally_and_sent_only_in_environment(self):
        password = "sample% ! & ^ | < >"
        config_file = Mock(spec=Path)
        config_file.is_file.return_value = True
        config_file.read_text.return_value = (
            "PGHOST=localhost\nPGPORT=5432\nPGDATABASE=container_port_flow\n"
            f"PGUSER=postgres\nPGPASSWORD={password}\n"
        )
        config = startup.load_config(config_file)
        with patch.dict(os.environ, {"DATABASE_URL": "old-value"}):
            child = startup.child_env(config)
        self.assertEqual(child["PGPASSWORD"], password)
        self.assertNotIn("DATABASE_URL", child)
        with patch.object(startup, "postgres_ready", return_value=True), \
             patch.object(startup, "pg_tool", return_value="psql.exe"), \
             patch.object(startup, "run_quiet", return_value=Mock(
                 returncode=0, stdout="container_port_flow\n")) as run:
            startup.check_database(config, child)
        self.assertNotIn(password, run.call_args.args[0])
        self.assertEqual(run.call_args.args[1]["PGPASSWORD"], password)

    def test_missing_config_and_placeholder_stop_before_any_connection(self):
        config_file = Mock(spec=Path)
        config_file.is_file.return_value = False
        with self.assertRaisesRegex(startup.SetupError, "Missing"):
            startup.load_config(config_file)
        config_file.is_file.return_value = True
        config_file.read_text.return_value = (
            "PGHOST=localhost\nPGPORT=5432\nPGDATABASE=container_port_flow\n"
            "PGUSER=postgres\nPGPASSWORD=YOUR_ACTUAL_POSTGRESQL_PASSWORD\n"
        )
        with self.assertRaisesRegex(startup.SetupError, "placeholder"):
            startup.load_config(config_file)

    def test_npm_exe_is_detected_before_npm_cmd(self):
        env = {"PATH": r"C:\nvm\.nodejs"}
        paths = {
            "node.exe": r"C:\nvm\.nodejs\node.exe",
            "npm.exe": r"C:\nvm\.nodejs\npm.exe",
            "npm.cmd": r"C:\nvm\.nodejs\npm.cmd",
        }

        def which(name, path=None):
            return paths.get(name)

        with patch.object(startup.shutil, "which", side_effect=which), \
             patch.object(startup, "run_quiet", return_value=Mock(returncode=0)) as run, \
             patch.object(startup.Path, "exists", return_value=True):
            _, npm = startup.frontend_environment(env)

        self.assertEqual(npm, paths["npm.exe"])
        self.assertEqual(run.call_args_list[0].args[0], [paths["node.exe"], "--version"])
        self.assertEqual(run.call_args_list[1].args[0], [paths["npm.exe"], "--version"])

    def test_npm_cmd_is_used_when_npm_exe_is_absent(self):
        env = {"PATH": r"C:\Node"}
        paths = {
            "node.exe": r"C:\Node\node.exe",
            "npm.cmd": r"C:\Node\npm.cmd",
        }
        with patch.object(startup.shutil, "which", side_effect=lambda name, path=None: paths.get(name)), \
             patch.object(startup, "run_quiet", return_value=Mock(returncode=0)), \
             patch.object(startup.Path, "exists", return_value=True):
            _, npm = startup.frontend_environment(env)

        self.assertEqual(npm, paths["npm.cmd"])

    def test_dependency_checks_finish_before_backend_is_inspected_or_started(self):
        config = {key: "value" for key in startup.DB_KEYS}
        env = {"PATH": r"C:\Node"}
        events = []

        def quiet(*args, **kwargs):
            events.append("backend dependencies")
            return Mock(returncode=0)

        with patch.object(startup, "run_quiet", side_effect=quiet), \
             patch.object(startup, "frontend_environment", side_effect=lambda value: (events.append("frontend dependencies") or (value, "npm.exe"))), \
             patch.object(startup, "check_database", side_effect=lambda *args, **kwargs: events.append("database")), \
             patch.object(startup, "backend_health", side_effect=lambda: events.append("backend inspection") or {"status": "ready"}), \
             patch.object(startup, "frontend_ready", return_value=True), \
             patch.object(startup.webbrowser, "open", return_value=True):
            startup.start_all(config, env)

        self.assertEqual(events[:3], ["backend dependencies", "frontend dependencies", "database"])
        self.assertEqual(events.count("backend inspection"), 2)

    def test_start_path_launches_backend_and_frontend_once(self):
        config = {key: "value" for key in startup.DB_KEYS}
        env = {"PATH": r"C:\Node"}
        backend = Mock()
        frontend = Mock()
        backend.poll.return_value = None
        frontend.poll.return_value = None

        with patch.object(startup, "run_quiet", return_value=Mock(returncode=0)), \
             patch.object(startup, "frontend_environment", return_value=(env, "npm.exe")), \
             patch.object(startup, "check_database"), \
             patch.object(startup, "port_in_use", return_value=False), \
             patch.object(startup, "backend_health", side_effect=[None, {"status": "ready"}, {"status": "ready"}]), \
             patch.object(startup, "frontend_ready", side_effect=[False, True]), \
             patch.object(startup.subprocess, "Popen", side_effect=[backend, frontend]) as popen, \
             patch.object(startup.webbrowser, "open", return_value=True):
            startup.start_all(config, env)

        self.assertEqual(popen.call_count, 2)
        self.assertIn("uvicorn", popen.call_args_list[0].args[0])
        self.assertEqual(popen.call_args_list[1].args[0][:3], ["npm.exe", "run", "dev"])
