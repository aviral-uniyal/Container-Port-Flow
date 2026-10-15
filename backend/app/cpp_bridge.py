"""
CppBridge – thin, thread-safe Python wrapper around the persistent
C++ JSON-Lines child process (``port_json_iface.exe``).

The child process owns an in-memory ``PortDispatcher``; it must stay alive
across many commands.  This module handles subprocess lifecycle, serialisation,
timeout enforcement, and concurrency safety.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from .config import get_cpp_exe_path

# ─── Custom exceptions ────────────────────────────────────────────────────────


class CppBridgeError(Exception):
    """Base exception for all CppBridge transport-level failures."""


class CppBridgeTimeoutError(CppBridgeError):
    """Raised when the C++ process does not respond within the timeout."""


class CppBridgeDeadError(CppBridgeError):
    """Raised when the C++ process has terminated unexpectedly."""


class CppBridgeProtocolError(CppBridgeError):
    """Raised when the C++ process returns malformed or non-object JSON."""


# ─── CppBridge ─────────────────────────────────────────────────────────────────


class CppBridge:
    """Manage a long-lived C++ child process speaking JSON Lines over stdio.

    Parameters
    ----------
    exe_path:
        Explicit path to the executable.  When *None*, resolved via
        :func:`~backend.app.config.get_cpp_exe_path`.
    timeout:
        Maximum seconds to wait for a response from the child process.
    """

    def __init__(
        self,
        exe_path: str | Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._exe_path: Path = Path(exe_path) if exe_path else get_cpp_exe_path()
        self._timeout: float = timeout

        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()       # serialises send/receive pairs
        self._dead = False                  # sticky flag – once dead, unusable

        # Stdout reader thread and its queue.
        self._reader_thread: threading.Thread | None = None
        self._line_queue: queue.Queue[str | None] = queue.Queue()

        # Stderr drainer thread (prevents child blocking on full pipe).
        self._stderr_thread: threading.Thread | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the C++ child process.

        Raises
        ------
        CppBridgeError
            If the executable does not exist or cannot be started.
        """
        if self._proc is not None and self._proc.poll() is None:
            return  # already running

        if not self._exe_path.exists():
            raise CppBridgeError(
                f"C++ executable not found: {self._exe_path}"
            )

        self._dead = False
        self._line_queue = queue.Queue()

        try:
            self._proc = subprocess.Popen(
                [str(self._exe_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )
        except OSError as exc:
            raise CppBridgeError(
                f"Failed to start C++ process ({self._exe_path}): {exc}"
            ) from exc

        # Spawn a daemon thread that reads stdout lines into a queue.
        self._reader_thread = threading.Thread(
            target=self._stdout_reader,
            daemon=True,
            name="cpp-bridge-stdout",
        )
        self._reader_thread.start()

        # Spawn a daemon thread that drains stderr so the child never blocks.
        self._stderr_thread = threading.Thread(
            target=self._stderr_drainer,
            daemon=True,
            name="cpp-bridge-stderr",
        )
        self._stderr_thread.start()

    @property
    def is_alive(self) -> bool:
        """True when the child process is running and the bridge is usable."""
        if self._dead:
            return False
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def send(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a command and return the parsed JSON response.

        Parameters
        ----------
        command:
            A JSON-serialisable dict.  Must contain a ``"cmd"`` key by C++
            protocol convention, though this wrapper does not enforce it.

        Returns
        -------
        dict
            The parsed JSON object returned by the child process.

        Raises
        ------
        CppBridgeDeadError
            If the process has already terminated.
        CppBridgeTimeoutError
            If no response arrives within the configured timeout.
        CppBridgeProtocolError
            If the response is not valid JSON or not a JSON object.
        """
        cmd_name = command.get("cmd", "<unknown>")

        with self._lock:
            self._assert_alive(cmd_name)

            line_out = json.dumps(command, separators=(",", ":")) + "\n"
            try:
                assert self._proc is not None  # for type checker
                assert self._proc.stdin is not None
                self._proc.stdin.write(line_out)
                self._proc.stdin.flush()
            except OSError as exc:
                self._mark_dead()
                raise CppBridgeDeadError(
                    f"Failed to write command '{cmd_name}' to C++ process: {exc}"
                ) from exc

            # Wait for a response line from the reader thread.
            try:
                line_in = self._line_queue.get(timeout=self._timeout)
            except queue.Empty:
                self._mark_dead()
                raise CppBridgeTimeoutError(
                    f"C++ process did not respond to '{cmd_name}' "
                    f"within {self._timeout}s"
                )

            if line_in is None:
                # Reader thread signalled EOF / process death.
                self._mark_dead()
                raise CppBridgeDeadError(
                    f"C++ process terminated while waiting for response "
                    f"to '{cmd_name}'"
                )

            return self._parse_response(line_in, cmd_name)

    def shutdown(self) -> None:
        """Gracefully shut down the C++ process.

        Sends ``{"cmd":"shutdown"}``, consumes the final response, and waits
        up to 3 seconds for exit.  Falls back to ``kill()`` if needed.

        Safe to call more than once or when the process is already dead.
        """
        if self._proc is None:
            return

        # If still alive, attempt graceful shutdown.
        if not self._dead and self._proc.poll() is None:
            try:
                self.send({"cmd": "shutdown"})
            except CppBridgeError:
                pass  # best-effort; we will kill below if needed

        # Wait for exit, then force-kill if necessary.
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=3)

        self._dead = True

        # Join reader threads so they don't leak.
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)

        # Close pipes explicitly to avoid ResourceWarning.
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

        self._proc = None

    # ── internal helpers ───────────────────────────────────────────────────

    def _assert_alive(self, cmd_name: str) -> None:
        """Raise if the bridge is dead or the process has exited."""
        if self._dead:
            raise CppBridgeDeadError(
                f"Bridge is permanently unusable (command: '{cmd_name}')"
            )
        if self._proc is None:
            raise CppBridgeDeadError(
                f"Bridge not started (command: '{cmd_name}')"
            )
        if self._proc.poll() is not None:
            self._mark_dead()
            raise CppBridgeDeadError(
                f"C++ process exited unexpectedly with code "
                f"{self._proc.returncode} (command: '{cmd_name}')"
            )

    def _mark_dead(self) -> None:
        """Set the sticky dead flag so all future calls fail immediately."""
        self._dead = True

    def _parse_response(self, raw: str, cmd_name: str) -> dict[str, Any]:
        """Decode a JSON Lines response, raising on protocol errors."""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._mark_dead()
            raise CppBridgeProtocolError(
                f"Malformed JSON from C++ (command '{cmd_name}'): {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            self._mark_dead()
            raise CppBridgeProtocolError(
                f"Expected JSON object from C++ (command '{cmd_name}'), "
                f"got {type(parsed).__name__}: {raw!r}"
            )

        # Application-level ok:false is *not* a transport error.
        return parsed

    # ── background threads ─────────────────────────────────────────────────

    def _stdout_reader(self) -> None:
        """Read lines from child stdout and put them in the queue.

        Runs in a daemon thread.  Puts ``None`` on EOF to signal death.
        """
        try:
            assert self._proc is not None
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                stripped = line.rstrip("\n").rstrip("\r")
                if stripped:
                    self._line_queue.put(stripped)
            # EOF reached – child closed stdout.
        except (OSError, ValueError):
            pass  # pipe closed or process dead
        finally:
            self._line_queue.put(None)  # sentinel for EOF

    def _stderr_drainer(self) -> None:
        """Read and discard stderr so the child never blocks on a full pipe.

        Runs in a daemon thread.  Stderr content is intentionally discarded;
        it contains diagnostics that must not corrupt the JSON protocol.
        """
        try:
            assert self._proc is not None
            assert self._proc.stderr is not None
            for _ in self._proc.stderr:
                pass  # discard
        except (OSError, ValueError):
            pass
