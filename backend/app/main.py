"""
FastAPI application entry-point for the Container-Port-Flow backend.

Lifespan management:
    The CppBridge is started *once* at application startup and shut down
    cleanly when the server stops.  It is stored on ``app.state.bridge``
    so that router handlers (and tests) can access or replace it.

Run locally:
    uvicorn backend.app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .cpp_bridge import CppBridge, CppBridgeError
from .router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the C++ bridge on startup; shut it down on shutdown."""
    bridge = CppBridge()
    try:
        bridge.start()
    except CppBridgeError:
        # Bridge failed to start — store None so /health reports degraded.
        bridge = None  # type: ignore[assignment]

    app.state.bridge = bridge
    yield

    if bridge is not None:
        bridge.shutdown()


app = FastAPI(
    title="Container-Port-Flow API",
    description="Thin HTTP layer over the C++ port-simulation engine.",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(router)
