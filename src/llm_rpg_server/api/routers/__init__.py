from .exploration import router as exploration_router
from .game import router as game_router
from .npcs import router as npc_router
from .rooms import router as room_router
from .websocket import router as websocket_router

__all__ = ["exploration_router", "game_router", "npc_router", "room_router", "websocket_router"]

