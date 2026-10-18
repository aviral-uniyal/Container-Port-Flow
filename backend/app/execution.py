"""One asyncio runner for deterministic C++ simulation steps."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SimulationPersistenceError(RuntimeError):
    """The C++ step succeeded but its snapshot could not be persisted."""


class SimulationExecution:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge
        self.lock = asyncio.Lock()
        self.transition_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.stop = asyncio.Event()
        self.state = "not_running"
        self.error: str | None = None
        self.interval = 1.0
        self.on_snapshot = None
        self._run_requested = False
        self._recoverable_block = False

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "interval_seconds": self.interval,
                "error": self.error}

    @staticmethod
    def _send(bridge: Any, command: dict[str, Any]) -> dict[str, Any]:
        response = bridge.send(command)
        if not response.get("ok", False):
            raise RuntimeError(response.get("error", "C++ command rejected"))
        return response

    async def step(self, continue_batch: bool = False) -> dict[str, Any]:
        async with self.lock:
            if self.state == "running" and not continue_batch:
                raise RuntimeError("Pause automatic execution before stepping manually")
            result = await asyncio.to_thread(
                self._send, self.bridge,
                {"cmd": "step", "continue_batch": continue_batch},
            )
            if self.on_snapshot is not None:
                snapshot = await asyncio.to_thread(self._send, self.bridge, {"cmd": "snapshot"})
                try:
                    await asyncio.to_thread(self.on_snapshot, snapshot)
                except Exception as exc:
                    raise SimulationPersistenceError(str(exc)) from exc
            return result

    async def run(self, interval: float) -> dict[str, Any]:
        async with self.transition_lock:
            previous_task = self.task
            if (self.state != "running" and previous_task is not None
                    and not previous_task.done()):
                await previous_task
            async with self.lock:
                if self.state == "running":
                    return self.status()
                self.interval = interval
                self.error = None
                self._run_requested = True
                self._recoverable_block = False
                self.stop = asyncio.Event()
                self.state = "running"
                self.task = asyncio.create_task(self._loop(), name="simulation-runner")
                return self.status()

    async def pause(self) -> dict[str, Any]:
        async with self.transition_lock:
            self._run_requested = False
            self._recoverable_block = False
            await self._stop_runner()
            return self.status()

    async def resume_after_recovery(self, can_progress: bool) -> dict[str, Any]:
        """Restart only a recoverably blocked runner whose Run intent remains set."""
        async with self.transition_lock:
            previous_task = self.task
            if (self.state == "blocked" and previous_task is not None
                    and not previous_task.done()):
                await previous_task
            async with self.lock:
                should_resume = (
                    can_progress
                    and self.state == "blocked"
                    and self._run_requested
                    and self._recoverable_block
                    and self.error is None
                )
                if not should_resume:
                    return self.status()
                self.stop = asyncio.Event()
                self.state = "running"
                self._recoverable_block = False
                self.task = asyncio.create_task(self._loop(), name="simulation-runner")
                return self.status()

    async def reset(self, command: dict[str, Any], before_reset=None,
                    capture_snapshot=False) -> dict[str, Any]:
        async with self.transition_lock:
            self._run_requested = False
            self._recoverable_block = False
            await self._stop_runner()
            async with self.lock:
                if before_reset is not None:
                    needs_snapshot = (capture_snapshot() if callable(capture_snapshot)
                                      else capture_snapshot)
                    snapshot = (await asyncio.to_thread(self._send, self.bridge, {"cmd": "snapshot"})
                                if needs_snapshot else None)
                    await asyncio.to_thread(before_reset, snapshot)
                response = await asyncio.to_thread(self._send, self.bridge, command)
                self.state = "not_running"
                self.error = None
                return response

    async def _stop_runner(self) -> None:
        # An in-flight C++ call must finish before pause/reset returns.
        self.stop.set()
        task = self.task
        if task is not None and task is not asyncio.current_task():
            await task
        async with self.lock:
            if self.state != "completed":
                self.state = "paused"

    async def _loop(self) -> None:
        try:
            while not self.stop.is_set():
                async with self.lock:
                    if self.stop.is_set():
                        break
                    result = await asyncio.to_thread(
                        self._send, self.bridge,
                        {"cmd": "step", "continue_batch": True},
                    )
                    snapshot = await asyncio.to_thread(
                        self._send, self.bridge, {"cmd": "snapshot"},
                    )
                    if self.on_snapshot is not None:
                        try:
                            await asyncio.to_thread(self.on_snapshot, snapshot)
                        except Exception as exc:
                            raise SimulationPersistenceError(str(exc)) from exc
                    jobs = snapshot.get("jobs", [])
                    unfinished = any(
                        job.get("state") in ("PENDING", "ACTIVE", "INTERRUPTED")
                        for job in jobs
                    )
                    if not unfinished:
                        self.state = "completed"
                        self._run_requested = False
                        self._recoverable_block = False
                        break
                    if not result.get("advanced_jobs") and not result.get("dispatched_jobs"):
                        self.state = "blocked"
                        self.error = None
                        self._recoverable_block = True
                        break
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
        except SimulationPersistenceError as exc:
            logger.error("Simulation persistence failed: %s", exc)
            self.error = f"Database persistence failed; reconciliation required: {exc}"
            self.state = "blocked"
            self._run_requested = False
            self._recoverable_block = False
        except Exception as exc:
            logger.error("Simulation runner failed: %s", exc)
            self.error = str(exc)
            self.state = "blocked"
            self._run_requested = False
            self._recoverable_block = False
        finally:
            if self.state == "running":
                self.state = "paused"
