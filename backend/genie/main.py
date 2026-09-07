"""FastAPI application. Serves /api/* and, in production, the built frontend from frontend/dist."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import all_routers
from .db import init_db

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    # Runs left `running` by a crash become `paused` so the UI can offer Resume.
    from .jobs.runner import runner

    runner.mark_interrupted()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Dataset Genie", version=__version__, lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    for router in all_routers():
        app.include_router(router)

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        dist_root = FRONTEND_DIST.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            # Serve a real file only if it resolves inside dist; anything else gets the SPA shell.
            if full_path:
                candidate = (dist_root / full_path).resolve()
                if candidate.is_relative_to(dist_root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(dist_root / "index.html")

    return app


app = create_app()
