"""FastMCP instance and FastAPI mount at /mcp."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import McpAuthMiddleware

mcp = FastMCP(
    "Dataset Genie",
    instructions="Configure Dataset Genie and run its eight-stage dataset pipeline.",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)


class McpBarePathMiddleware:
    """Internally normalize the public /mcp endpoint without an HTTP redirect."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


def mcp_asgi():
    """Fresh transport app whose MCP endpoint is at the mount root."""
    if hasattr(mcp, "http_app"):
        return mcp.http_app(path="/")

    # MCP 1.30 session managers are single-use. Keep the shared FastMCP registry,
    # but give every Dataset Genie app its own transport lifecycle.
    mcp._session_manager = None
    app = mcp.streamable_http_app()
    manager = mcp.session_manager
    app.router.lifespan_context = lambda _: manager.run()
    return app


def mount_mcp(app: FastAPI) -> None:
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Mount

    from .tools import inspect, projects, runs, setup

    inspect.register()
    projects.register()
    runs.register()
    setup.register()
    inner = mcp_asgi()
    app.state.mcp_asgi = inner
    wrapped = Starlette(
        routes=[Mount("/", app=inner)],
        middleware=[Middleware(McpAuthMiddleware)],
    )
    app.mount("/mcp", wrapped)
    app.add_middleware(McpBarePathMiddleware)


def combined_lifespan(app_lifespan):
    @asynccontextmanager
    async def _life(app: FastAPI) -> AsyncIterator[None]:
        mcp_app = app.state.mcp_asgi
        mcp_life = getattr(mcp_app, "lifespan", None)
        if mcp_life is None:
            mcp_life = getattr(mcp_app.router, "lifespan_context", None)
        async with app_lifespan(app):
            if mcp_life is None:
                yield
                return
            async with mcp_life(app):
                yield

    return _life
