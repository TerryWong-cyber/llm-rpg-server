from fastapi import Request, WebSocket

from llm_rpg_server.bootstrap import AppContainer


def container_from_request(request: Request) -> AppContainer:
    return request.app.state.container


def container_from_websocket(websocket: WebSocket) -> AppContainer:
    return websocket.app.state.container

