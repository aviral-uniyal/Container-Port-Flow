"""
Tests for backend.app.cpp_bridge – CppBridge class.

All tests use temporary *Python* scripts as fake child processes so they
do not depend on the real C++ executable.  They run on the standard-library
``unittest`` runner with Python 3.14.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

# Ensure the backend package is importable regardless of cwd.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.app.cpp_bridge import (
    CppBridge,
    CppBridgeDeadError,
    CppBridgeError,
    CppBridgeProtocolError,
    CppBridgeTimeoutError,
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _write_fake_exe(code: str) -> Path:
    """Write a tiny Python script that mimics the C++ JSON-Lines protocol.

    Returns the path to the temporary ``.py`` file.
    """
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(textwrap.dedent(code))
    return Path(path)


def _bridge_for_script(script_path: Path, **kwargs) -> CppBridge:
    """Create a CppBridge whose exe_path invokes the given Python script."""
    # We cannot directly set exe_path to a .py file because CppBridge runs it
    # as a subprocess.  Instead, we use the Python interpreter + script path.
    # To achieve this we subclass CppBridge and override start().
    bridge = _ScriptBridge(script_path, **kwargs)
    return bridge


class _ScriptBridge(CppBridge):
    """A CppBridge variant that spawns ``python <script>`` instead of an exe."""

    def __init__(self, script: Path, **kwargs):
        # Pass a dummy exe_path that we never actually validate.
        super().__init__(exe_path=script, **kwargs)
        self._script = script

    def start(self) -> None:
        import queue as _queue

        if self._proc is not None and self._proc.poll() is None:
            return

        if not self._script.exists():
            raise CppBridgeError(f"Script not found: {self._script}")

        self._dead = False
        self._line_queue = _queue.Queue()

        self._proc = subprocess.Popen(
            [sys.executable, str(self._script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._reader_thread = threading.Thread(
            target=self._stdout_reader, daemon=True, name="test-stdout"
        )
        self._reader_thread.start()

        self._stderr_thread = threading.Thread(
            target=self._stderr_drainer, daemon=True, name="test-stderr"
        )
        self._stderr_thread.start()


# ─── Tests ─────────────────────────────────────────────────────────────────────


class TestMultipleRoundTrips(unittest.TestCase):
    """1. Successful multiple-command round trips through one persistent process."""

    def test_echo_multiple_commands(self):
        script = _write_fake_exe("""\
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                cmd = req.get("cmd", "")
                if cmd == "shutdown":
                    print(json.dumps({"ok": True, "msg": "shutdown"}), flush=True)
                    break
                # Echo back with ok: true
                print(json.dumps({"ok": True, "echo": req}), flush=True)
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()

            r1 = bridge.send({"cmd": "init", "berths": 2, "cranes": 3, "trucks": 4})
            self.assertTrue(r1["ok"])
            self.assertEqual(r1["echo"]["cmd"], "init")

            r2 = bridge.send({"cmd": "snapshot"})
            self.assertTrue(r2["ok"])
            self.assertEqual(r2["echo"]["cmd"], "snapshot")

            r3 = bridge.send({"cmd": "submit_job", "job_id": "J1"})
            self.assertTrue(r3["ok"])

            bridge.shutdown()
            self.assertFalse(bridge.is_alive)
        finally:
            os.unlink(script)


