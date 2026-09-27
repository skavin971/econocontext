from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from ..config import Config
from ..contracts import (
    CancelView,
    HealthView,
    MetricsView,
    RunAccepted,
    RunRequest,
    RunView,
    TracePage,
)
from ..runtime.manager import Manager


def create_app(config=None, backend=None):
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app):
        from agents import build

        app.state.manager = await Manager(config, backend, build).start()
        try:
            yield
        finally:
            await app.state.manager.close()

    app = FastAPI(title="EconoContext", version="0.1.0", lifespan=lifespan)

    async def get_run(run_id):
        try:
            return await app.state.manager.memory.run(run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @app.post("/runs", status_code=202, response_model=RunAccepted)
    async def submit(request: RunRequest):
        try:
            run = await app.state.manager.submit(request)
        except ValueError as exc:
            raise HTTPException(409 if "Idempotency" in str(exc) else 422, str(exc)) from exc
        return {"run_id": run["id"], "status": run["status"]}

    @app.get("/runs/{run_id}", response_model=RunView)
    async def status(run_id: str):
        return await get_run(run_id)

    @app.get("/runs/{run_id}/trace", response_model=TracePage)
    async def trace(
        run_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
    ):
        await get_run(run_id)
        events = await app.state.manager.memory.events(run_id, after, limit)
        return {"events": events, "next_cursor": events[-1]["seq"] if events else after}

    @app.get("/runs/{run_id}/metrics", response_model=MetricsView)
    async def metrics(run_id: str):
        await get_run(run_id)
        return await app.state.manager.telemetry.metrics(run_id)

    @app.post("/runs/{run_id}/cancel", response_model=CancelView)
    async def cancel(run_id: str):
        await get_run(run_id)
        return await app.state.manager.cancel(run_id)

    @app.get("/health", response_model=HealthView)
    async def health():
        await app.state.manager.memory.query("SELECT 1")
        return {"status": "ok", "storage": "sqlite", "backend": config.backend}

    return app
