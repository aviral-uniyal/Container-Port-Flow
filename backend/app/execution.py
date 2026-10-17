"""One asyncio runner for deterministic C++ simulation steps."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SimulationExecution:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge
        self.lock = asyncio.Lock()
        self.transition_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.stop = asyncio.Event()
        self.state = "paused"
        self.error: str | None = None
        self.interval = 1.0

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
            return await asyncio.to_thread(
                self._send, self.bridge,
                {"cmd": "step", "continue_batch": continue_batch},
            )

    async def run(self, interval: float) -> dict[str, Any]:
        async with self.transition_lock:
            async with self.lock:
                if self.state == "running":
                    return self.status()
                self.interval = interval
                self.error = None
                self.stop = asyncio.Event()
                self.state = "running"
                self.task = asyncio.create_task(self._loop(), name="simulation-runner")
                return self.status()

    async def pause(self) -> dict[str, Any]:
        async with self.transition_lock:
            await self._stop_runner()
            return self.status()

    async def reset(self, command: dict[str, Any]) -> dict[str, Any]:
        async with self.transition_lock:
            await self._stop_runner()
            async with self.lock:
                response = await asyncio.to_thread(self._send, self.bridge, command)
                self.state = "paused"
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
                    jobs = snapshot.get("jobs", [])
                    unfinished = any(
                        job.get("state") in ("PENDING", "ACTIVE", "INTERRUPTED")
                        for job in jobs
                    )
                    if not unfinished:
                        self.state = "completed"
                        break
                    if not result.get("advanced_jobs") and not result.get("dispatched_jobs"):
                        self.state = "blocked"
                        break
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            logger.error("Simulation runner failed: %s", exc)
            self.error = str(exc)
            self.state = "blocked"
        finally:
            if self.state == "running":
                self.state = "paused"