class TestOkFalseReturned(unittest.TestCase):
    """2. C++-style ok:false response is returned (not raised as exception)."""

    def test_ok_false_is_returned(self):
        script = _write_fake_exe("""\
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                if req.get("cmd") == "shutdown":
                    print(json.dumps({"ok": True, "msg": "shutdown"}), flush=True)
                    break
                print(json.dumps({"ok": False, "error": "dispatcher not initialised"}), flush=True)
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()

            resp = bridge.send({"cmd": "submit_job", "job_id": "J1"})
            self.assertFalse(resp["ok"])
            self.assertIn("error", resp)

            bridge.shutdown()
        finally:
            os.unlink(script)


class TestMissingExecutable(unittest.TestCase):
    """3. Missing executable produces a clear startup error."""

    def test_missing_exe_raises(self):
        bridge = CppBridge(exe_path="/nonexistent/path/to/port_json_iface.exe")
        with self.assertRaises(CppBridgeError) as ctx:
            bridge.start()
        self.assertIn("not found", str(ctx.exception))


class TestMalformedJson(unittest.TestCase):
    """4. Malformed JSON response raises CppBridgeProtocolError."""

    def test_malformed_json(self):
        script = _write_fake_exe("""\
            import sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                # Return garbage
                print("NOT VALID JSON {{{", flush=True)
                break
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()

            with self.assertRaises(CppBridgeProtocolError) as ctx:
                bridge.send({"cmd": "snapshot"})
            self.assertIn("Malformed JSON", str(ctx.exception))
            self.assertFalse(bridge.is_alive)
            bridge.shutdown()
        finally:
            os.unlink(script)


class TestNonObjectJson(unittest.TestCase):
    """5. Non-object JSON response raises CppBridgeProtocolError."""

    def test_json_array_raises(self):
        script = _write_fake_exe("""\
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                # Return a valid JSON array instead of an object
                print(json.dumps([1, 2, 3]), flush=True)
                break
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()

            with self.assertRaises(CppBridgeProtocolError) as ctx:
                bridge.send({"cmd": "snapshot"})
            self.assertIn("Expected JSON object", str(ctx.exception))
            self.assertFalse(bridge.is_alive)
            bridge.shutdown()
        finally:
            os.unlink(script)


class TestTimeout(unittest.TestCase):
    """6. Response timeout raises CppBridgeTimeoutError and marks bridge unusable."""

    def test_timeout_marks_dead(self):
        script = _write_fake_exe("""\
            import sys, time
            # Read the command but never respond
            line = sys.stdin.readline()
            time.sleep(30)
        """)
        try:
            bridge = _bridge_for_script(script, timeout=0.5)
            bridge.start()

            with self.assertRaises(CppBridgeTimeoutError):
                bridge.send({"cmd": "snapshot"})

            self.assertFalse(bridge.is_alive)

            # Subsequent sends must also fail.
            with self.assertRaises(CppBridgeDeadError):
                bridge.send({"cmd": "snapshot"})

            bridge.shutdown()
        finally:
            os.unlink(script)


class TestUnexpectedExit(unittest.TestCase):
    """7. Unexpected child-process exit raises CppBridgeDeadError."""

    def test_exit_before_response(self):
        script = _write_fake_exe("""\
            import sys
            # Read one line then exit immediately without responding
            sys.stdin.readline()
            sys.exit(42)
        """)
        try:
            bridge = _bridge_for_script(script, timeout=2.0)
            bridge.start()

            with self.assertRaises(CppBridgeDeadError):
                bridge.send({"cmd": "snapshot"})

            self.assertFalse(bridge.is_alive)
            bridge.shutdown()
        finally:
            os.unlink(script)


class TestConcurrentSerialization(unittest.TestCase):
    """8. Concurrent callers are serialised correctly."""

    def test_concurrent_sends(self):
        # Fake process echoes back 'seq' value so we can verify ordering.
        script = _write_fake_exe("""\
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                if req.get("cmd") == "shutdown":
                    print(json.dumps({"ok": True, "msg": "shutdown"}), flush=True)
                    break
                # Echo the seq field back
                print(json.dumps({"ok": True, "seq": req.get("seq")}), flush=True)
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()

            results: dict[int, dict] = {}
            errors: list[Exception] = []

            def send_one(seq: int):
                try:
                    resp = bridge.send({"cmd": "ping", "seq": seq})
                    results[seq] = resp
                except Exception as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=send_one, args=(i,)) for i in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(len(errors), 0, f"Errors: {errors}")
            self.assertEqual(len(results), 10)

            # Every response should have the correct seq echoed back.
            for seq, resp in results.items():
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["seq"], seq)

            bridge.shutdown()
        finally:
            os.unlink(script)


class TestShutdownIdempotent(unittest.TestCase):
    """9. Shutdown is clean and idempotent."""

    def test_double_shutdown(self):
        script = _write_fake_exe("""\
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                req = json.loads(line)
                if req.get("cmd") == "shutdown":
                    print(json.dumps({"ok": True, "msg": "shutdown"}), flush=True)
                    break
                print(json.dumps({"ok": True}), flush=True)
        """)
        try:
            bridge = _bridge_for_script(script)
            bridge.start()
            self.assertTrue(bridge.is_alive)

            bridge.shutdown()
            self.assertFalse(bridge.is_alive)

            # Second shutdown must not raise.
            bridge.shutdown()
        finally:
            os.unlink(script)

    def test_shutdown_without_start(self):
        """Calling shutdown on an un-started bridge is a no-op."""
        bridge = CppBridge(exe_path="dummy")
        bridge.shutdown()  # should not raise


class TestIsAliveProperty(unittest.TestCase):
    """Additional: is_alive reflects process state correctly."""

    def test_not_alive_before_start(self):
        bridge = CppBridge(exe_path="dummy")
        self.assertFalse(bridge.is_alive)


if __name__ == "__main__":
    unittest.main()
