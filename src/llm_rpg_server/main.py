from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from llm_rpg_server.api.routers import (
    exploration_router,
    game_router,
    npc_router,
    room_router,
    websocket_router,
)
from llm_rpg_server.bootstrap import AppContainer, build_container
from llm_rpg_server.shared.config import LocalContentProvider, Settings


def create_app(container: AppContainer | None = None) -> FastAPI:
    settings = container.settings if container else Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build_container()
        yield
        app.state.container.observability.flush()

    content = container.content if container else LocalContentProvider(settings.content_root)
    title = content.text("api.title")
    app = FastAPI(title=title, version="3.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(game_router)
    app.include_router(exploration_router)
    app.include_router(npc_router)
    app.include_router(room_router)
    app.include_router(websocket_router)

    @app.exception_handler(KeyError)
    async def key_error(request: Request, exc: KeyError):
        message = request.app.state.container.content.text("errors.resource_not_found")
        return JSONResponse(status_code=404, content={"detail": message})

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def runtime_error(request: Request, exc: RuntimeError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    return app


app = create_app()
app_api = app


def run() -> None:
    import uvicorn

    uvicorn.run("llm_rpg_server.main:app", host="0.0.0.0", port=8008, reload=False)


if __name__ == "__main__":
    run()
