"""
FastAPI application entry-point for the Container-Port-Flow backend.

Lifespan management:
    The CppBridge and DatabasePool are started *once* at application startup
    and shut down cleanly when the server stops.  They are stored on
    ``app.state.bridge`` and ``app.state.db_pool`` so that router handlers
    (and tests) can access or replace them.

Run locally:
    uvicorn backend.app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .cpp_bridge import CppBridge, CppBridgeError
from .database import DatabasePool
from .router import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the C++ bridge and DB pool on startup; shut them down on shutdown."""

    # ── C++ bridge ────────────────────────────────────────────────────────
    bridge = CppBridge()
    try:
        bridge.start()
    except CppBridgeError:
        # Bridge failed to start — store None so /health reports degraded.
        bridge = None  # type: ignore[assignment]
    app.state.bridge = bridge

    # ── Database pool ─────────────────────────────────────────────────────
    db_pool = DatabasePool()
    try:
        db_pool.open()
    except Exception as exc:
        logger.warning("Database pool failed to open: %s", exc)
        db_pool = None  # type: ignore[assignment]
    app.state.db_pool = db_pool

    try:
        yield
    finally:
        # ── Shutdown ──────────────────────────────────────────────────────
        if db_pool is not None:
            try:
                db_pool.close()
            except Exception as exc:
                logger.error("Failed to close database pool: %s", exc)

        if bridge is not None:
            try:
                bridge.shutdown()
            except Exception as exc:
                logger.error("Failed to shut down C++ bridge: %s", exc)


app = FastAPI(
    title="Container-Port-Flow API",
    description="Thin HTTP layer over the C++ port-simulation engine.",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(router)
